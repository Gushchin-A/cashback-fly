"""Воркер: число комнат из конфига, цикл, сохранение и восстановление.

Требуют собранный коннектом:
    FLY_FULL_TEST=1 .venv/bin/python -m pytest tests/test_worker.py -q
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("FLY_FULL_TEST") != "1",
    reason="нужен собранный коннектом; включается FLY_FULL_TEST=1",
)

ROOT = Path(__file__).resolve().parent.parent


def run_worker(args, timeout=240):
    env = {**os.environ, "OPENBLAS_NUM_THREADS": "1"}
    return subprocess.run(
        [sys.executable, "-m", "worker.main", *args],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout,
    )


@pytest.fixture
def workdir(tmp_path):
    return {"snapshots": str(tmp_path / "snap"), "state": str(tmp_path / "state")}


def test_worker_completes_a_cycle_and_writes_a_snapshot(workdir):
    result = run_worker(
        ["--once", "--snapshots", workdir["snapshots"], "--state", workdir["state"]]
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert "выбрано:" in result.stdout

    index = json.loads((Path(workdir["snapshots"]) / "rooms.json").read_text("utf-8"))
    assert index["rooms"], "индекс комнат пуст"
    room_id = index["rooms"][0]["id"]
    # Путь несёт идентификатор комнаты даже когда комната одна.
    assert index["rooms"][0]["snapshot"] == f"/api/rooms/{room_id}/snapshot.json"

    path = Path(workdir["snapshots"]) / "rooms" / room_id / "snapshot.json"
    s = json.loads(path.read_text("utf-8"))
    for key in (
        "room", "month", "offers", "selection", "activity",
        "spikes", "uptime_seconds", "timestamp", "sequence", "phase",
    ):
        assert key in s, f"в снимке нет поля {key}"
    assert s["room"]["id"] == room_id
    assert s["room"]["interests"], "интересы комнаты не попали в снимок"
    assert len(s["selection"]["selected"]) == s["selection"]["slots"]
    assert len(set(s["selection"]["selected"])) == s["selection"]["slots"]
    chosen = {o["id"] for o in s["offers"]}
    assert set(s["selection"]["selected"]) <= chosen
    assert s["spikes"]["total"] > 0
    assert s["uptime_seconds"] > 0
    assert s["activity"]["sample_counts"], "нет данных для визуализации"
    assert len(s["activity"]["sample_ids"]) == len(s["activity"]["sample_counts"])


def test_state_survives_restart_and_month_advances(workdir):
    first = run_worker(
        ["--once", "--snapshots", workdir["snapshots"], "--state", workdir["state"]]
    )
    assert first.returncode == 0, first.stderr[-2000:]
    assert Path(workdir["state"]).glob("*/brain.npz")

    second = run_worker(
        ["--once", "--snapshots", workdir["snapshots"], "--state", workdir["state"]]
    )
    assert second.returncode == 0, second.stderr[-2000:]
    assert "состояние восстановлено" in second.stdout
    # Кольцо провернулось: второй прогон закрыл уже следующий месяц.
    assert "начинаем с месяца" not in second.stdout


def test_sigterm_saves_state(workdir):
    env = {**os.environ, "OPENBLAS_NUM_THREADS": "1"}
    process = subprocess.Popen(
        [sys.executable, "-m", "worker.main",
         "--snapshots", workdir["snapshots"], "--state", workdir["state"]],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        # Ждём, пока появится первый снимок, — значит воркер уже считает.
        deadline = time.monotonic() + 180
        snapshots = Path(workdir["snapshots"]) / "rooms"
        while time.monotonic() < deadline:
            if list(snapshots.glob("*/snapshot.json")):
                break
            assert process.poll() is None, "воркер умер, не начав вещать"
            time.sleep(0.5)
        else:
            pytest.fail("снимок не появился")
        process.send_signal(signal.SIGTERM)
        out, err = process.communicate(timeout=90)
    finally:
        if process.poll() is None:
            process.kill()
    assert process.returncode == 0, err[-2000:]
    assert "Состояние сохранено" in out
    assert list(Path(workdir["state"]).glob("*/brain.npz")), "чекпойнт не записан"
    assert list(Path(workdir["state"]).glob("*/cycle.json")), "ход цикла не записан"


def test_room_count_follows_the_config(workdir, tmp_path):
    """Одна, две и три комнаты поднимаются без правок кода."""
    original = (ROOT / "config" / "rooms.json").read_text("utf-8")
    config = json.loads(original)
    try:
        for count in (2, 3):
            for i, room in enumerate(config["rooms"]):
                room["enabled"] = i < count
            (ROOT / "config" / "rooms.json").write_text(
                json.dumps(config, ensure_ascii=False, indent=2) + "\n", "utf-8"
            )
            snap = tmp_path / f"snap{count}"
            result = run_worker(
                ["--once", "--snapshots", str(snap), "--state", str(tmp_path / f"st{count}")],
                timeout=420,
            )
            assert result.returncode == 0, result.stderr[-2000:]
            written = sorted(p.parent.name for p in snap.glob("rooms/*/snapshot.json"))
            expected = sorted(r["id"] for r in config["rooms"][:count])
            assert written == expected, f"для {count} комнат получили {written}"
            index = json.loads((snap / "rooms.json").read_text("utf-8"))
            assert len(index["rooms"]) == count
    finally:
        (ROOT / "config" / "rooms.json").write_text(original, "utf-8")
