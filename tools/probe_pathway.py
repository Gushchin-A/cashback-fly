"""Этап 1 — по звеньям: где именно теряется запах.

Предыдущий зонд показал, что на выходе эффект стимула нулевой, а сеть шумит
сама по себе. Два возможных объяснения, и их надо развести:

  а) запах не доходит — обонятельный вход слишком слаб или путь обрывается;
  б) запах доходит, но тонет в самоподдерживающейся активности.

Здесь смотрим активность по звеньям обонятельного пути на ЧИСТОМ состоянии:
перед каждым замером граф сбрасывается в покой, так что видно именно отклик
на запах, без наслоения предыдущих окон.

Звенья: обонятельные рецепторы (вход) → антеннальная доля → грибовидное тело
и боковой рог → нисходящие нейроны (выход).

Запуск:
    .venv/bin/python -m tools.probe_pathway
"""

import argparse
import json
from pathlib import Path

import numpy as np

REPORT = Path("reports/stage1-pathway.json")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ms", type=float, default=100.0)
    p.add_argument("--amplitudes", type=float, nargs="+", default=[18.0, 40.0, 80.0])
    p.add_argument("--categories", nargs="+", default=["knigi", "kafe", "igry"])
    a = p.parse_args()

    from neural.flybrain import FlyBrain

    fb = FlyBrain()
    sid = fb.create_state([], room="probe")
    brain = fb._states[sid].brain
    zero = fb._zero_light
    types = fb.types
    sc = brain.superclass

    # Звенья пути. Берутся из разметки, а не перечислением id.
    orn = np.concatenate([fb.glomeruli[g] for g in sorted(fb.glomeruli)])
    stages = {
        "ORN (вход)": orn.astype(np.int32),
        "антеннальная доля": np.flatnonzero(
            types.str.match(r"^(M_|V_|l?v?PN|il3LN|AL)").to_numpy()
        ).astype(np.int32),
        "клетки Кеньона": brain.circuit["kc"],
        "выходы гриб. тела": brain.circuit["mb"],
        "нисходящие (выход)": fb.decision,
        "весь граф": np.arange(brain.n, dtype=np.int32),
    }
    for name, ix in stages.items():
        print(f"{name}: {len(ix)} клеток")

    def measure(stimulation, ms):
        brain.reset()  # чистое состояние: без наслоения предыдущих окон
        counts, _ = brain.step(
            zero, ms, learning=False, stimulation=stimulation,
            lamina_bias=fb.lamina_bias,
        )
        seconds = ms / 1000
        return {
            name: float(counts[ix].sum() / (len(ix) * seconds))
            for name, ix in stages.items()
        }

    result = {"window_ms": a.ms, "rows": []}
    print(f"\nокно {a.ms:.0f} мс, каждый замер с чистого состояния\n")
    header = f"{'условие':28s} " + " ".join(f"{n[:12]:>13s}" for n in stages)
    print(header)
    print("-" * len(header))

    silence = measure(None, a.ms)
    result["rows"].append({"condition": "тишина", "amplitude_mv": 0.0, **silence})
    print(f"{'тишина':28s} " + " ".join(f"{silence[n]:13.2f}" for n in stages))

    for amp in a.amplitudes:
        for cat in a.categories:
            ix, base = fb.odor.stimulus(cat, 1.0)
            scaled = (base / fb.odor.base * amp).astype(np.float32)
            row = measure((ix, scaled), a.ms)
            label = f"{cat} @ {amp:.0f} мВ"
            result["rows"].append(
                {"condition": cat, "amplitude_mv": amp, **row}
            )
            print(f"{label:28s} " + " ".join(f"{row[n]:13.2f}" for n in stages))

    # Разделимость на выходе при каждой амплитуде: разброс между категориями.
    print("\nразброс частоты нисходящих между категориями:")
    summary = []
    for amp in a.amplitudes:
        vals = [
            r["нисходящие (выход)"]
            for r in result["rows"]
            if r["amplitude_mv"] == amp
        ]
        spread = float(np.std(vals, ddof=1)) if len(vals) > 1 else 0.0
        mean = float(np.mean(vals))
        summary.append({"amplitude_mv": amp, "mean_hz": mean, "spread_hz": spread})
        print(
            f"  {amp:5.0f} мВ: среднее {mean:6.2f} Гц, "
            f"разброс {spread:5.3f} Гц ({100 * spread / mean if mean else 0:.1f} %)"
        )
    result["decision_spread_by_amplitude"] = summary

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(f"\nОтчёт: {REPORT}")


if __name__ == "__main__":
    main()
