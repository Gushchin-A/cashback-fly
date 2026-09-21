"""Этап 1 — диагностика: доходит ли запах до выхода и несёт ли паттерн опознание.

Проверка разделимости показала, что средняя частота по всему решающему пулу
запахи не различает. Усреднение по 1314 клеткам убивает структуру: важно не
«насколько громко», а «кто именно» отвечает. Этот зонд разбирает, что на самом
деле происходит, и отвечает на три вопроса по очереди.

  1. Доходит ли стимул. Частота пула с запахом против частоты без запаха.
     Если они равны, обонятельный вход просто не добивает до выхода, и никакая
     метрика не спасёт — чинить надо стимуляцию.

  2. Воспроизводим ли паттерн. Один и тот же запах, поданный дважды, должен
     давать похожие векторы активности. Мера — корреляция между повторами.

  3. Различимы ли паттерны разных запахов. Корреляция между разными запахами
     должна быть заметно ниже, чем между повторами одного. Разрыв между этими
     двумя числами и есть весь полезный сигнал.

Дополнительно прогоняется несколько длительностей окна: счётный шум падает как
1/sqrt(T), и надо понять, лечится ли задача просто более длинным окном.

Запуск:
    .venv/bin/python -m tools.probe_pattern
"""

import argparse
import json
import time
from pathlib import Path

import numpy as np

REPORT = Path("reports/stage1-pattern.json")


def corr(a, b):
    a = a - a.mean()
    b = b - b.mean()
    d = np.sqrt((a**2).sum() * (b**2).sum())
    return float((a * b).sum() / d) if d else 0.0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--month", default="sentyabr")
    p.add_argument("--categories", type=int, default=6)
    p.add_argument("--repeats", type=int, default=4)
    p.add_argument("--windows", type=float, nargs="+", default=[50.0, 200.0, 500.0])
    a = p.parse_args()

    from neural.flybrain import FlyBrain

    fb = FlyBrain()
    sid = fb.create_state([], room="probe")
    brain = fb._states[sid].brain
    zero = fb._zero_light
    month = fb.month(a.month)
    cats = [o["category"] for o in month["offers"]][: a.categories]
    pool = fb.decision

    def run(stimulation, ms):
        counts, _ = brain.step(
            zero, ms, learning=False, stimulation=stimulation,
            lamina_bias=fb.lamina_bias,
        )
        return counts[pool].astype(np.float64)

    result = {"month": a.month, "categories": cats, "repeats": a.repeats, "windows": []}

    for ms in a.windows:
        print(f"\n=== окно {ms:.0f} мс ===", flush=True)
        started = time.perf_counter()

        # 1. Доходит ли стимул вообще.
        base = [run(None, ms) for _ in range(a.repeats)]
        base_hz = float(np.mean([b.sum() / (len(pool) * ms / 1000) for b in base]))

        # 2-3. Паттерны: каждая категория по несколько повторов.
        patterns = {c: [] for c in cats}
        for _ in range(a.repeats):
            for c in cats:
                ix, amp = fb.odor.stimulus(c, 1.0)
                patterns[c].append(run((ix, amp), ms))

        stim_hz = float(
            np.mean([v.sum() / (len(pool) * ms / 1000) for c in cats for v in patterns[c]])
        )

        # Корреляция между повторами одного запаха.
        within = [
            corr(patterns[c][i], patterns[c][j])
            for c in cats
            for i in range(a.repeats)
            for j in range(i + 1, a.repeats)
        ]
        # Корреляция между разными запахами.
        between = [
            corr(patterns[c1][i], patterns[c2][j])
            for n1, c1 in enumerate(cats)
            for c2 in cats[n1 + 1 :]
            for i in range(a.repeats)
            for j in range(a.repeats)
        ]

        # Сколько нисходящих нейронов вообще разряжается за окно.
        active = float(np.mean([np.count_nonzero(v) for c in cats for v in patterns[c]]))

        row = {
            "window_ms": ms,
            "baseline_hz": round(base_hz, 3),
            "stimulated_hz": round(stim_hz, 3),
            "stimulus_effect_hz": round(stim_hz - base_hz, 3),
            "active_decision_cells": round(active, 1),
            "pool_size": int(len(pool)),
            "within_odor_corr": round(float(np.mean(within)), 4),
            "between_odor_corr": round(float(np.mean(between)), 4),
            "separation": round(float(np.mean(within) - np.mean(between)), 4),
            "seconds": round(time.perf_counter() - started, 1),
        }
        result["windows"].append(row)
        print(
            f"  фон {row['baseline_hz']:.2f} Гц → с запахом {row['stimulated_hz']:.2f} Гц "
            f"(эффект {row['stimulus_effect_hz']:+.2f})"
        )
        print(
            f"  активных нисходящих {row['active_decision_cells']:.0f} из {row['pool_size']}"
        )
        print(
            f"  корреляция: внутри запаха {row['within_odor_corr']:.4f}, "
            f"между запахами {row['between_odor_corr']:.4f}, "
            f"разрыв {row['separation']:+.4f}"
        )

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(f"\nОтчёт: {REPORT}")


if __name__ == "__main__":
    main()
