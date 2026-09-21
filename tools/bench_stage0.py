"""Этап 0 — замер: сколько памяти и времени стоит полный граф MaleCNS v1.0.

Считает три вещи, ради которых этап и существует:
  1. RAM проводки (одна копия, общая для комнат) и RAM одного состояния.
  2. Скорость одного состояния — в кратностях реального нейронного времени.
  3. Скорость пяти состояний — последовательно на одном потоке и параллельно
     в пяти потоках (ctypes отпускает GIL на время вызова ядра).

Режимы стимуляции берутся вилкой, потому что ядро событийное: его скорость
зависит от того, сколько нейронов реально активно.

  idle    — нет входа вообще. Пол по стоимости.
  sparse  — ~2000 сенсорных нейронов под током. Ожидаемый режим кешбэка.
  visual  — полный зрительный вход (белый кадр). Потолок; именно этот режим
            меряли в doomfly, с ним сравниваем цифру 0.2–0.66× на M1 Pro.

Запуск:
    .venv/bin/python -m tools.bench_stage0
"""

import argparse
import json
import platform
import subprocess
import threading
import time
from pathlib import Path

import numpy as np

REPORT = Path("reports/stage0-benchmark.json")


def rss_mb():
    """Текущий RSS процесса в МБ. ps, потому что psutil в зависимостях нет."""
    out = subprocess.run(
        ["ps", "-o", "rss=", "-p", str(__import__("os").getpid())],
        capture_output=True,
        text=True,
        check=True,
    )
    return int(out.stdout.strip()) / 1024


def array_breakdown(obj, names):
    return {
        k: round(getattr(obj, k).nbytes / 1e6, 2)
        for k in names
        if isinstance(getattr(obj, k, None), np.ndarray)
    }


WIRING = ["ptr", "post", "weight", "ids", "retina", "uv", "lamina", "sugar", "superclass"]
STATE = [
    "v", "g", "drive", "previous_drive", "refractory", "queue", "queue_count",
    "counts", "luminance", "active", "active_flag", "last", "eligibility",
    "eligibility_last", "modulation", "modulation_last", "adaptation", "rest",
    "modulation_mask", "tonic", "rate_kc", "rate_dan", "memory_u", "memory_w",
]


def make_stimulus(brain, mode, rng):
    """Вход для замера. Ни одна из этих схем не является выбором категории —
    это только нагрузочный профиль, чтобы измерить стоимость шага."""
    if mode in ("idle", "visual"):
        return None  # visual получает ток из кадра внутри rgb_step
    size = int(mode.split("-")[1])
    # Выборка нужного размера по всему графу: важен объём вызванной активности,
    # а не анатомический смысл. Осмысленный паттерн проектируется на Этапе 1.
    ix = rng.choice(brain.n, size=size, replace=False).astype(np.int32)
    return (ix, np.float32(20.0))


