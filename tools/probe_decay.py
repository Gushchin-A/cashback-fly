"""Этап 1 — затухание: сколько тишины нужно между категориями и чего это стоит.

Зонд по звеньям показал, что на чистом состоянии запах проходит всю цепочку,
а губит сигнал перенос активности из предыдущего окна. Сбрасывать граф между
категориями нельзя: по ТЗ симуляция непрерывна, да и муха не перезагружается.
Значит между запахами нужна пауза, за которую активность спадает сама.

Меряем две вещи:
  1. Кривая затухания — активность через разные интервалы тишины после запаха.
  2. Цена паузы по времени. Ядро событийное, и в Этапе 0 покой стоил 4,2 мс
     на 50 нейронных мс против 150 мс под стимулом. Если тишина дешёвая,
     длинная пауза почти ничего не стоит и протокол «запах + пауза» проходит
     по бюджету.

Запуск:
    .venv/bin/python -m tools.probe_decay
"""

import argparse
import json
import time
from pathlib import Path

import numpy as np

REPORT = Path("reports/stage1-decay.json")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--stim-ms", type=float, default=100.0)
    p.add_argument("--probe-ms", type=float, default=20.0)
    p.add_argument("--gaps", type=float, nargs="+",
                   default=[0.0, 50.0, 100.0, 200.0, 400.0, 800.0, 1600.0])
    p.add_argument("--category", default="knigi")
    a = p.parse_args()

    from neural.flybrain import FlyBrain

    fb = FlyBrain()
    sid = fb.create_state([], room="probe")
    brain = fb._states[sid].brain
    zero = fb._zero_light
    pool = fb.decision
    whole = np.arange(brain.n, dtype=np.int32)

    def integrate(stimulation, ms):
        t = time.perf_counter()
        counts, _ = brain.step(
            zero, ms, learning=False, stimulation=stimulation,
            lamina_bias=fb.lamina_bias,
        )
        return counts, time.perf_counter() - t

    ix, amp = fb.odor.stimulus(a.category, 1.0)
    rows = []
    print(f"запах {a.stim_ms:.0f} мс, затем тишина, затем замер {a.probe_ms:.0f} мс\n")
    print(f"{'пауза':>8s} {'граф, Гц':>10s} {'выход, Гц':>11s} "
          f"{'цена паузы':>12s} {'мс/нейро-с':>12s}")
    print("-" * 58)

    for gap in a.gaps:
        brain.reset()
        integrate((ix, amp), a.stim_ms)
        gap_seconds = 0.0
        if gap > 0:
            _, gap_seconds = integrate(None, gap)
        counts, _ = integrate(None, a.probe_ms)
        seconds = a.probe_ms / 1000
        whole_hz = float(counts.sum() / (len(whole) * seconds))
        pool_hz = float(counts[pool].sum() / (len(pool) * seconds))
        # Во что обходится секунда нейронной тишины по стенным часам.
        cost = (gap_seconds / (gap / 1000)) * 1000 if gap > 0 else 0.0
        rows.append({
            "gap_ms": gap,
            "whole_graph_hz": round(whole_hz, 4),
            "decision_hz": round(pool_hz, 4),
            "gap_wall_seconds": round(gap_seconds, 4),
            "wall_ms_per_neural_second": round(cost, 1),
        })
        print(f"{gap:7.0f}м {whole_hz:10.3f} {pool_hz:11.3f} "
              f"{gap_seconds:11.3f}с {cost:12.1f}")

    # Для сравнения: во что обходится окно под стимулом.
    brain.reset()
    _, stim_wall = integrate((ix, amp), a.stim_ms)
    stim_cost = stim_wall / (a.stim_ms / 1000) * 1000
    print(f"\nокно под стимулом {a.stim_ms:.0f} мс: {stim_wall:.3f} с "
          f"({stim_cost:.0f} мс стенных на нейронную секунду)")

    quiet = [r for r in rows if r["whole_graph_hz"] < 0.05]
    recommended = quiet[0]["gap_ms"] if quiet else None
    print(f"активность падает ниже 0.05 Гц после паузы: {recommended} мс")

    result = {
        "stim_ms": a.stim_ms,
        "probe_ms": a.probe_ms,
        "category": a.category,
        "decay": rows,
        "stimulated_window_wall_seconds": round(stim_wall, 4),
        "stimulated_wall_ms_per_neural_second": round(stim_cost, 1),
        "recommended_gap_ms": recommended,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(f"\nОтчёт: {REPORT}")


if __name__ == "__main__":
    main()
