"""Атомарность записи снимков и корректность формата.

Снимок читают параллельно с записью: браузер опрашивает раз в секунду, а
воркер пишет по своему таймеру. Читатель обязан увидеть либо целиком старый
снимок, либо целиком новый, но никогда не половину.
"""

import json
import os
import threading
from pathlib import Path

import pytest

from worker.snapshot import snapshot_path, write_atomic, write_index


def test_writes_and_reads_back(tmp_path):
    target = tmp_path / "snapshot.json"
    write_atomic(target, {"schema": 1, "room": "муха"})
    assert json.loads(target.read_text(encoding="utf-8"))["room"] == "муха"


def test_creates_missing_directories(tmp_path):
    target = tmp_path / "rooms" / "obyknovennaya" / "snapshot.json"
    write_atomic(target, {"ok": True})
    assert target.exists()


def test_replaces_existing_file(tmp_path):
    target = tmp_path / "snapshot.json"
    write_atomic(target, {"sequence": 1})
    write_atomic(target, {"sequence": 2})
    assert json.loads(target.read_text())["sequence"] == 2


def test_leaves_no_temporary_files(tmp_path):
    target = tmp_path / "snapshot.json"
    for i in range(20):
        write_atomic(target, {"sequence": i})
    assert [p.name for p in tmp_path.iterdir()] == ["snapshot.json"]


def test_temporary_file_is_cleaned_up_on_failure(tmp_path):
    target = tmp_path / "snapshot.json"

    class Unserializable:
        pass

    with pytest.raises(TypeError):
        write_atomic(target, {"bad": Unserializable()})
    assert not target.exists()
    assert list(tmp_path.iterdir()) == [], "временный файл остался после сбоя"


def test_reader_never_sees_a_partial_file(tmp_path):
    """Главная проверка: пишем в цикле, читаем в цикле, рвани быть не должно.

    Полуфайл проявился бы как ошибка разбора JSON. Полезная нагрузка большая,
    чтобы запись заведомо не уложилась в один блок.
    """
    target = tmp_path / "snapshot.json"
    write_atomic(target, {"sequence": 0, "filler": ["x" * 64] * 2000})
    failures = []
    stop = threading.Event()

    def writer():
        for i in range(1, 200):
            write_atomic(target, {"sequence": i, "filler": ["y" * 64] * 2000})
        stop.set()

    def reader():
        while not stop.is_set():
            try:
                payload = json.loads(target.read_text(encoding="utf-8"))
                assert isinstance(payload["sequence"], int)
            except Exception as e:
                failures.append(repr(e))
                return

    threads = [threading.Thread(target=writer)] + [
        threading.Thread(target=reader) for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not failures, f"читатель увидел незавершённую запись: {failures[:3]}"


def test_sequence_never_goes_backwards(tmp_path):
    """Читатель должен видеть версии по возрастанию, без откатов."""
    target = tmp_path / "snapshot.json"
    write_atomic(target, {"sequence": 0})
    seen = []
    stop = threading.Event()

    def writer():
        for i in range(1, 150):
            write_atomic(target, {"sequence": i})
        stop.set()

    def reader():
        while not stop.is_set():
            seen.append(json.loads(target.read_text())["sequence"])

    w, r = threading.Thread(target=writer), threading.Thread(target=reader)
    w.start()
    r.start()
    w.join(timeout=60)
    r.join(timeout=60)
    assert seen == sorted(seen), "последовательность снимков откатывалась назад"


def test_temporary_file_stays_in_the_target_directory(tmp_path, monkeypatch):
    """Rename атомарен только внутри одной файловой системы."""
    seen = {}
    real = os.replace

    def spy(src, dst):
        seen["src_dir"] = Path(src).parent
        seen["dst_dir"] = Path(dst).parent
        return real(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    target = tmp_path / "rooms" / "x" / "snapshot.json"
    write_atomic(target, {"ok": True})
    assert seen["src_dir"] == seen["dst_dir"] == target.parent


def test_snapshot_path_always_carries_the_room_id():
    path = snapshot_path("snapshots", "obyknovennaya")
    assert path.as_posix() == "snapshots/rooms/obyknovennaya/snapshot.json"


def test_index_lists_rooms_with_api_paths(tmp_path):
    rooms = [
        {"id": "obyknovennaya", "title": "Муха обыкновенная"},
        {"id": "kinomanka", "title": "Киноманка"},
    ]
    write_index(tmp_path, rooms, {"release": "MaleCNS v1.0"})
    payload = json.loads((tmp_path / "rooms.json").read_text(encoding="utf-8"))
    assert [r["id"] for r in payload["rooms"]] == ["obyknovennaya", "kinomanka"]
    assert payload["rooms"][0]["snapshot"] == "/api/rooms/obyknovennaya/snapshot.json"
    # Дисклеймер едет вместе с данными, а не только на странице.
    assert "Живая муха не участвует" in payload["disclaimer"]
    assert "Токена нет" in payload["disclaimer"]