def step_once(brain, mode, ms, stimulus, frame):
    if mode == "visual":
        return brain.rgb_step(frame, ms, learning=False)
    zero = np.zeros(len(brain.retina), dtype=np.float32)
    return brain.step(zero, ms, learning=False, stimulation=stimulus, lamina_bias=0.0)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--rooms", type=int, default=5)
    p.add_argument("--ms", type=float, default=50.0, help="нейронных мс за шаг")
    p.add_argument("--repeats", type=int, default=6)
    a = p.parse_args()

    result = {
        "host": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "cpu": subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True,
            ).stdout.strip(),
            "cores": int(subprocess.run(
                ["sysctl", "-n", "hw.ncpu"], capture_output=True, text=True,
            ).stdout.strip()),
            "ram_gb": round(int(subprocess.run(
                ["sysctl", "-n", "hw.memsize"], capture_output=True, text=True,
            ).stdout.strip()) / 1e9, 1),
            "python": platform.python_version(),
            "numpy": np.__version__,
        },
        "settings": {"rooms": a.rooms, "neural_ms_per_step": a.ms, "repeats": a.repeats},
    }

    base_rss = rss_mb()
    print(f"RSS до загрузки: {base_rss:.0f} МБ", flush=True)

    from neural.visual import VisualMemoryBrain

    t0 = time.perf_counter()
    brains = [VisualMemoryBrain()]
    load_seconds = time.perf_counter() - t0
    first_rss = rss_mb()
    b0 = brains[0]
    # Плазичность выключена: в задаче выбора кешбэка нет сигнала подкрепления,
    # а замороженные веса делают проводку пригодной для общего доступа.
    b0.weights_frozen = True

    wiring = array_breakdown(b0, WIRING)
    state = array_breakdown(b0, STATE)
    result["graph"] = {
        "neurons": int(b0.n),
        "directed_edges": int(len(b0.post)),
        "load_seconds": round(load_seconds, 2),
        "wiring_mb_by_array": wiring,
        "wiring_mb_total": round(sum(wiring.values()), 1),
        "state_mb_by_array": state,
        "state_mb_total": round(sum(state.values()), 1),
        "initial_snapshot_mb": round(
            sum(v.nbytes for v in b0.initial.values()) / 1e6, 1
        ),
        "rss_after_first_brain_mb": round(first_rss, 1),
    }
    print(
        f"Проводка {result['graph']['wiring_mb_total']:.0f} МБ, "
        f"состояние {result['graph']['state_mb_total']:.0f} МБ, "
        f"RSS {first_rss:.0f} МБ",
        flush=True,
    )

    # Каждая следующая комната — отдельный экземпляр. Замеряем реальный прирост.
    growth = []
    for i in range(1, a.rooms):
        before = rss_mb()
        b = VisualMemoryBrain()
        b.weights_frozen = True
        brains.append(b)
        after = rss_mb()
        growth.append(round(after - before, 1))
        print(f"комната {i + 1}: +{after - before:.0f} МБ → RSS {after:.0f} МБ", flush=True)
    result["graph"]["rss_growth_per_extra_room_mb"] = growth
    result["graph"]["rss_all_rooms_mb"] = round(rss_mb(), 1)

    rng = np.random.default_rng(20260920)
    white = np.full((160, 90, 3), 255, dtype=np.uint8)
    result["throughput"] = {}

    modes = ["idle", "stim-128", "stim-512", "stim-2000", "visual"]
    for mode in modes:
        stimuli = [make_stimulus(b, mode, rng) for b in brains]

        # Прогрев: первый шаг платит за компиляцию ядра и прогрев кэшей.
        step_once(brains[0], mode, a.ms, stimuli[0], white)

        single = []
        for _ in range(a.repeats):
            t = time.perf_counter()
            counts, _ = step_once(brains[0], mode, a.ms, stimuli[0], white)
            single.append(time.perf_counter() - t)
        spikes_single = int(counts.sum())

        seq = []
        for _ in range(a.repeats):
            t = time.perf_counter()
            for b, s in zip(brains, stimuli):
                step_once(b, mode, a.ms, s, white)
            seq.append(time.perf_counter() - t)

        par = []
        for _ in range(a.repeats):
            threads = [
                threading.Thread(target=step_once, args=(b, mode, a.ms, s, white))
                for b, s in zip(brains, stimuli)
            ]
            t = time.perf_counter()
            for th in threads:
                th.start()
            for th in threads:
                th.join()
            par.append(time.perf_counter() - t)

        def stats(samples, states):
            median = float(np.median(samples))
            return {
                "median_seconds": round(median, 4),
                "min_seconds": round(min(samples), 4),
                "max_seconds": round(max(samples), 4),
                # >1 означает быстрее реального нейронного времени.
                "realtime_factor": round(a.ms / 1000 / median, 3),
                "neural_ms_per_wall_second": round(states * a.ms / median, 1),
            }

        threaded = stats(par, a.rooms)
        result["throughput"][mode] = {
            "spikes_per_step_one_state": spikes_single,
            "one_state": stats(single, 1),
            f"{a.rooms}_states_sequential": stats(seq, a.rooms),
            f"{a.rooms}_states_threaded": threaded,
            "thread_speedup": round(float(np.median(seq) / np.median(par)), 2),
            # Главное практическое число: сколько нейронного времени успевает
            # набрать КАЖДАЯ комната за 30-секундный цикл, когда все пять
            # считаются параллельно. Реальное время тут не цель — цикл задан ТЗ.
            "neural_ms_per_room_per_30s_cycle": round(
                30.0 / np.median(par) * a.ms, 1
            ),
        }
        m = result["throughput"][mode]
        print(
            f"{mode:9s} спайков/шаг {spikes_single:>8d} | "
            f"1 сост. {m['one_state']['median_seconds'] * 1000:7.1f} мс "
            f"({m['one_state']['realtime_factor']:6.2f}× рт) | "
            f"{a.rooms} посл. {m[f'{a.rooms}_states_sequential']['median_seconds'] * 1000:7.1f} мс | "
            f"{a.rooms} пар. {m[f'{a.rooms}_states_threaded']['median_seconds'] * 1000:7.1f} мс "
            f"(×{m['thread_speedup']:.2f}) | "
            f"{m['neural_ms_per_room_per_30s_cycle']:8.0f} нейро-мс/комната за 30 с",
            flush=True,
        )

    result["graph"]["rss_final_mb"] = round(rss_mb(), 1)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(f"\nОтчёт: {REPORT}", flush=True)


if __name__ == "__main__":
    main()
