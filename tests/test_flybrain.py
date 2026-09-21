"""Тесты нейронного модуля на полном графе.

Требуют собранный коннектом, поэтому по умолчанию пропускаются:
    FLY_FULL_TEST=1 .venv/bin/python -m pytest tests/test_flybrain.py -q
"""

import json
import os

import numpy as np
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("FLY_FULL_TEST") != "1",
    reason="нужен собранный коннектом; включается FLY_FULL_TEST=1",
)


@pytest.fixture(scope="module")
def fb():
    from neural.flybrain import FlyBrain

    return FlyBrain()


def test_graph_is_the_full_retained_release(fb):
    p = fb.provenance()
    assert p["neurons"] == 166700
    assert p["directed_edges"] == 25582938


def test_olfactory_alphabet(fb):
    p = fb.provenance()
    assert p["glomeruli"] == 53
    assert p["olfactory_neurons"] == 2635


def test_decision_pool_comes_from_annotations(fb):
    # Пул задан разметкой, а не списком id в коде.
    assert len(fb.decision) > 1000
    assert all(fb.types.iloc[i].startswith("LH") for i in fb.decision[:50])


def test_unknown_interest_is_rejected(fb):
    with pytest.raises(ValueError):
        fb.create_state(["такого-интереса-нет"])


def test_interest_tags_are_derived_from_config(fb):
    sid = fb.create_state(["knigi", "sport"], room="t-tags")
    assert fb.readout(sid).interest_tags == ["книги", "здоровье", "спорт"].__class__(
        sorted(["книги", "здоровье", "спорт"])
    )


def test_boost_follows_tag_overlap(fb):
    sid = fb.create_state(["knigi"], room="t-boost")
    assert fb.is_boosted(sid, "knigi")  # тег «книги»
    assert not fb.is_boosted(sid, "apteki")  # тег «здоровье»
    assert not fb.is_boosted(sid, "vse-pokupki")  # без тегов вообще


def test_category_without_tags_is_never_boosted(fb):
    sid = fb.create_state(list(fb.interests["interests"]), room="t-all")
    for cid in ["vse-pokupki", "oplata-qr", "oplata-telefonom", "drugie-servisy"]:
        assert not fb.is_boosted(sid, cid)


def test_unit_gain_makes_interests_irrelevant():
    """Главная архитектурная гарантия: интересы входят ТОЛЬКО через амплитуду.

    При усилении 1.0 стимул не зависит от интересов, значит две комнаты с
    совершенно разными интересами обязаны выбрать одно и то же. Если бы
    где-то в коде сидело соответствие интерес→категория, этот тест упал бы.
    """
    from neural.flybrain import FlyBrain, load_config

    config = load_config("config/simulation.json")
    config["stimulus"]["interest_gain"] = 1.0
    config["sweeps"] = 1
    fb = FlyBrain(config=config)

    picks = []
    for interests in (["knigi", "samorazvitie"], ["drifting", "kotiki", "meykap"]):
        sid = fb.create_state(interests, room="probe")  # одинаковое имя → одно расписание
        fb.start_month(sid, "sentyabr")
        while not fb.cycle_complete(sid):
            fb.step(sid)
        picks.append(fb.readout(sid).selected)
    assert picks[0] == picks[1]


def test_cycle_fills_every_slot(fb):
    sid = fb.create_state(["knigi"], room="t-cycle")
    fb.start_month(sid, "sentyabr")
    guard = 0
    while not fb.cycle_complete(sid):
        fb.step(sid)
        guard += 1
        assert guard < 500, "цикл не сходится"
    r = fb.readout(sid)
    assert len(r.selected) == fb.slots
    assert len(set(r.selected)) == fb.slots
    assert r.windows_done == fb.windows_per_cycle(sid)
    assert r.complete


def test_selected_are_categories_of_that_month(fb):
    sid = fb.create_state(["igry"], room="t-month")
    fb.start_month(sid, "noyabr")
    while not fb.cycle_complete(sid):
        fb.step(sid)
    offers = {o["category"] for o in fb.month("noyabr")["offers"]}
    assert set(fb.readout(sid).selected) <= offers


def test_step_before_start_month_is_an_error(fb):
    sid = fb.create_state([], room="t-nostart")
    with pytest.raises(RuntimeError):
        fb.step(sid)


def test_unknown_state_is_an_error(fb):
    with pytest.raises(KeyError):
        fb.readout(9999)


