"""Сборка и атомарная запись снимков комнат.

Снимок — единственный канал от воркера к зрителю. nginx отдаёт его как
статический файл, браузер опрашивает раз в секунду. Тысяча зрителей — это
тысяча чтений одного файла; воркер при этом считает ровно столько же,
сколько при одном.

Запись атомарна: сначала временный файл рядом, потом `os.replace`. Это
гарантирует, что читатель увидит либо старый снимок целиком, либо новый
целиком, но никогда — половину. Временный файл создаётся в том же каталоге,
иначе rename между файловыми системами перестанет быть атомарным.
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def write_atomic(path: Path, payload: dict) -> None:
    """Записать JSON так, чтобы читатель никогда не увидел полуфайл."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    handle, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=path.name + ".", suffix=".partial"
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            # Без fsync переживший панику ядра файл может оказаться пустым.
            os.fsync(f.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def snapshot_path(root, room_id: str) -> Path:
    """Путь снимка. Идентификатор комнаты в пути всегда, даже когда она одна."""
    return Path(root) / "rooms" / room_id / "snapshot.json"


def build(fb, state_id, room, *, phase, sequence, started_at, cycles_done):
    """Собрать снимок из показаний модуля.

    Здесь нет ни одного решения: всё, что касается выбора, берётся из
    `readout`. Задача снимка — переложить это в форму, удобную клиенту,
    и ничего не досочинить.
    """
    r = fb.readout(state_id)
    catalog = fb.categories["categories"]
    interests = fb.interests["interests"]
    month = fb.month(r.month) if r.month else None
    now = datetime.now(timezone.utc)

    offers = []
    if month:
        for o in month["offers"]:
            cid = o["category"]
            offers.append({
                "id": cid,
                "title": catalog[cid]["title"],
                "note": o.get("note"),
                "rate": o["rate"],
                "tags": catalog[cid]["tags"],
                "boosted": fb.is_boosted(state_id, cid),
                "rating": round(r.ratings.get(cid, 0.0), 4),
                "windows": r.windows.get(cid, 0),
            })

    return {
        "schema": 1,
        "room": {
            "id": room["id"],
            "title": room["title"],
            "interests": [
                {"id": i, "title": interests[i]["title"], "tags": interests[i]["tags"]}
                for i in r.interests
            ],
            "interest_tags": r.interest_tags,
        },
        "month": {"id": month["id"], "title": month["title"]} if month else None,
        "offers": offers,
        "selection": {
            "slots": fb.slots,
            "selected": r.selected,
            "top": r.top,
            "complete": r.complete,
        },
        "presenting": (
            {"id": r.presenting, "boosted": r.presenting_boosted}
            if r.presenting else None
        ),
        "phase": phase,
        "progress": {
            "windows_done": r.windows_done,
            "windows_total": r.windows_total,
            "sweep_index": r.sweep_index,
            "sweeps": fb.sweeps,
        },
        "activity": {
            "decision_hz": round(r.decision_hz, 4),
            "decision_history": [round(x, 4) for x in r.decision_history],
            "motor_hz": round(r.motor_hz, 4),
            "turn_hz": round(r.turn_hz, 4),
            # Идентификаторы нейронов — строками: 64-битные id не переживут
            # преобразование в число JavaScript.
            "sample_ids": r.sample_ids,
            "sample_types": r.sample_types,
            "sample_counts": r.sample_counts,
            # Карта ЦНС разреженно: за окно разряжается меньше десятой части
            # выборки, и слать четыре тысячи нулей каждую секунду незачем.
            # Формат — плоский [индекс, разряды, индекс, разряды, …].
            "cns_sparse": [
                v for i, c in enumerate(r.cns_counts) if c for v in (i, c)
            ],
            "cns_total": len(r.cns_counts),
        },
        "spikes": {"window": r.window_spikes, "total": r.total_spikes},
        "sim_ms": round(r.sim_ms, 3),
        "cycles_done": cycles_done,
        "uptime_seconds": round((now - started_at).total_seconds(), 1),
        "timestamp": now.isoformat(timespec="milliseconds"),
        "sequence": sequence,
    }


def write_index(root, rooms, provenance) -> None:
    """Список комнат для клиента: что существует и где лежат снимки.

    Клиент не должен знать конфиг воркера. Он читает этот файл, узнаёт число
    комнат и решает, показывать переключатель или нет.
    """
    write_atomic(
        Path(root) / "rooms.json",
        {
            "schema": 1,
            "rooms": [
                {
                    "id": r["id"],
                    "title": r["title"],
                    "snapshot": f"/api/rooms/{r['id']}/snapshot.json",
                }
                for r in rooms
            ],
            "provenance": provenance,
            "disclaimer": (
                "Проводка реальная — полный граф MaleCNS v1.0. Динамика "
                "приближённая. Движения усилены из измеренной активности. "
                "Живая муха не участвует. Токена нет."
            ),
        },
    )
