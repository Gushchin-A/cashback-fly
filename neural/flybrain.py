"""FlyBrain — выбор категорий кешбэка активностью графа MaleCNS v1.0.

Одна копия проводки, N независимых состояний. Каждое состояние — это комната
со своим набором интересов и своим ходом выбора.

Как категория становится выбором
--------------------------------
1. Категория превращается в обонятельный паттерн (`odor.py`): её id
   детерминированно выбирает подмножество из 53 классов гломерул.
2. Если тег категории совпал с тегом интереса комнаты, амплитуда умножается
   на `interest_gain`. Больше интересы не делают ничего.
3. Паттерн подаётся в граф на окно `window_ms`. Считается активность
   решающего пула — нейронов бокового рога.
4. Рейтинг категории = средняя частота разрядов этого пула за её окна.
5. После `sweeps` полных проходов по всем категориям месяца рейтинги
   фиксируются, и слоты занимаются по убыванию рейтинга — по одному за окно,
   чтобы выбор было видно, а счёт не прерывался.

Почему боковой рог
------------------
Пул выбран замером, а не вкусом: `tools/probe_window.py` сравнил все выходные
популяции. На окне 200 мс боковой рог даёт F = 68,5 при устойчивости порядка
0,944, против 6,3/0,645 у выходов грибовидного тела и 7,4/0,897 у нисходящих.
Это совпадает с ролью бокового рога у настоящей мухи: он отвечает за
врождённую, незаученную оценку запаха. Грибовидное тело — путь выученной
оценки, а наша муха ничему не учится, пластичность выключена.

Решающий пул один и тот же для всех категорий: у категории нет «своих»
нейронов. Поэтому в коде нет и не может быть соответствия интерес→категория.
Совпадение тега поднимает вход, а отклик на этот вход считает граф.

Декодер (пул, метрика, правило слота) зафиксирован и одинаков для всех комнат,
как фиксированный декодер stonkfly. Это инженерный интерфейс, а не открытие
«нейронов кешбэка».
"""

import hashlib
import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .brain import MemoryBrain
from .common import GRAPH, annotations, save_json

# Массивы проводки: общие для всех состояний, во время счёта только читаются.
WIRING = ["ptr", "post", "weight", "ids", "retina", "uv", "lamina", "sugar", "superclass"]


@dataclass
class Readout:
    """Снимок того, что комната насчитала к этому моменту."""

    room: str
    interests: list[str]
    interest_tags: list[str]
    month: str
    # Активность решающего пула: последнее окно и история.
    decision_hz: float
    decision_history: list[float]
    # Накопленный рейтинг по каждой категории месяца, id → средние Гц.
    ratings: dict[str, float]
    # Сколько окон получила каждая категория: рейтинг без этого не читается.
    windows: dict[str, int]
    # Слоты, уже занятые, по порядку фиксации.
    selected: list[str]
    # Текущий топ-5: занятые слоты плюс лидеры среди оставшихся.
    top: list[str]
    # Категория, которая стимулируется прямо сейчас.
    presenting: str | None
    presenting_boosted: bool
    sweep_index: int
    windows_done: int
    windows_total: int
    complete: bool
    # Счётчики.
    total_spikes: int
    window_spikes: int
    sim_ms: float
    # Данные для визуализации: выборка нейронов и их активность.
    sample_ids: list[str] = field(default_factory=list)
    sample_types: list[str] = field(default_factory=list)
    sample_counts: list[int] = field(default_factory=list)
    # Разряды нейронов карты ЦНС, порядок совпадает с cns_map().
    cns_counts: list[int] = field(default_factory=list)
    motor_hz: float = 0.0
    turn_hz: float = 0.0

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["ratings"] = {k: round(v, 4) for k, v in self.ratings.items()}
        d["decision_hz"] = round(self.decision_hz, 4)
        d["decision_history"] = [round(x, 4) for x in self.decision_history]
        d["motor_hz"] = round(self.motor_hz, 4)
        d["turn_hz"] = round(self.turn_hz, 4)
        d["sim_ms"] = round(self.sim_ms, 3)
        return d


