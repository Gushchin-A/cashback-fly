"""Этап 1 — различает ли граф категории вообще.

Это проверка «не чистый ли это шум», которую надо пройти до подбора усиления.
Каждая категория месяца предъявляется несколько раз в перемешанном порядке,
без интересов (усиление 1.0). Смотрим три вещи:

  Разделимость — разброс средних между категориями против разброса повторов
    одной категории. Если первый заметно больше второго, отклик определяется
    запахом, а не случайностью. Отношение считаем как F: дисперсия между
    категориями / дисперсия внутри категории.

  Дрейф — зависит ли отклик от номера предъявления. Сеть разогревается и
    адаптируется; если это доминирует, выбор будет определять очерёдность,
    а не категория. Порядок специально перемешан, так что систематический
    тренд по позиции виден.

  Устойчивость ранга — совпадает ли порядок категорий между независимыми
    половинами повторов (корреляция Спирмена). Это прямо отвечает на вопрос
    «не залипнет ли выбор и не будет ли он каждый раз новым».

Запуск:
    .venv/bin/python -m tools.probe_separability --repeats 8
"""

import argparse
import json
import time
from pathlib import Path

import numpy as np

REPORT = Path("reports/stage1-separability.json")


def spearman(a, b):
    ra = np.argsort(np.argsort(a))
    rb = np.argsort(np.argsort(b))
    ra = ra - ra.mean()
    rb = rb - rb.mean()
    denom = np.sqrt((ra**2).sum() * (rb**2).sum())
    return float((ra * rb).sum() / denom) if denom else 0.0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--month", default="sentyabr")
    p.add_argument("--repeats", type=int, default=8)
    p.add_argument("--seed", type=int, default=20260920)
    a = p.parse_args()

    from neural.flybrain import FlyBrain

    fb = FlyBrain()
    # Комната без интересов: усиление никуда не применяется, сравниваем
    # чистый отклик графа на запахи.
    sid = fb.create_state([], room="probe")
    month = fb.month(a.month)
    cats = [o["category"] for o in month["offers"]]
    print(f"{month['title']}: {len(cats)} категорий × {a.repeats} повторов", flush=True)

    rng = np.random.default_rng(a.seed)
    schedule = []
    for r in range(a.repeats):
        order = list(cats)
        rng.shuffle(order)
        schedule.extend((r, c) for c in order)

    zero = fb._zero_light
    samples = {c: [] for c in cats}
    by_position = []
    started = time.perf_counter()
    for i, (rep, c) in enumerate(schedule):
        ix, amp = fb.odor.stimulus(c, 1.0)
        counts, _ = fb._states[sid].brain.step(
            zero, fb.window_ms, learning=False,
            stimulation=(ix, amp), lamina_bias=fb.lamina_bias,
        )
        hz = float(
            counts[fb.decision].sum()
            / (len(fb.decision) * fb.window_ms / 1000)
        )
        samples[c].append(hz)
        by_position.append(hz)
        if (i + 1) % 20 == 0:
            done = i + 1
            rate = (time.perf_counter() - started) / done
            print(
                f"  {done}/{len(schedule)} окон, осталось "
                f"{rate * (len(schedule) - done):.0f} с",
                flush=True,
            )

    means = np.array([np.mean(samples[c]) for c in cats])
    within = np.array([np.var(samples[c], ddof=1) for c in cats])
    # F: разброс между категориями против типичного разброса внутри категории.
    f_ratio = float(np.var(means, ddof=1) / within.mean()) if within.mean() else 0.0

    pos = np.arange(len(by_position))
    vals = np.array(by_position)
    drift = float(np.corrcoef(pos, vals)[0, 1])

    half_a = np.array([np.mean(samples[c][: a.repeats // 2]) for c in cats])
    half_b = np.array([np.mean(samples[c][a.repeats // 2 :]) for c in cats])
    rank_stability = spearman(half_a, half_b)

    order = np.argsort(means)[::-1]
    result = {
        "month": a.month,
        "repeats": a.repeats,
        "window_ms": fb.window_ms,
        "decision_pool_size": int(len(fb.decision)),
        "f_ratio_between_over_within": round(f_ratio, 2),
        "drift_correlation_with_position": round(drift, 3),
        "rank_stability_spearman": round(rank_stability, 3),
        "overall_mean_hz": round(float(means.mean()), 3),
        "spread_between_categories_hz": round(float(means.std(ddof=1)), 3),
        "typical_within_category_sd_hz": round(float(np.sqrt(within.mean())), 3),
        "per_category": [
            {
                "category": cats[i],
                "mean_hz": round(float(means[i]), 3),
                "sd_hz": round(float(np.sqrt(within[i])), 3),
            }
            for i in order
        ],
    }

    print(f"\nF (между/внутри):            {f_ratio:8.2f}")
    print(f"дрейф по позиции (корр.):    {drift:8.3f}")
    print(f"устойчивость ранга (Спирмен):{rank_stability:8.3f}")
    print(
        f"средняя {means.mean():.2f} Гц, разброс между категориями "
        f"{means.std(ddof=1):.3f} Гц, внутри категории "
        f"{np.sqrt(within.mean()):.3f} Гц"
    )
    print("\nтоп-5 по отклику без интересов:")
    for i in order[:5]:
        print(f"  {cats[i]:22s} {means[i]:6.3f} Гц ± {np.sqrt(within[i]):.3f}")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(f"\nОтчёт: {REPORT}")


if __name__ == "__main__":
    main()