def test_readout_serializes_to_json(fb):
    sid = fb.create_state(["sport"], room="t-json")
    fb.start_month(sid, "oktyabr")
    fb.step(sid)
    d = fb.readout(sid).to_dict()
    text = json.dumps(d, ensure_ascii=False)
    assert json.loads(text)["room"] == "t-json"
    # Идентификаторы нейронов — строки: 64-битные id не переживут JS-число.
    assert all(isinstance(i, str) for i in d["sample_ids"])


def test_simulation_advances_neural_time(fb):
    sid = fb.create_state([], room="t-time")
    fb.start_month(sid, "sentyabr")
    before = fb.readout(sid).sim_ms
    fb.step(sid)
    after = fb.readout(sid).sim_ms
    assert after == pytest.approx(before + fb.window_ms, abs=1e-6)


def test_states_are_independent(fb):
    a = fb.create_state(["knigi"], room="t-a")
    b = fb.create_state(["igry"], room="t-b")
    fb.start_month(a, "sentyabr")
    fb.start_month(b, "sentyabr")
    for _ in range(3):
        fb.step(a)
    assert fb.readout(a).windows_done == 3
    assert fb.readout(b).windows_done == 0


def test_states_share_one_wiring_copy(fb):
    """ТЗ требует одну копию проводки в памяти на все комнаты."""
    a = fb.create_state([], room="t-w1")
    b = fb.create_state([], room="t-w2")
    for field in ("ptr", "post", "weight", "ids"):
        first = getattr(fb._states[a].brain, field)
        second = getattr(fb._states[b].brain, field)
        assert first is second, f"{field} продублирован между состояниями"


def test_schedule_covers_every_category_each_sweep(fb):
    sid = fb.create_state([], room="t-sched")
    fb.start_month(sid, "sentyabr")
    st = fb._states[sid]
    n = len(st.order)
    for s in range(fb.sweeps):
        sweep = st.schedule[s * n : (s + 1) * n]
        assert sorted(sweep) == sorted(st.order)


def test_reveal_phase_does_not_change_ratings(fb):
    """Окна оглашения не должны менять порядок, который они оглашают."""
    sid = fb.create_state(["knigi"], room="t-reveal")
    fb.start_month(sid, "sentyabr")
    st = fb._states[sid]
    while st.cursor < len(st.schedule):
        fb.step(sid)
    frozen = dict(st.ratings)
    while not fb.cycle_complete(sid):
        fb.step(sid)
    assert st.ratings == frozen


def test_selection_follows_ratings(fb):
    """Слоты обязаны идти строго по убыванию накопленного рейтинга."""
    sid = fb.create_state(["frukty", "sport"], room="t-order")
    fb.start_month(sid, "sentyabr")
    while not fb.cycle_complete(sid):
        fb.step(sid)
    st = fb._states[sid]
    scores = [st.mean_rating(c) for c in st.selected]
    assert scores == sorted(scores, reverse=True)
    losers = [st.mean_rating(c) for c in st.remaining()]
    assert min(scores) >= max(losers)


def test_month_ring_closes_for_any_count(fb):
    """Кольцо месяцев не должно зависеть от их числа — их уже не три."""
    ids = fb.month_ids
    assert len(ids) >= 2
    assert fb.next_month() == ids[0]
    seen = [ids[0]]
    for _ in range(len(ids) - 1):
        seen.append(fb.next_month(seen[-1]))
    assert seen == ids, "кольцо обошло месяцы не в том порядке"
    assert fb.next_month(ids[-1]) == ids[0], "кольцо не замкнулось"


def test_unknown_month_is_an_error(fb):
    with pytest.raises(KeyError):
        fb.next_month("brumaire")
    with pytest.raises(KeyError):
        fb.month("brumaire")


def test_every_configured_month_runs(fb):
    """Каждый месяц из конфига должен проходить цикл, включая новые."""
    for month_id in fb.month_ids:
        sid = fb.create_state([], room=f"t-m-{month_id}")
        fb.start_month(sid, month_id)
        # Полный цикл дорогой; достаточно убедиться, что расписание построено
        # по фактическому числу категорий этого месяца.
        st = fb._states[sid]
        offers = [o["category"] for o in fb.month(month_id)["offers"]]
        assert st.order == offers
        assert len(st.schedule) == len(offers) * fb.sweeps
        assert fb.windows_per_cycle(sid) == len(st.schedule) + fb.slots
        fb.step(sid)
        assert fb.readout(sid).presenting in offers


def test_provenance_records_the_decoder(fb):
    p = fb.provenance()
    assert p["interest_gain"] == fb.interest_gain
    assert p["learning"] is False
    assert "source_sha256" in p and p["source_sha256"]
