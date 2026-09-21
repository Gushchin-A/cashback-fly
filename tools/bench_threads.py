"""Этап 0 — развёртка по числу потоков: во что упирается параллельный счёт.

Пять комнат считаются в одном процессе разными потоками (ctypes отпускает GIL).
Замер показал масштабирование ×2.2–2.7 вместо ×5. Возможных причин две:
у M4 четыре производительных ядра из десяти — либо потоки уезжают на
энергоэффективные ядра, либо все состояния дерутся за пропускную способность
памяти, обходя одни и те же 200 МБ массивов post/weight.

Развёртка 1→6 потоков различает эти случаи: насыщение на четырёх означает
ограничение по P-ядрам, плавный выход на плато — по памяти.

Запуск:
    .venv/bin/python -m tools.bench_threads
"""

import json
import threading
import time
from pathlib import Path

import numpy as np

REPORT = Path("reports/stage0-threads.json")
MS = 50.0
REPEATS = 5
MAX_THREADS = 6


def main():
    from neural.visual import VisualMemoryBrain

    print(f"Загружаю {MAX_THREADS} состояний…", flush=True)
    brains = []
    for _ in range(MAX_THREADS):
        b = VisualMemoryBrain()
        b.weights_frozen = True
        brains.append(b)

    rng = np.random.default_rng(20260920)
    zero = np.zeros(len(brains[0].retina), dtype=np.float32)
    stimuli = [
        (rng.choice(b.n, size=512, replace=False).astype(np.int32), np.float32(20.0))
        for b in brains
    ]

    def work(b, s):
        b.step(zero, MS, learning=False, stimulation=s, lamina_bias=0.0)

    for b, s in zip(brains, stimuli):  # прогрев каждого состояния
        work(b, s)

    rows = []
    for k in range(1, MAX_THREADS + 1):
        samples = []
        for _ in range(REPEATS):
            threads = [
                threading.Thread(target=work, args=(brains[i], stimuli[i]))
                for i in range(k)
            ]
            t = time.perf_counter()
            for th in threads:
                th.start()
            for th in threads:
                th.join()
            samples.append(time.perf_counter() - t)
        median = float(np.median(samples))
        rows.append(
            {
                "threads": k,
                "median_seconds": round(median, 4),
                "neural_ms_per_wall_second": round(k * MS / median, 1),
            }
        )
        base = rows[0]["median_seconds"]
        # Совокупная пропускная способность относительно одного потока.
        rows[-1]["aggregate_speedup"] = round(k * base / median, 2)
        print(
            f"{k} поток(ов): {median * 1000:7.1f} мс на шаг | "
            f"совокупно ×{rows[-1]['aggregate_speedup']:.2f} | "
            f"{rows[-1]['neural_ms_per_wall_second']:7.1f} нейро-мс/с всего",
            flush=True,
        )

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(
        json.dumps(
            {"neural_ms_per_step": MS, "repeats": REPEATS, "scaling": rows},
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )
    print(f"\nОтчёт: {REPORT}", flush=True)


if __name__ == "__main__":
    main()
