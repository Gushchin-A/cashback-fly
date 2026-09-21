"""Этап 0 — свободный ход: реальная архитектура воркера, без барьера.

Развёртка по потокам меряла синхронные пачки: все потоки стартуют и ждут
друг друга, поэтому самый медленный задаёт темп всей пачке. На M4 при пяти
потоках один неизбежно попадает на энергоэффективное ядро и тормозит всех.

В воркере комнаты независимы: у каждой свой цикл, барьера между ними нет.
Медленная комната замедляет только себя. Этот замер воспроизводит именно
такую схему и даёт цифру, на которую можно опираться в Этапе 2: сколько
нейронного времени набирает каждая комната за фиксированное окно.

Запуск:
    .venv/bin/python -m tools.bench_freerun --seconds 30
"""

import argparse
import json
import threading
import time
from pathlib import Path

import numpy as np

REPORT = Path("reports/stage0-freerun.json")
MS = 50.0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--rooms", type=int, default=5)
    p.add_argument("--seconds", type=float, default=30.0)
    a = p.parse_args()

    from neural.visual import VisualMemoryBrain

    print(f"Загружаю {a.rooms} состояний…", flush=True)
    brains = []
    for _ in range(a.rooms):
        b = VisualMemoryBrain()
        b.weights_frozen = True
        brains.append(b)

    rng = np.random.default_rng(20260920)
    zero = np.zeros(len(brains[0].retina), dtype=np.float32)
    stimuli = [
        (rng.choice(b.n, size=512, replace=False).astype(np.int32), np.float32(20.0))
        for b in brains
    ]

    def one(i):
        brains[i].step(
            zero, MS, learning=False, stimulation=stimuli[i], lamina_bias=0.0
        )

    for i in range(a.rooms):  # прогрев
        one(i)

    stop = threading.Event()
    steps = [0] * a.rooms

    def loop(i):
        while not stop.is_set():
            one(i)
            steps[i] += 1

    threads = [threading.Thread(target=loop, args=(i,)) for i in range(a.rooms)]
    started = time.perf_counter()
    for th in threads:
        th.start()
    time.sleep(a.seconds)
    stop.set()
    for th in threads:
        th.join()
    elapsed = time.perf_counter() - started

    per_room = [
        {
            "room": i,
            "steps": steps[i],
            "neural_ms": round(steps[i] * MS, 1),
            "ms_per_step": round(elapsed / steps[i] * 1000, 1),
        }
        for i in range(a.rooms)
    ]
    total_neural_ms = sum(r["neural_ms"] for r in per_room)
    result = {
        "rooms": a.rooms,
        "wall_seconds": round(elapsed, 2),
        "neural_ms_per_step": MS,
        "per_room": per_room,
        "total_neural_ms_per_wall_second": round(total_neural_ms / elapsed, 1),
        "slowest_room_neural_ms_per_wall_second": round(
            min(r["neural_ms"] for r in per_room) / elapsed, 1
        ),
        "fastest_room_neural_ms_per_wall_second": round(
            max(r["neural_ms"] for r in per_room) / elapsed, 1
        ),
    }
    for r in per_room:
        print(
            f"комната {r['room']}: {r['steps']:4d} шагов, "
            f"{r['neural_ms'] / 1000:5.2f} нейро-с за {elapsed:.1f} с "
            f"({r['ms_per_step']:6.1f} мс/шаг)",
            flush=True,
        )
    print(
        f"\nсовокупно {result['total_neural_ms_per_wall_second']:.0f} нейро-мс/с; "
        f"самая медленная комната {result['slowest_room_neural_ms_per_wall_second']:.0f} "
        f"нейро-мс на секунду реального времени",
        flush=True,
    )

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(f"Отчёт: {REPORT}", flush=True)


if __name__ == "__main__":
    main()
