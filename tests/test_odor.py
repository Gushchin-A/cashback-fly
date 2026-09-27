"""Кодирование категории в запах: детерминированность и отсутствие смысла.

Паттерн обязан быть вечным — он определяет, чем категория пахнет. Если он
поедет между запусками, муха будет каждый день нюхать другой список, а
восстановление состояния из чекпойнта потеряет смысл.
"""

import subprocess
import sys

import numpy as np
import pytest

from neural.odor import NAMESPACE_DEFAULT, OdorCode, category_seed


@pytest.fixture
def code():
    # Синтетические гломерулы: тест кодировщика не требует коннектома.
    rng = np.random.default_rng(0)
    groups = {
        f"ORN_G{i:02d}": np.sort(rng.choice(5000, size=20, replace=False)).astype(np.int32)
        for i in range(53)
    }
    return OdorCode(groups, glomeruli_per_category=8, base_amplitude_mv=18.0)


def test_seed_is_stable_across_processes():
    """hash() рандомизируется между запусками, blake2b — нет."""
    out = subprocess.run(
        [sys.executable, "-c",
         "from neural.odor import category_seed; print(category_seed('knigi'))"],
        capture_output=True, text=True, check=True,
    )
    assert int(out.stdout.strip()) == category_seed("knigi")


def test_seed_depends_on_namespace():
    assert category_seed("knigi", "a") != category_seed("knigi", "b")
    assert category_seed("knigi") == category_seed("knigi", NAMESPACE_DEFAULT)


def test_pattern_is_repeatable(code):
    a_ix, a_amp = code.pattern("knigi")
    b_ix, b_amp = code.pattern("knigi")
    assert np.array_equal(a_ix, b_ix)
    assert np.array_equal(a_amp, b_amp)


def test_different_categories_differ(code):
    a, _ = code.pattern("knigi")
    b, _ = code.pattern("kafe")
    assert not np.array_equal(a, b)


def test_pattern_uses_requested_number_of_glomeruli(code):
    for cid in ["knigi", "kafe", "igry", "plus"]:
        assert len(code.describe(cid)["glomeruli"]) == 8
        assert len(set(code.describe(cid)["glomeruli"])) == 8


def test_caller_cannot_corrupt_the_cache(code):
    ix, amp = code.pattern("knigi")
    ix[0] = -12345
    amp[0] = 999.0
    fresh_ix, fresh_amp = code.pattern("knigi")
    assert fresh_ix[0] != -12345
    assert fresh_amp[0] != 999.0


def test_gain_scales_amplitude_not_membership(code):
    ix1, amp1 = code.stimulus("knigi", 1.0)
    ix2, amp2 = code.stimulus("knigi", 2.0)
    # Интерес делает запах громче, а не другим: состав гломерул тот же.
    assert np.array_equal(ix1, ix2)
    assert np.allclose(amp2, amp1 * 2.0, rtol=1e-6)


def test_gain_must_be_positive(code):
    for bad in [0.0, -1.0, float("nan"), float("inf")]:
        with pytest.raises(ValueError):
            code.stimulus("knigi", bad)


def test_rejects_impossible_configuration():
    groups = {"ORN_A": np.array([1], dtype=np.int32)}
    with pytest.raises(ValueError):
        OdorCode(groups, glomeruli_per_category=5)
    with pytest.raises(ValueError):
        OdorCode({}, glomeruli_per_category=1)


def test_amplitudes_are_positive_and_finite(code):
    for cid in ["knigi", "kafe", "oplata-qr"]:
        _, amp = code.stimulus(cid, 1.0)
        assert np.isfinite(amp).all()
        assert (amp > 0).all()
