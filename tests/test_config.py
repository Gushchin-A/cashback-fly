"""Целостность конфигов. Опечатка в теге молча обесценила бы интерес."""

import json
from pathlib import Path

import pytest

CONFIG = Path("config")


def load(name):
    return json.loads((CONFIG / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def data():
    return load("categories.json"), load("interests.json"), load("rooms.json")


def test_tags_declared_once(data):
    categories, _, _ = data
    assert len(categories["tags"]) == len(set(categories["tags"]))


def test_category_tags_are_declared(data):
    categories, _, _ = data
    declared = set(categories["tags"])
    for cid, c in categories["categories"].items():
        unknown = set(c["tags"]) - declared
        assert not unknown, f"категория {cid} использует необъявленные теги {unknown}"


def test_interest_tags_are_declared(data):
    """Тег интереса, которого нет ни у одной категории, никогда не сработает."""
    categories, interests, _ = data
    declared = set(categories["tags"])
    for iid, i in interests["interests"].items():
        unknown = set(i["tags"]) - declared
        assert not unknown, f"интерес {iid} использует необъявленные теги {unknown}"


def test_every_declared_tag_is_used_by_some_category(data):
    categories, _, _ = data
    used = {t for c in categories["categories"].values() for t in c["tags"]}
    assert not set(categories["tags"]) - used


def test_every_interest_tag_reaches_some_category(data):
    categories, interests, _ = data
    used = {t for c in categories["categories"].values() for t in c["tags"]}
    for iid, i in interests["interests"].items():
        assert set(i["tags"]) & used, f"интерес {iid} не достаёт ни до одной категории"


def test_months_reference_existing_categories(data):
    categories, _, _ = data
    known = set(categories["categories"])
    for month in categories["months"]:
        ids = [o["category"] for o in month["offers"]]
        assert len(ids) == len(set(ids)), f"дубликат категории в месяце {month['id']}"
        unknown = set(ids) - known
        assert not unknown, f"месяц {month['id']} ссылается на {unknown}"


def test_offer_rates_are_not_negative(data):
    """Отрицательный процент читается как «отрицательный кешбэк», а такого
    в каталоге нет: скидка на доставку показывается положительным числом."""
    categories, _, _ = data
    for month in categories["months"]:
        for offer in month["offers"]:
            assert offer["rate"] >= 0, f"{month['id']}/{offer['category']}: {offer['rate']}"


def test_months_are_unique_and_nonempty(data):
    categories, _, _ = data
    ids = [m["id"] for m in categories["months"]]
    assert len(ids) == len(set(ids))
    for month in categories["months"]:
        assert month["offers"], f"месяц {month['id']} пуст"


def test_rooms_reference_existing_interests(data):
    _, interests, rooms = data
    known = set(interests["interests"])
    ids = [r["id"] for r in rooms["rooms"]]
    assert len(ids) == len(set(ids)), "дубликат id комнаты"
    for room in rooms["rooms"]:
        unknown = set(room["interests"]) - known
        assert not unknown, f"комната {room['id']} ссылается на {unknown}"
        assert room["interests"], f"комната {room['id']} без интересов"


def test_at_least_one_room_enabled(data):
    _, _, rooms = data
    assert any(r.get("enabled") for r in rooms["rooms"])


def test_room_ids_are_url_safe(data):
    """id комнаты попадает в путь снимка, поэтому только [a-z0-9-]."""
    import re

    _, _, rooms = data
    for room in rooms["rooms"]:
        assert re.fullmatch(r"[a-z0-9-]+", room["id"]), room["id"]


def test_simulation_config_is_sane():
    c = load("simulation.json")
    assert 1 <= c["slots"] <= 10
    assert 1 <= c["sweeps"] <= 20
    assert c["stimulus"]["interest_gain"] >= 1.0
    assert 0 < c["neural"]["window_ms"] <= 1000
    assert c["cycle_seconds"] > 0
    # Окна кратны шагу ядра 0,1 мс, иначе округление меняет длительность.
    for key in ("window_ms", "reveal_window_ms"):
        value = c["neural"][key]
        assert abs(value * 10 - round(value * 10)) < 1e-7, key
