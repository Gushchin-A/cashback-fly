"""Этап 1 — какое звено несёт опознание запаха на работающей сети.

Предыдущие зонды дали три факта:
  • на чистом состоянии запах проходит всю цепочку;
  • активность после запаха не затухает — сеть самоподдерживается;
  • нисходящие нейроны на этом фоне запахи не различают.

Сбрасывать граф между категориями нельзя, пауза не помогает и дороже стимула.
Значит считывать надо там, где запах ещё не утонул в фоне. Этот зонд меряет
все звенья ОДНОВРЕМЕННО, в продакшен-условиях: непрерывный прогон, категории
в перемешанном порядке, никаких сбросов.

Для каждого звена считаются две величины:
  F — однофакторный дисперсионный анализ: разброс между категориями против
      разброса повторов. F ≈ 1 значит чистый шум, F >> 1 — реальный сигнал.
  ранг — корреляция Спирмена между двумя независимыми половинами повторов:
      воспроизводится ли порядок категорий.

Звено, которое выигрывает по обоим, и становится решающим пулом. Выбор пула
делается по данным один раз и одинаков для всех категорий и комнат.

Запуск:
    .venv/bin/python -m tools.probe_stages --repeats 12
"""

import argparse
import json
import time
from pathlib import Path

import numpy as np

REPORT = Path("reports/stage1-stages.json")


def spearman(a, b):
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    ra -= ra.mean()
    rb -= rb.mean()
    d = np.sqrt((ra**2).sum() * (rb**2).sum())
    return float((ra * rb).sum() / d) if d else 0.0


def anova_f(groups):
    """Классический однофакторный F. При чистом шуме ожидается ≈ 1."""
    k = len(groups)
    n = len(groups[0])
    means = np.array([g.mean() for g in groups])
    grand = means.mean()
    between = n * ((means - grand) ** 2).sum() / (k - 1)
    within = sum(((g - g.mean()) ** 2).sum() for g in groups) / (k * (n - 1))
    return float(between / within) if within else 0.0


def build_stages(fb, brain):
    types = fb.types
    orn = np.concatenate([fb.glomeruli[g] for g in sorted(fb.glomeruli)])
    def by_prefix(*prefixes):
        m = np.zeros(len(types), dtype=bool)
        for p in prefixes:
            m |= types.str.startswith(p).to_numpy()
        return np.flatnonzero(m).astype(np.int32)
    return {
        "ORN": orn.astype(np.int32),
        "антеннальная доля": np.flatnonzero(
            types.str.match(r"^(M_|V_|l?v?PN|il3LN|AL)").to_numpy()
        ).astype(np.int32),
        "боковой рог": by_prefix("LH"),
        "клетки Кеньона": brain.circuit["kc"],
        "MBON": by_prefix("MBON"),
        "нисходящие": fb.decision,
        "весь граф": np.arange(brain.n, dtype=np.int32),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--month", default="sentyabr")
    p.add_argument("--repeats", type=int, default=12)
    p.add_argument("--window-ms", type=float, default=100.0)
    p.add_argument("--seed", type=int, default=20260920)
    a = p.parse_args()

    from neural.flybrain import FlyBrain

    fb = FlyBrain()
    sid = fb.create_state([], room="probe")
    brain = fb._states[sid].brain
    zero = fb._zero_light
    stages = build_stages(fb, brain)
    for name, ix in stages.items():
        print(f"{name}: {len(ix)} клеток")

    cats = [o["category"] for o in fb.month(a.month)["offers"]]
    rng = np.random.default_rng(a.seed)
    schedule = []
    for _ in range(a.repeats):
        order = list(cats)
        rng.shuffle(order)
        schedule.extend(order)

    print(
        f"\n{len(cats)} категорий × {a.repeats} повторов = {len(schedule)} окон "
        f"по {a.window_ms:.0f} мс, без сбросов\n",
        flush=True,
    )
    records = {name: {c: [] for c in cats} for name in stages}
    started = time.perf_counter()
    for i, c in enumerate(schedule):
        ix, amp = fb.odor.stimulus(c, 1.0)
        counts, _ = brain.step(
            zero, a.window_ms, learning=False,
            stimulation=(ix, amp), lamina_bias=fb.lamina_bias,
        )
        seconds = a.window_ms / 1000
        for name, sel in stages.items():
            records[name][c].append(float(counts[sel].sum() / (len(sel) * seconds)))
        if (i + 1) % 34 == 0:
            rate = (time.perf_counter() - started) / (i + 1)
            print(f"  {i + 1}/{len(schedule)}, осталось "
                  f"{rate * (len(schedule) - i - 1):.0f} с", flush=True)

    half = a.repeats // 2
    rows = []
    for name in stages:
        groups = [np.array(records[name][c]) for c in cats]
        means = np.array([g.mean() for g in groups])
        f = anova_f(groups)
        ha = np.array([np.mean(records[name][c][:half]) for c in cats])
        hb = np.array([np.mean(records[name][c][half:]) for c in cats])
        rows.append({
            "stage": name,
            "cells": int(len(stages[name])),
            "mean_hz": round(float(means.mean()), 3),
            "between_sd_hz": round(float(means.std(ddof=1)), 4),
            "within_sd_hz": round(float(np.mean([g.std(ddof=1) for g in groups])), 4),
            "anova_f": round(f, 2),
            "rank_stability": round(spearman(ha, hb), 3),
        })

    print(f"\n{'звено':20s} {'клеток':>7s} {'средн Гц':>9s} "
          f"{'между':>8s} {'внутри':>8s} {'F':>8s} {'ранг':>7s}")
    print("-" * 72)
    for r in rows:
        print(f"{r['stage']:20s} {r['cells']:7d} {r['mean_hz']:9.3f} "
              f"{r['between_sd_hz']:8.4f} {r['within_sd_hz']:8.4f} "
              f"{r['anova_f']:8.2f} {r['rank_stability']:7.3f}")

    best = max(
        (r for r in rows if r["stage"] != "ORN"),
        key=lambda r: (r["rank_stability"], r["anova_f"]),
    )
    print(f"\nлучшее звено (кроме входа): {best['stage']} "
          f"— F {best['anova_f']:.1f}, ранг {best['rank_stability']:.3f}")

    result = {
        "month": a.month,
        "repeats": a.repeats,
        "window_ms": a.window_ms,
        "windows": len(schedule),
        "continuous_no_reset": True,
        "stages": rows,
        "best_non_input_stage": best["stage"],
        "per_category_by_stage": {
            name: {c: round(float(np.mean(v)), 4) for c, v in d.items()}
            for name, d in records.items()
        },
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(f"\nОтчёт: {REPORT}")


if __name__ == "__main__":
    main()
