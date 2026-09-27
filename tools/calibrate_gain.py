"""Этап 1 — подбор interest_gain и проверка выбора по требованиям ТЗ.

ТЗ требует, чтобы интересы забирали 2–3 слота из 5, но не все пять. Это
нельзя проверять абсолютным числом: комнаты покрывают категории очень
по-разному. У «Мухи обыкновенной» в сентябре под интересы попадает 10 из 17
категорий, и случайный выбор сам по себе даст 2,9 слота; у «Развивается» в
ноябре подходящих категорий всего две, и больше двух она взять не может.

Поэтому критерий здесь такой:
  • превышение над случайностью — интересы должны заметно поднимать свои
    категории, иначе коэффициент не работает;
  • незанятые слоты — категории вне интересов должны стабильно брать хотя бы
    пару слотов, иначе муха ничего не добирает сама;
  • воспроизводимость — повторные циклы одной комнаты должны совпадать
    заметно сильнее случайного (иначе это чистый шум);
  • но не полностью, иначе это залипание на одном ответе;
  • различность — разные комнаты в одном месяце должны выбирать разное.

Запуск:
    .venv/bin/python -m tools.calibrate_gain --gains 1.0 1.2 1.5 --repeats 2
"""

import argparse
import json
import time
from itertools import combinations
from pathlib import Path

import numpy as np

REPORT = Path("reports/stage1-calibration.json")


def jaccard(a, b):
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a | b else 0.0


def run_cycle(fb, sid, month_id):
    fb.start_month(sid, month_id)
    while not fb.cycle_complete(sid):
        fb.step(sid)
    return fb.readout(sid)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--gains", type=float, nargs="+", default=[1.0, 1.2, 1.5])
    p.add_argument("--repeats", type=int, default=2)
    p.add_argument("--months", nargs="+", default=None)
    p.add_argument("--rooms", nargs="+", default=None, help="по умолчанию все из конфига")
    p.add_argument(
        "--out", type=Path, default=None,
        help="куда писать отчёт; по умолчанию reports/stage1-calibration.json",
    )
    a = p.parse_args()

    from neural.flybrain import FlyBrain, load_config

    base_config = load_config("config/simulation.json")
    all_rooms = load_config("config/rooms.json")["rooms"]
    rooms = [r for r in all_rooms if not a.rooms or r["id"] in a.rooms]
    results = []
    started = time.perf_counter()

    for gain in a.gains:
        config = json.loads(json.dumps(base_config))
        config["stimulus"]["interest_gain"] = gain
        fb = FlyBrain(config=config)
        months = a.months or fb.month_ids
        catalog = fb.categories["categories"]
        total_cycles = len(rooms) * len(months) * a.repeats
        print(f"\n=== interest_gain = {gain} ({total_cycles} циклов) ===", flush=True)

        arm_started = time.perf_counter()
        runs = []
        for room in rooms:
            sid = fb.create_state(room["interests"], room=room["id"])
            for month_id in months:
                offers = [o["category"] for o in fb.month(month_id)["offers"]]
                eligible = [c for c in offers if fb.is_boosted(sid, c)]
                chance = 5 * len(eligible) / len(offers)
                for rep in range(a.repeats):
                    r = run_cycle(fb, sid, month_id)
                    boosted = [c for c in r.selected if fb.is_boosted(sid, c)]
                    runs.append({
                        "room": room["id"],
                        "month": month_id,
                        "repeat": rep,
                        "selected": list(r.selected),
                        "titles": [catalog[c]["title"] for c in r.selected],
                        "boosted_in_top": len(boosted),
                        "eligible": len(eligible),
                        "offers": len(offers),
                        "chance_baseline": round(chance, 2),
                    })
                    done = len(runs)
                    # Темп считается по текущей ветке: у каждого значения
                    # усиления свой прогон, и общий отсчёт завышал бы остаток.
                    rate = (time.perf_counter() - arm_started) / done
                    print(
                        f"  {room['id']:14s} {month_id:9s} #{rep} → "
                        f"интересов {len(boosted)}/5 (случайно {chance:.1f}) "
                        f"| ост. {rate * (total_cycles - done) / 60:.0f} мин",
                        flush=True,
                    )

        boosted_counts = np.array([r["boosted_in_top"] for r in runs])
        chances = np.array([r["chance_baseline"] for r in runs])

        # Воспроизводимость: повторы одной комнаты в одном месяце.
        repro, identical = [], []
        for room in rooms:
            for month_id in months:
                sel = [
                    r["selected"] for r in runs
                    if r["room"] == room["id"] and r["month"] == month_id
                ]
                for x, y in combinations(sel, 2):
                    repro.append(jaccard(x, y))
                    identical.append(x == y)

        # Различность: разные комнаты в одном месяце.
        across = []
        for month_id in months:
            sel = {
                room["id"]: [
                    r["selected"] for r in runs
                    if r["room"] == room["id"] and r["month"] == month_id
                ][0]
                for room in rooms
            }
            for x, y in combinations(sel.values(), 2):
                across.append(jaccard(x, y))

        row = {
            "interest_gain": gain,
            "cycles": len(runs),
            "mean_boosted_in_top5": round(float(boosted_counts.mean()), 2),
            "mean_chance_baseline": round(float(chances.mean()), 2),
            "lift_over_chance": round(float((boosted_counts - chances).mean()), 2),
            "mean_free_slots": round(float(5 - boosted_counts.mean()), 2),
            "saturated_cycles": int(np.count_nonzero(boosted_counts >= 5)),
            "reproducibility_jaccard": round(float(np.mean(repro)), 3) if repro else None,
            "identical_repeats_fraction": (
                round(float(np.mean(identical)), 3) if identical else None
            ),
            "across_room_jaccard": round(float(np.mean(across)), 3) if across else None,
            "runs": runs,
        }
        results.append(row)
        print(
            f"  итог: интересов {row['mean_boosted_in_top5']:.2f}/5 "
            f"(случайно {row['mean_chance_baseline']:.2f}, "
            f"превышение {row['lift_over_chance']:+.2f}), "
            f"воспроизводимость {row['reproducibility_jaccard']}, "
            f"между комнатами {row['across_room_jaccard']}",
            flush=True,
        )

    def fmt(value, spec):
        # Часть метрик не определена при одной комнате: между комнатами нечего
        # сравнивать. Пишем прочерк, а не роняем сводку.
        width = int(spec.split(".")[0].lstrip("+"))
        return format(value, spec) if value is not None else "—".rjust(width)

    print(f"\n{'усиление':>9s} {'интересов':>10s} {'случайно':>9s} "
          f"{'превыш.':>8s} {'повтор':>8s} {'идентич':>8s} {'между':>7s} {'сатур.':>7s}")
    print("-" * 72)
    for r in results:
        print(f"{r['interest_gain']:9.2f} {r['mean_boosted_in_top5']:10.2f} "
              f"{r['mean_chance_baseline']:9.2f} {r['lift_over_chance']:+8.2f} "
              f"{fmt(r['reproducibility_jaccard'], '8.3f')} "
              f"{fmt(r['identical_repeats_fraction'], '8.3f')} "
              f"{fmt(r['across_room_jaccard'], '7.3f')} {r['saturated_cycles']:7d}")

    out = a.out or REPORT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {"repeats": a.repeats, "rooms": [r["id"] for r in rooms],
             "months": months, "sweep": results},
            indent=2, ensure_ascii=False,
        ) + "\n"
    )
    print(f"\nОтчёт: {out}")
    print(f"Всего {(time.perf_counter() - started) / 60:.0f} мин")


if __name__ == "__main__":
    main()
