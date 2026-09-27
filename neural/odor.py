"""Детерминированное кодирование категории в обонятельный паттерн.

Категория кешбэка не имеет запаха. Чтобы граф вообще мог на неё отреагировать,
ей нужен вход, и вход должен быть сенсорным — иначе стимуляция бьёт в середину
сети, где у неё нет физиологического смысла.

MaleCNS v1.0 размечает 2635 обонятельных рецепторных нейронов, сгруппированных
в 53 класса по гломерулам антеннальной доли. Настоящая муха кодирует запах
именно так: комбинацией активных гломерул с разной силой. Мы пользуемся этим
алфавитом: id категории детерминированно (blake2b) выбирает подмножество
гломерул и силу в каждой.

Что здесь НЕ происходит: паттерн ничего не знает ни о смысле категории, ни о
её проценте кешбэка, ни о том, какую категорию нужно выбрать. Соответствие
"id → гломерулы" — это произвольная, но воспроизводимая раскладка, ровно как
назначение цвета хешем. Решает дальше граф.

Интересы комнаты входят сюда одним-единственным способом: если у категории
есть тег, совпадающий с тегом интереса, амплитуда умножается на коэффициент
из конфига. Набор гломерул при этом не меняется — интерес делает запах
громче, а не другим.
"""

import hashlib

import numpy as np

NAMESPACE_DEFAULT = "fly-cashback/odor/v1"


def category_seed(category_id: str, namespace: str = NAMESPACE_DEFAULT) -> int:
    """Вечное 64-битное зерно категории.

    blake2b, а не hash(): встроенный hash() рандомизируется между запусками
    процесса, и запах категории менялся бы при каждом рестарте воркера.
    """
    digest = hashlib.blake2b(
        f"{namespace}/{category_id}".encode(), digest_size=8
    ).digest()
    return int.from_bytes(digest, "big")


class OdorCode:
    """Раскладка категорий по гломерулам для конкретного графа."""

    def __init__(
        self,
        glomeruli: dict[str, np.ndarray],
        *,
        glomeruli_per_category: int = 8,
        base_amplitude_mv: float = 18.0,
        amplitude_jitter: float = 0.35,
        namespace: str = NAMESPACE_DEFAULT,
    ):
        if not glomeruli:
            raise ValueError("Нет ни одного обонятельного класса")
        if not 1 <= glomeruli_per_category <= len(glomeruli):
            raise ValueError(
                f"glomeruli_per_category должен быть 1..{len(glomeruli)}"
            )
        if not 0 < base_amplitude_mv <= 100:
            raise ValueError("Амплитуда вне разумного диапазона 0..100 мВ")
        if not 0 <= amplitude_jitter < 1:
            raise ValueError("amplitude_jitter должен быть в [0, 1)")
        self.names = sorted(glomeruli)
        self.glomeruli = glomeruli
        self.k = glomeruli_per_category
        self.base = float(base_amplitude_mv)
        self.jitter = float(amplitude_jitter)
        self.namespace = namespace
        self._cache: dict[str, tuple[np.ndarray, np.ndarray]] = {}

    def pattern(self, category_id: str) -> tuple[np.ndarray, np.ndarray]:
        """Индексы нейронов и амплитуды при единичном усилении.

        Возвращает копии: вызывающая сторона масштабирует амплитуду под
        интересы комнаты и не должна портить кеш.
        """
        if category_id not in self._cache:
            rng = np.random.default_rng(category_seed(category_id, self.namespace))
            chosen = rng.choice(len(self.names), size=self.k, replace=False)
            indices, amplitudes = [], []
            for g in chosen:
                cells = self.glomeruli[self.names[g]]
                # Сила запаха в гломеруле: base с детерминированным разбросом.
                # Разброс нужен, чтобы два запаха с общей гломерулой всё же
                # различались по профилю, а не только по составу.
                strength = self.base * (1 + self.jitter * (2 * rng.random() - 1))
                indices.append(cells)
                amplitudes.append(np.full(len(cells), strength, dtype=np.float32))
            self._cache[category_id] = (
                np.concatenate(indices).astype(np.int32),
                np.concatenate(amplitudes).astype(np.float32),
            )
        indices, amplitudes = self._cache[category_id]
        return indices.copy(), amplitudes.copy()

    def stimulus(self, category_id: str, gain: float = 1.0):
        """Пара (индексы, амплитуды) в формате, который принимает ядро."""
        if not np.isfinite(gain) or gain <= 0:
            raise ValueError("Усиление должно быть конечным и положительным")
        indices, amplitudes = self.pattern(category_id)
        return indices, (amplitudes * np.float32(gain)).astype(np.float32)

    def describe(self, category_id: str) -> dict:
        """Какие гломерулы задействованы — для отчётов и провенанса."""
        rng = np.random.default_rng(category_seed(category_id, self.namespace))
        chosen = rng.choice(len(self.names), size=self.k, replace=False)
        indices, _ = self.pattern(category_id)
        return {
            "category": category_id,
            "seed": category_seed(category_id, self.namespace),
            "glomeruli": [self.names[g] for g in chosen],
            "neurons": int(len(indices)),
        }


def glomeruli_from_annotations(cell_types, prefix: str = "ORN_") -> dict:
    """Сгруппировать обонятельные рецепторные нейроны по классу гломерулы.

    cell_types — поданные по порядку узлов графа типы клеток; возвращаются
    индексы внутри графа, а не body id.
    """
    types = cell_types.fillna("").astype(str)
    mask = types.str.startswith(prefix).to_numpy()
    if not mask.any():
        raise ValueError(f"В аннотациях нет клеток с префиксом {prefix}")
    groups: dict[str, np.ndarray] = {}
    for name in sorted(set(types[mask])):
        groups[name] = np.flatnonzero((types == name).to_numpy()).astype(np.int32)
    return groups
