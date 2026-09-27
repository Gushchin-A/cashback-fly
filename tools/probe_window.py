"""Этап 1 — длина окна: сколько нейронного времени нужно на опознание.

Счётный шум падает как 1/sqrt(T), поэтому устойчивость порядка категорий
должна расти с длиной окна. Надо найти точку, где порядок уже воспроизводим,
а бюджет цикла ещё не проеден.

Меряются только выходные популяции — те, по которым ТЗ разрешает принимать
решение: выходы грибовидного тела (MBON), боковой рог и нисходящие нейроны.
Клетки Кеньона и граф целиком идут справочно, решение по ним приниматься не
будет.

Запуск:
    .venv/bin/python -m tools.probe_window --windows 100 200 400
"""

import argparse
import json
import time
from pathlib import Path

import numpy as np

from tools.probe_stages import anova_f, spearman

REPORT = Path("reports/stage1-window.json")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--month", default="sentyabr")
    p.add_argument("--repeats", type=int, default=10)
    p.add_argument("--windows", type=float, nargs="+", default=[100.0, 200.0, 400.0])
    p.add_argument("--seed", type=int, default=20260920)
    a = p.parse_args()

    from neural.flybrain import FlyBrain

    fb = FlyBrain()
    sid = fb.create_state([], room="probe")
    brain = fb._states[sid].brain
    zero = fb._zero_light
    types = fb.types

    def by_prefix(*prefixes):
        m = np.zeros(len(types), dtype=bool)
        for pref in prefixes:
            m |= types.str.startswith(pref).to_numpy()
        return np.flatnonzero(m).astype(np.int32)

    pools = {
        "MBON": by_prefix("MBON"),
        "боковой рог": by_prefix("LH"),
        "нисходящие": fb.decision,
    }
    cats = [o["category"] for o in fb.month(a.month)["offers"]]
    rng = np.random.default_rng(a.seed)
    rows = []

    for ms in a.windows:
        schedule = []
        for _ in range(a.repeats):
            order = list(cats)
            rng.shuffle(order)
            schedule.extend(order)
        records = {name: {c: [] for c in cats} for name in pools}
        started = time.perf_counter()
        for c in schedule:
            ix, amp = fb.odor.stimulus(c, 1.0)
            counts, _ = brain.step(
                zero, ms, learning=False,
                stimulation=(ix, amp), lamina_bias=fb.lamina_bias,
            )
            seconds = ms / 1000
            for name, sel in pools.items():
                records[name][c].append(
                    float(counts[sel].sum() / (len(sel) * seconds))
                )
        wall = time.perf_counter() - started
        half = a.repeats // 2
        for name in pools:
            groups = [np.array(records[name][c]) for c in cats]
            ha = np.array([np.mean(records[name][c][:half]) for c in cats])
            hb = np.array([np.mean(records[name][c][half:]) for c in cats])
            rows.append({
                "window_ms": ms,
                "pool": name,
                "cells": int(len(pools[name])),
                "anova_f": round(anova_f(groups), 2),
                "rank_stability": round(spearman(ha, hb), 3),
                "wall_seconds_per_window": round(wall / len(schedule), 3),
            })
        print(f"окно {ms:4.0f} мс ({wall / len(schedule):.2f} с стенных на окно):", flush=True)
        for r in rows[-len(pools):]:
            print(f"   {r['pool']:14s} F {r['anova_f']:7.2f}  ранг {r['rank_stability']:6.3f}")

    # Во что обходится полный цикл выбора при каждой длине окна.
    windows_per_cycle = sum(len(cats) - i for i in range(5))
    print(f"\nполный цикл = {windows_per_cycle} окон:")
    budget = []
    for ms in a.windows:
        per = next(r for r in rows if r["window_ms"] == ms)["wall_seconds_per_window"]
        total = per * windows_per_cycle
        budget.append({"window_ms": ms, "cycle_wall_seconds": round(total, 1)})
        fits = "влезает в 30 с" if total <= 30 else "НЕ влезает в 30 с"
        print(f"   {ms:4.0f} мс → {total:5.1f} с стенных — {fits}")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(
        json.dumps(
            {"repeats": a.repeats, "rows": rows,
             "windows_per_cycle": windows_per_cycle, "cycle_budget": budget},
            indent=2, ensure_ascii=False,
        ) + "\n"
    )
    print(f"\nОтчёт: {REPORT}")


if __name__ == "__main__":
    main()