class _State:
    """Состояние одной комнаты. Создаётся только через FlyBrain.create_state."""

    def __init__(self, room, interests, interest_tags, brain):
        self.room = room
        self.interests = list(interests)
        self.tags = set(interest_tags)
        self.brain = brain
        self.month = None
        self.order: list[str] = []
        self.schedule: list[str] = []
        self.ratings: dict[str, float] = {}
        self.windows: dict[str, int] = {}
        self.selected: list[str] = []
        self.cursor = 0
        self.sweep_index = 0
        self.windows_done = 0
        self.cycle_index = 0
        self.presenting = None
        self.presenting_boosted = False
        self.window_spikes = 0
        self.decision_hz = 0.0
        self.history: list[float] = []
        self.sample_counts = np.zeros(0, dtype=np.int64)
        self.cns_counts = np.zeros(0, dtype=np.int64)

    def start_month(self, month_id, category_ids, sweeps):
        """Новый месяц: рейтинги и слоты обнуляются, нейронное состояние — нет.

        Граф продолжается непрерывно: муха не перезагружается между месяцами,
        она просто начинает нюхать другой список.
        """
        if not category_ids:
            raise ValueError("В месяце нет ни одной категории")
        self.month = month_id
        self.order = list(category_ids)
        self.ratings = {c: 0.0 for c in self.order}
        self.windows = {c: 0 for c in self.order}
        self.selected = []
        self.cursor = 0
        self.sweep_index = 0
        self.windows_done = 0
        self.presenting = None
        self.schedule = self._make_schedule(sweeps)
        self.cycle_index += 1

    def _make_schedule(self, sweeps):
        """Порядок предъявления: каждый проход перемешивается.

        Перемешивание убирает позиционное смещение — сеть разогревается по ходу
        прохода, и при фиксированном порядке ранние категории систематически
        отличались бы от поздних. Зерно детерминировано, так что расписание
        воспроизводится при восстановлении состояния после перезапуска.
        """
        schedule = []
        for s in range(sweeps):
            seed = hashlib.blake2b(
                f"{self.room}/{self.month}/{self.cycle_index}/{s}".encode(),
                digest_size=8,
            ).digest()
            rng = np.random.default_rng(int.from_bytes(seed, "big"))
            order = list(self.order)
            rng.shuffle(order)
            schedule.extend(order)
        return schedule

    def remaining(self):
        return [c for c in self.order if c not in self.selected]

    def mean_rating(self, category):
        return self.ratings[category] / max(1, self.windows[category])


