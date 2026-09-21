"""Покрытие категорий тегами: сколько категорий усиливает каждая комната.

Число слотов, которые заберут интересы, определяется в первую очередь тем,
сколько категорий месяца вообще попадает под теги комнаты. Если под интересы
подходит 10 категорий из 17, случайный выбор сам по себе даст почти 3 слота
из 5, и никакой коэффициент усиления это не исправит.

Поэтому решения о сужении тегов принимаются по этой таблице, а не на глаз:
она показывает случайный уровень каждой комнаты и вклад каждого отдельного
тега.

Запуск:
    .venv/bin/python -m tools.tag_coverage
    .venv/bin/python -m tools.tag_coverage --room obyknovennaya
"""

import argparse
import json
from pathlib import Path


def load(name):
    return json.loads(Path("config", name).read_text(encoding="utf-8"))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--room", help="разобрать по тегам одну комнату")
    p.add_argument("--slots", type=int, default=None)
    a = p.parse_args()

    cats = load("categories.json")
    interests = load("interests.json")["interests"]
    rooms = load("rooms.json")["rooms"]
    slots = a.slots or load("simulation.json")["slots"]
    months = cats["months"]
    catalog = cats["categories"]

    def covered(tags, month):
        return [
            o["category"] for o in month["offers"]
            if set(catalog[o["category"]]["tags"]) & tags
        ]

    print(f"Слотов: {slots}. Месяцев: {len(months)}.\n")
    header = f"{'комната':20s} {'тегов':>6s} " + " ".join(
        f"{m['title'][:9]:>12s}" for m in months
    ) + f" {'случайно':>9s}"
    print(header)
    print("-" * len(header))

    for room in rooms:
        tags = {t for i in room["interests"] for t in interests[i]["tags"]}
        cells, chances = [], []
        for m in months:
            n = len(covered(tags, m))
            total = len(m["offers"])
            chance = slots * n / total
            chances.append(chance)
            cells.append(f"{n:2d}/{total} → {chance:.1f}")
        mark = " ←" if room.get("enabled") else ""
        print(
            f"{room['title']:20s} {len(tags):6d} "
            + " ".join(f"{c:>12s}" for c in cells)
            + f" {sum(chances) / len(chances):9.2f}{mark}"
        )
    print("\n«случайно» — сколько слотов из "
          f"{slots} досталось бы интересам при выборе наугад.")
    print("← отмечена включённая комната.")

    if not a.room:
        return

    room = next(r for r in rooms if r["id"] == a.room)
    tags = {t for i in room["interests"] for t in interests[i]["tags"]}
    print(f"\n\n=== {room['title']}: вклад каждого тега ===\n")
    print(f"{'тег':16s} {'через интерес':26s} {'категорий':>10s} "
          f"{'из них только он':>17s}")
    print("-" * 74)
    rows = []
    for tag in sorted(tags):
        via = [interests[i]["title"] for i in room["interests"]
               if tag in interests[i]["tags"]]
        hit = exclusive = 0
        for m in months:
            for o in m["offers"]:
                ctags = set(catalog[o["category"]]["tags"])
                if tag in ctags:
                    hit += 1
                    # Снятие тега уберёт категорию, только если её не держат
                    # другие теги комнаты.
                    if not (ctags & tags - {tag}):
                        exclusive += 1
        rows.append((tag, ", ".join(via), hit, exclusive))
    for tag, via, hit, exclusive in sorted(rows, key=lambda r: -r[3]):
        print(f"{tag:16s} {via[:26]:26s} {hit:10d} {exclusive:17d}")
    print("\n«из них только он» — сколько усилений пропадёт, если снять тег: "
          "\nостальные держатся ещё каким-то тегом комнаты и не пострадают.")


if __name__ == "__main__":
    main()