class FlyBrain:
    """Общий граф и N состояний комнат."""

    def __init__(
        self,
        connectome_path=GRAPH,
        *,
        config: dict | None = None,
        categories: dict | None = None,
        interests: dict | None = None,
    ):
        self.config = config or load_config("config/simulation.json")
        self.categories = categories or load_config("config/categories.json")
        self.interests = interests or load_config("config/interests.json")
        s = self.config["stimulus"]
        n = self.config["neural"]
        self.slots = int(self.config["slots"])
        self.sweeps = int(self.config["sweeps"])
        self.window_ms = float(n["window_ms"])
        self.reveal_window_ms = float(n["reveal_window_ms"])
        self.lamina_bias = float(n["lamina_bias_mv"])
        self.learning = bool(n["learning"])
        self.interest_gain = float(s["interest_gain"])
        if not 1 <= self.slots <= 10:
            raise ValueError("slots должен быть 1..10")
        if not 1 <= self.sweeps <= 20:
            raise ValueError("sweeps должен быть 1..20")
        if not math.isfinite(self.interest_gain) or self.interest_gain < 1:
            raise ValueError("interest_gain должен быть >= 1")

        started = time.perf_counter()
        self._template = MemoryBrain(connectome_path)
        self._template.weights_frozen = not self.learning
        self.n = int(self._template.n)
        self.load_seconds = time.perf_counter() - started

        a = annotations(self._template.ids)
        self._annotations = a
        self.types = a.type.fillna("").astype(str)
        self._soma_side = a.somaSide.fillna("").astype(str)

        from .odor import OdorCode, glomeruli_from_annotations

        self.glomeruli = glomeruli_from_annotations(self.types)
        self.odor = OdorCode(
            self.glomeruli,
            glomeruli_per_category=int(s["glomeruli_per_category"]),
            base_amplitude_mv=float(s["base_amplitude_mv"]),
            amplitude_jitter=float(s["amplitude_jitter"]),
            namespace=s["seed_namespace"],
        )

        # Решающий пул задаётся разметкой, а не перечислением id в коде.
        self.decision = self._resolve_pool(self.config["readout"]["decision_pool"])

        # Моторные показания для 3D: те же типы, что использует FlyTok.
        self.motor = np.flatnonzero(
            self.types.isin(["MN9", "DNp09"]).to_numpy()
        ).astype(np.int32)
        self.turn_left = np.flatnonzero(
            (self.types == "DNa02").to_numpy() & (self._soma_side == "L").to_numpy()
        ).astype(np.int32)
        self.turn_right = np.flatnonzero(
            (self.types == "DNa02").to_numpy() & (self._soma_side == "R").to_numpy()
        ).astype(np.int32)

        self.sample = self._build_sample(int(self.config["readout"]["visual_sample"]))
        self.cns = self._build_cns_map(int(self.config["readout"].get("cns_sample", 4000)))
        self._zero_light = np.zeros(len(self._template.retina), dtype=np.float32)
        self._states: dict[int, _State] = {}
        self._next_id = 0

    def _resolve_pool(self, spec):
        """Найти решающий пул по разметке коннектома.

        `kind` = "type_prefix" — по префиксу типа клетки (LH, MBON);
        `kind` = "superclass" — по суперклассу (descending_neuron).
        """
        kind, value = spec["kind"], spec["value"]
        if kind == "type_prefix":
            ix = np.flatnonzero(self.types.str.startswith(value).to_numpy())
        elif kind == "superclass":
            ix = np.flatnonzero(self._template.superclass == value)
        else:
            raise ValueError(f"Неизвестный вид пула: {kind}")
        if len(ix) < 50:
            raise ValueError(f"Решающий пул {kind}={value} пуст или мал: {len(ix)}")
        return ix.astype(np.int32)

    # ---------- построение состояний ----------

    def _build_sample(self, size):
        """Детерминированная выборка нейронов для картинки активности.

        Смесь входа, решающего пула и моторики, чтобы на экране было видно
        и стимул, и отклик, а не один сплошной фон.
        """
        picks = []
        for g in sorted(self.glomeruli)[:16]:
            picks.extend(self.glomeruli[g][:2].tolist())
        picks.extend(self.decision[:: max(1, len(self.decision) // 32)][:32].tolist())
        picks.extend(self.motor[:16].tolist())
        picks.extend(self.turn_left[:4].tolist())
        picks.extend(self.turn_right[:4].tolist())
        unique = list(dict.fromkeys(int(i) for i in picks))
        if len(unique) < size:
            filler = np.linspace(0, self.n - 1, size).astype(int)
            unique = list(dict.fromkeys(unique + [int(i) for i in filler]))
        return np.asarray(unique[:size], dtype=np.int32)

    def _build_cns_map(self, size: int) -> dict:
        """Карта сом для снимка ЦНС: реальные координаты, а не рисунок.

        MaleCNS даёт положение сомы у 84 % нейронов графа (`somaLocation`,
        воксели). Берём выборку, разделяем на головной мозг и брюшную нервную
        цепочку по суперклассу и проецируем на плоскость.

        Позиции статичны, поэтому уезжают клиенту один раз отдельным файлом;
        в снимке едут только разряды этих же нейронов, в том же порядке.
        """
        soma = self._annotations.somaLocation
        present = soma.notna().to_numpy()
        if not present.any():
            return {"indices": np.zeros(0, dtype=np.int32), "points": []}

        superclass = self._template.superclass
        is_vnc = np.char.startswith(superclass.astype(str), "vnc")

        rng = np.random.default_rng(20260920)
        chosen: list[np.ndarray] = []
        # Доли выборки пропорциональны размеру отделов, но брюшной цепочке
        # даём минимум, иначе она вырождается в горстку точек.
        for mask, share in ((present & ~is_vnc, 0.72), (present & is_vnc, 0.28)):
            pool = np.flatnonzero(mask)
            take = min(len(pool), max(1, int(size * share)))
            chosen.append(rng.choice(pool, size=take, replace=False))
        indices = np.sort(np.concatenate(chosen)).astype(np.int32)

        xyz = np.stack(soma.iloc[indices].to_numpy()).astype(np.float64)
        parts = np.where(is_vnc[indices], "vnc", "brain")

        points = []
        # Оси выбраны по плотности облака, а не наугад: у мозга фронтальный
        # вид X-Y даёт заполнение 70 % при пропорции 1,42, у брюшной цепочки
        # вид Z-Y — вытянутый силуэт 2,5:1. Другие пары дают рыхлые пятна.
        axes = {"brain": (0, 1), "vnc": (2, 1)}
        for part in ("brain", "vnc"):
            mask = parts == part
            if not mask.any():
                continue
            flat = xyz[mask][:, list(axes[part])]
            lo, hi = flat.min(axis=0), flat.max(axis=0)
            span = np.where(hi - lo > 0, hi - lo, 1.0)
            unit = (flat - lo) / span
            points.append({
                "part": part,
                "slots": np.flatnonzero(mask).astype(int).tolist(),
                "x": np.round(unit[:, 0], 4).tolist(),
                "y": np.round(1 - unit[:, 1], 4).tolist(),
            })

        return {
            "indices": indices,
            "points": points,
            "total_with_soma": int(present.sum()),
            "neurons": int(len(indices)),
        }

    def cns_map(self) -> dict:
        """Статическая карта для клиента: координаты и подписи отделов."""
        return {
            "schema": 1,
            "neurons": self.cns["neurons"],
            "total_with_soma": self.cns["total_with_soma"],
            "parts": [
                {
                    "id": p["part"],
                    "title": "Головной мозг" if p["part"] == "brain" else "Брюшная нервная цепочка",
                    "slots": p["slots"],
                    "x": p["x"],
                    "y": p["y"],
                }
                for p in self.cns["points"]
            ],
            "source": (
                "Положение сомы из MaleCNS v1.0 (поле somaLocation), проекция "
                "на вид сбоку. Координаты реальные; яркость — измеренные разряды."
            ),
        }

    def create_state(self, interests: list[str], *, room: str | None = None) -> int:
        """Поднять состояние комнаты. Возвращает StateId."""
        known = self.interests["interests"]
        unknown = [i for i in interests if i not in known]
        if unknown:
            raise ValueError(f"Неизвестные интересы: {unknown}")
        tags = sorted({t for i in interests for t in known[i]["tags"]})

        if not self._states:
            brain = self._template  # первое состояние занимает шаблон
        else:
            brain = MemoryBrain(
                GRAPH,
                circuit=self._template.circuit,
                modulation_mask=self._template.modulation_mask,
            )
            brain.weights_frozen = not self.learning
            # Проводка одна на всех: сбросить дубликаты, созданные загрузкой.
            # Веса заморожены, во время счёта эти массивы только читаются.
            for k in WIRING:
                setattr(brain, k, getattr(self._template, k))

        state_id = self._next_id
        self._next_id += 1
        self._states[state_id] = _State(
            room or f"room{state_id}", interests, tags, brain
        )
        return state_id

    def _state(self, state_id) -> _State:
        if state_id not in self._states:
            raise KeyError(f"Нет состояния {state_id}")
        return self._states[state_id]

    # ---------- цикл ----------

    def start_month(self, state_id, month_id):
        month = self.month(month_id)
        self._state(state_id).start_month(
            month["id"], [o["category"] for o in month["offers"]], self.sweeps
        )

    def month(self, month_id):
        for m in self.categories["months"]:
            if m["id"] == month_id:
                return m
        raise KeyError(f"Нет месяца {month_id}")

    @property
    def month_ids(self):
        return [m["id"] for m in self.categories["months"]]

    def next_month(self, month_id=None):
        """Следующий месяц в кольце. Длина кольца — из конфига, не из кода.

        Кольцо определено здесь, а не в воркере, чтобы число месяцев нигде не
        оказалось зашито: добавление четвёртого, пятого или двенадцатого
        месяца в `categories.json` не требует правок логики.
        """
        ids = self.month_ids
        if not ids:
            raise ValueError("В конфиге нет ни одного месяца")
        if month_id is None:
            return ids[0]
        if month_id not in ids:
            raise KeyError(f"Нет месяца {month_id}")
        return ids[(ids.index(month_id) + 1) % len(ids)]

    def is_boosted(self, state_id, category_id) -> bool:
        """Попадает ли категория в интересы комнаты."""
        tags = set(self.categories["categories"][category_id]["tags"])
        return bool(tags & self._state(state_id).tags)

    def step(self, state_id, categories=None) -> None:
        """Проинтегрировать одно окно: либо предъявление, либо занятие слота.

        Цикл состоит из двух фаз. Пока расписание не исчерпано — идёт
        накопление: очередная категория нюхается и её рейтинг растёт. Когда
        расписание кончилось, рейтинги фиксированы, и каждое следующее окно
        занимает один слот по убыванию рейтинга. Счёт при этом не прерывается:
        во время фиксации граф продолжает нюхать ту категорию, которая слот и
        занимает, — выбор видно, а симуляция остаётся непрерывной.

        `categories` задаёт список месяца. Если он передан и отличается от
        текущего, цикл начинается заново. Обычно воркер вызывает `start_month`
        и дальше зовёт `step` без аргументов.
        """
        st = self._state(state_id)
        if categories is not None and list(categories) != st.order:
            st.start_month(st.month or "custom", list(categories), self.sweeps)
        if not st.order:
            raise RuntimeError("Месяц не начат: вызовите start_month")
        if self.cycle_complete(state_id):
            st.presenting = None
            return

        accumulating = st.cursor < len(st.schedule)
        if accumulating:
            category = st.schedule[st.cursor]
            window_ms = self.window_ms
        else:
            # Фаза фиксации: слот занимает лидер среди ещё не занятых.
            category = max(st.remaining(), key=st.mean_rating)
            window_ms = self.reveal_window_ms

        boosted = self.is_boosted(state_id, category)
        gain = self.interest_gain if boosted else 1.0
        indices, amplitudes = self.odor.stimulus(category, gain)

        counts, _ = st.brain.step(
            self._zero_light,
            window_ms,
            learning=self.learning,
            stimulation=(indices, amplitudes),
            lamina_bias=self.lamina_bias,
        )

        seconds = window_ms / 1000
        hz = float(counts[self.decision].sum() / (len(self.decision) * seconds))
        if accumulating:
            # Рейтинг растёт только в фазе накопления: окна фиксации не должны
            # менять порядок, который они же и оглашают.
            st.ratings[category] += hz
            st.windows[category] += 1
            st.cursor += 1
            st.sweep_index = st.cursor // max(1, len(st.order))
        else:
            st.selected.append(category)

        st.decision_hz = hz
        st.history.append(hz)
        del st.history[:-240]
        st.window_spikes = int(counts.sum())
        st.sample_counts = counts[self.sample].astype(np.int64)
        st.cns_counts = counts[self.cns["indices"]].astype(np.int64)
        st.presenting = category
        st.presenting_boosted = boosted
        st.windows_done += 1
        st._last_counts = counts

    def cycle_complete(self, state_id) -> bool:
        st = self._state(state_id)
        return bool(st.order) and len(st.selected) >= min(self.slots, len(st.order))

    def windows_per_cycle(self, state_id) -> int:
        """Сколько окон занимает полный цикл. Нужно воркеру для темпа."""
        st = self._state(state_id)
        return len(st.schedule) + min(self.slots, len(st.order))

    # ---------- считывание ----------

    def readout(self, state_id) -> Readout:
        st = self._state(state_id)
        mean = {c: st.ratings[c] / max(1, st.windows[c]) for c in st.order}
        rest = sorted(
            (c for c in st.order if c not in st.selected),
            key=lambda c: mean[c],
            reverse=True,
        )
        counts = getattr(st, "_last_counts", None)
        seconds = self.window_ms / 1000

        def rate(ix):
            if counts is None or not len(ix):
                return 0.0
            return float(counts[ix].sum() / (len(ix) * seconds))

        return Readout(
            room=st.room,
            interests=list(st.interests),
            interest_tags=sorted(st.tags),
            month=st.month or "",
            decision_hz=st.decision_hz,
            decision_history=list(st.history),
            ratings=dict(mean),
            windows=dict(st.windows),
            selected=list(st.selected),
            top=(st.selected + rest)[: self.slots],
            presenting=st.presenting,
            presenting_boosted=st.presenting_boosted,
            sweep_index=st.sweep_index,
            windows_done=st.windows_done,
            windows_total=self.windows_per_cycle(state_id),
            complete=self.cycle_complete(state_id),
            total_spikes=int(st.brain.total_spikes),
            window_spikes=int(st.window_spikes),
            sim_ms=float(st.brain.sim_ms),
            sample_ids=[str(st.brain.ids[i]) for i in self.sample],
            sample_types=[self.types.iloc[i] for i in self.sample],
            sample_counts=st.sample_counts.tolist(),
            cns_counts=st.cns_counts.tolist(),
            motor_hz=rate(self.motor),
            turn_hz=rate(self.turn_right) - rate(self.turn_left),
        )

    # ---------- сохранение и восстановление ----------

    def save_state(self, state_id, directory) -> None:
        """Сохранить состояние комнаты: нейронное и ход выбора.

        Пишется атомарно. Нейронное состояние идёт в чекпойнт stonkfly
        (он же проверяет провенанс при восстановлении), ход цикла — рядом
        в JSON. Одно без другого бессмысленно, поэтому JSON пишется вторым:
        если процесс умрёт между ними, при старте не найдётся ход цикла и
        комната честно начнёт месяц заново, а не подхватит рассогласованный.
        """
        st = self._state(state_id)
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        st.brain.checkpoint(directory / "brain.npz")
        cycle = {
            "room": st.room,
            "interests": st.interests,
            "month": st.month,
            "order": st.order,
            "schedule": st.schedule,
            "ratings": st.ratings,
            "windows": st.windows,
            "selected": st.selected,
            "cursor": st.cursor,
            "sweep_index": st.sweep_index,
            "cycle_index": st.cycle_index,
            "windows_done": st.windows_done,
            "history": st.history,
            "interest_gain": self.interest_gain,
            "sweeps": self.sweeps,
            "slots": self.slots,
        }
        save_json(directory / "cycle.json", cycle)

    def load_state(self, state_id, directory) -> bool:
        """Восстановить состояние. False, если восстанавливать нечего.

        Несовпадение настроек — не ошибка, а причина начать заново: старые
        рейтинги считались другим усилением и сравнивать их с новыми нельзя.
        """
        directory = Path(directory)
        brain_path = directory / "brain.npz"
        cycle_path = directory / "cycle.json"
        if not brain_path.exists() or not cycle_path.exists():
            return False
        cycle = json.loads(cycle_path.read_text(encoding="utf-8"))
        st = self._state(state_id)
        if (
            cycle.get("room") != st.room
            or cycle.get("interests") != st.interests
            or cycle.get("interest_gain") != self.interest_gain
            or cycle.get("sweeps") != self.sweeps
            or cycle.get("slots") != self.slots
        ):
            return False
        if cycle.get("month") not in self.month_ids:
            return False
        # Чекпойнт сам проверит, что граф, параметры и сборка ядра те же.
        st.brain.restore(brain_path)
        st.month = cycle["month"]
        st.order = list(cycle["order"])
        st.schedule = list(cycle["schedule"])
        st.ratings = dict(cycle["ratings"])
        st.windows = dict(cycle["windows"])
        st.selected = list(cycle["selected"])
        st.cursor = int(cycle["cursor"])
        st.sweep_index = int(cycle["sweep_index"])
        st.cycle_index = int(cycle["cycle_index"])
        st.windows_done = int(cycle["windows_done"])
        st.history = list(cycle["history"])
        return True

    # ---------- провенанс ----------

    def provenance(self) -> dict:
        """Чем именно считали. Идёт в снимок и в отчёты."""
        source = Path(__file__).parent
        return {
            "release": "MaleCNS v1.0",
            "neurons": self.n,
            "directed_edges": int(len(self._template.post)),
            "glomeruli": len(self.glomeruli),
            "olfactory_neurons": int(sum(len(v) for v in self.glomeruli.values())),
            "cns_map_neurons": int(self.cns["neurons"]),
            "decision_pool": self.config["readout"]["decision_pool"],
            "decision_pool_size": int(len(self.decision)),
            "decoder": (
                "Накопленная средняя частота разрядов нисходящих нейронов за окна "
                "категории; лидер прохода занимает слот. Фиксированный инженерный "
                "интерфейс, не обнаружение «нейронов кешбэка»."
            ),
            "interest_mechanism": (
                "Совпадение тега поднимает амплитуду стимула в interest_gain раз. "
                "Других путей влияния интересов на выбор нет."
            ),
            "interest_gain": self.interest_gain,
            "window_ms": self.window_ms,
            "learning": self.learning,
            "config_sha256": hashlib.sha256(
                json.dumps(self.config, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest(),
            "source_sha256": {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(source.glob("*.py"))
            },
        }


def load_config(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def enabled_rooms(path="config/rooms.json"):
    """Комнаты, которые воркер должен поднять. Число комнат — из конфига."""
    return [r for r in load_config(path)["rooms"] if r.get("enabled")]
