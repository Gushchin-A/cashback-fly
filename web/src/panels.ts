/**
 * Панели: категории, карта ЦНС, комната с интересами и графиком.
 *
 * Панели ничего не решают. Всё, что они показывают, приходит из снимка:
 * галочки — из `selection.selected`, подсветка интереса — из тегов уже
 * выбранной категории, цифры и точки — из `activity`. Декоративных чисел
 * и «додумывания» выбора на клиенте нет.
 */

import { applyPixelIcon } from "./pixelate";
import type { CnsMap, Interest, Offer, Snapshot } from "./types";

/**
 * Иконки категорий. Есть не у всех — остальные показываются заглушкой.
 * Имена файлов совпадают с id категории.
 */
let iconIndex: Record<string, string> = {};
void fetch("/icons/index.json")
  .then((r) => (r.ok ? r.json() : {}))
  .then((data) => {
    iconIndex = data as Record<string, string>;
  })
  .catch(() => {
    /* без иконок список работает, это не повод падать */
  });

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
  text?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

const hz = (value: number, digits = 1) => value.toFixed(digits).replace(".", ",");
const thousands = (value: number) => value.toLocaleString("ru-RU");

/**
 * Строка вида «подпись ЗНАЧЕНИЕ · подпись ЗНАЧЕНИЕ …» с двумя шрифтами:
 * подпись обычным моно, значение — пиксельным. Разделитель между парами
 * идёт обычным шрифтом подписи, чтобы точка между парами не «прыгала».
 */
function serviceLine(pairs: [label: string, value: string][]): Node[] {
  const nodes: Node[] = [];
  pairs.forEach(([label, value], i) => {
    if (i > 0) nodes.push(document.createTextNode(" · "));
    nodes.push(el("span", "service__label", label));
    nodes.push(el("span", "service__value", value));
  });
  return nodes;
}

/** «3 категории» / «5 категорий» — падеж по числу. */
function plural(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

/** Пауза между появлением галочек, чтобы пояснение успевало читаться. */
const REVEAL_GAP_MS = 1200;
/** Смена месяца: уход вверх, приход сверху. */
const SWAP_OUT_MS = 420;
const SWAP_IN_MS = 420;

// ---------- категории ----------

export class CategoriesPanel {
  readonly root = el("section", "panel panel--categories");
  private readonly heading = el("h2", "panel__title");
  private readonly counter = el("div", "cats__counter");
  private readonly card = el("div", "cats__card");
  private readonly list = el("ol", "cats");
  /**
   * Галочки показываются с задержкой после реального события в снимке.
   *
   * Воркер иногда занимает слоты подряд быстрее, чем зритель успевает
   * прочитать пояснение. Механику это не трогает: слот в снимке уже занят,
   * отложен только момент, когда галочка появляется на экране.
   */
  private shown: string[] = [];
  private nextRevealAt = 0;
  private month = "";
  /**
   * Идёт подмена списка — рендер списка приостановлен.
   *
   * Держится до РЕАЛЬНОЙ смены месяца в снимке, а не фиксированное время:
   * пауза воркера длится `pause_seconds` (в конфиге — 12 с), и если анимация
   * заканчивалась раньше по таймеру, рендер возобновлялся со старым месяцем
   * — старые галочки на пару секунд проступали поверх ещё не обновившихся
   * данных. Раз список не трогается, пока swapping истинно, такого кадра
   * больше не бывает: он появится только когда придут новые offers/selected.
   */
  private swapping = false;
  /** Сколько строк было в последнем реальном месяце — под это рисуем скелет. */
  private rowCount = 12;

  constructor() {
    const card = el("div", "cats__with");
    card.append(
      el("span", "cats__with-text", "С вашей картой"),
      el("span", "yapay", "Я"),
      el("span", "cats__with-text", "Пэй"),
    );
    this.card.append(this.list);
    this.root.append(this.heading, this.counter, card, this.card);
  }

  render(snapshot: Snapshot) {
    const { slots } = snapshot.selection;
    this.advanceReveal(snapshot);
    // Месяц в заголовке не пишем: он меняется, а заголовок должен стоять.
    this.heading.textContent = "Категории кешбэка на месяц";
    this.counter.textContent = `Выбрано ${this.shown.length} из ${slots}`;

    const chosen = new Set(this.shown);
    // После последней, пятой отметки заливка строки пропадает сразу —
    // иначе кажется, что муха продолжает перебирать список, хотя выбор уже
    // завершён. Для 1–4 отметок подсветка остаётся как была.
    const presenting = this.shown.length >= slots ? null : snapshot.presenting?.id ?? null;
    if (this.swapping) {
      // Пока идёт смена месяца, настоящий список не трогаем — на экране
      // либо анимация ухода, либо скелет, поставленный в press().
      return;
    }
    this.rowCount = snapshot.offers.length;
    this.list.replaceChildren(
      ...snapshot.offers.map((offer) => this.row(offer, chosen, presenting)),
    );
  }

  /** Показаны ли зрителю все занятые слоты. Пока нет — жать рано. */
  allShown(snapshot: Snapshot): boolean {
    return (
      snapshot.selection.complete &&
      this.shown.length >= snapshot.selection.slots &&
      !this.swapping
    );
  }

  private advanceReveal(snapshot: Snapshot) {
    const selected = snapshot.selection.selected;
    const month = snapshot.month?.id ?? "";
    if (month !== this.month) {
      // Настоящая смена месяца пришла из снимка: показ начинается заново.
      this.month = month;
      this.shown = [];
      this.nextRevealAt = 0;
      // Если это случилось во время swapping — вот он, сигнал закончить
      // подмену: ждали именно этого, а не истечения таймера.
      if (this.swapping) this.finishSwap();
    }
    // Пока идёт подмена, копить reveal нет смысла: список всё равно скрыт,
    // а как только swapping снимется, старт будет с нуля.
    if (this.swapping) return;

    // Откат (например, воркер перезапустился) — подчиняемся снимку.
    if (selected.length < this.shown.length) this.shown = selected.slice();

    const now = performance.now();
    if (this.shown.length < selected.length && now >= this.nextRevealAt) {
      this.shown = selected.slice(0, this.shown.length + 1);
      this.nextRevealAt = now + REVEAL_GAP_MS;
    }
  }

  private finishSwap() {
    this.swapping = false;
    delete this.card.dataset.loading;
    this.card.dataset.entering = "";
    window.setTimeout(() => delete this.card.dataset.entering, SWAP_IN_MS);
  }

  /**
   * Смена месяца. Кнопки нет намеренно: муха не пользователь и ничего не
   * «нажимает», поэтому список просто уезжает, на его месте встаёт скелет
   * — и держится ровно до тех пор, пока в снимке не пришёл настоящий
   * следующий месяц (это может быть несколько секунд, это нормально:
   * воркер объявляет цикл закрытым не мгновенно).
   */
  press() {
    if (this.swapping) return;
    this.swapping = true;
    this.card.dataset.leaving = "";
    window.setTimeout(() => {
      delete this.card.dataset.leaving;
      this.card.dataset.loading = "";
      // Скелет тех же размеров, что настоящий список — так это делают в
      // реальных интерфейсах (лента, список), а не подписью по центру
      // пустого места. Живёт, пока swapping не снимет advanceReveal(),
      // увидев реальную смену snapshot.month.id.
      this.list.replaceChildren(...this.buildSkeleton());
    }, SWAP_OUT_MS);
  }

  private buildSkeleton(): HTMLLIElement[] {
    return Array.from({ length: this.rowCount }, () => {
      const item = el("li", "cat cat--skeleton");
      item.append(
        el("span", "cat__icon cat__icon--skeleton"),
        el("span", "cat__title cat__title--skeleton"),
        el("span", "cat__box cat__box--skeleton"),
      );
      return item;
    });
  }

  private row(offer: Offer, chosen: Set<string>, presenting: string | null) {
    const item = el("li", "cat");
    item.dataset.id = offer.id;
    if (chosen.has(offer.id)) item.dataset.selected = "";
    if (offer.id === presenting) item.dataset.presenting = "";
    if (offer.boosted) item.dataset.boosted = "";

    const icon = el("span", "cat__icon");
    // Иконка проходит тот же пиксельный фильтр, что и экран телефона,
    // иначе гладкие значки спорят с пиксель-артом остальной сцены.
    const iconSrc = iconIndex[offer.id];
    if (iconSrc) applyPixelIcon(icon, iconSrc);
    // Настоящий чекбокс: галочка рисуется всегда, а не цветной квадрат без
    // символа — цвет заливки один, состояние читается по форме, а не только
    // по цвету (важно и для доступности, не только для вида).
    const box = el("span", "cat__box");
    box.innerHTML =
      '<svg viewBox="0 0 16 16" width="12" height="12" fill="none" ' +
      'aria-hidden="true"><path d="M3 8.5L6.2 11.5L13 4" ' +
      'stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
      'stroke-linejoin="round"/></svg>';
    box.setAttribute("role", "img");
    box.setAttribute(
      "aria-label",
      chosen.has(offer.id) ? "выбрано мухой" : "не выбрано",
    );

    // Описания категорий убраны намеренно: они отвлекали от главного —
    // что именно муха выбрала и почему.
    const title = el("span", "cat__title", `${offer.rate}% ${offer.title}`);
    item.append(icon, title, box);
    return item;
  }
}

// ---------- карта ЦНС ----------

/**
 * Тепловая шкала активности: светло-голубой (покой) → голубой → лайм
 * (средняя активность) → жёлто-оранжевый (пик). Один нейрон движется по
 * ней целиком в зависимости от своей текущей активности, а не от того,
 * к какому анатомическому отделу он относится — см. вызов в draw().
 * Нижняя ступень — светло-голубой/белёсый, как в самой первой версии
 * (была rgba(108,132,158)), а не тёмно-синий: у референса
 * (flyhard/src/flyhard/cns_view.py, MIT, github.com/MarkUnthank/flyhard)
 * покой тоже держится на видимом сером, а не проваливается в фон.
 */
const HEAT_STOPS: [number, [number, number, number]][] = [
  [0.0, [165, 185, 210]],
  [0.3, [70, 155, 220]],
  [0.55, [120, 220, 110]],
  [0.78, [255, 225, 60]],
  [1.0, [255, 130, 30]],
];

/**
 * Сжатие через arcsinh — та же техника, что в cns_view.py: гораздо мягче
 * к средним значениям, чем `value ** p`.
 *
 * ВАЖНО: нормируем не на пик кадра (общий для всех нейронов), а на
 * персональную шкалу каждого нейрона — его собственный p95 по накопленной
 * истории. Так делает и cns_view.py, с прямой причиной в их комментарии:
 * общая шкала «прячет более слабые ответы мозга за намного большими
 * значениями VNC». У нас та же проблема была иначе: пик кадра почти
 * всегда — один из десятка структурных хабов графа (проверено по
 * входящей степени связности на самих данных коннектома: у топ-активных
 * нейронов медиана входящих связей 561 против 133 по всей карте, то есть
 * они получают вход почти от любого стимула). Нормируя на общий пик, вся
 * картина двигалась вместе с этими хабами — 91% пересечение топ-40 самых
 * ярких нейронов между кадрами разных категорий. Персональная шкала даёт
 * 15% — то есть картина реально перестраивается под категорию, а не носит
 * маску одного и того же хаба.
 *
 * Плата за это, как и у референса: значения разных нейронов больше не
 * сравнимы напрямую (comparable_between_neurons: false у них) — шкала у
 * каждого своя. Для этой панели (общее ощущение живости, не точная шкала)
 * это приемлемо.
 */
const ASINH_M = Math.asinh(20);
/** Сколько последних активаций нейрона помним для оценки его p95. */
const SCALE_HISTORY_LEN = 300;

function heatColor(t: number): [number, number, number] {
  const clamped = Math.max(0, Math.min(1, t));
  for (let i = 1; i < HEAT_STOPS.length; i++) {
    const [t0, c0] = HEAT_STOPS[i - 1];
    const [t1, c1] = HEAT_STOPS[i];
    if (clamped <= t1) {
      const k = (clamped - t0) / (t1 - t0);
      return [
        Math.round(c0[0] + (c1[0] - c0[0]) * k),
        Math.round(c0[1] + (c1[1] - c0[1]) * k),
        Math.round(c0[2] + (c1[2] - c0[2]) * k),
      ];
    }
  }
  return HEAT_STOPS[HEAT_STOPS.length - 1][1];
}

export class CnsPanel {
  readonly root = el("section", "panel panel--cns");
  private readonly canvas = el("canvas", "cns__canvas");
  private readonly legend = el("div", "cns__legend");
  private map: CnsMap | null = null;
  /** Отображаемая яркость — плавно тянется к target, см. draw(). */
  private glow: Float32Array = new Float32Array(0);
  /** Куда тянется glow: свежий heat из последнего снимка, остальное — 0. */
  private target: Float32Array = new Float32Array(0);
  /**
   * История разрядов на нейрон — растёт непрерывно всю сессию, без сброса
   * на границе месяца: население этой карты не меняется между месяцами
   * (тот же фиксированный набор ~7000 сом), так что личный p95 нейрона —
   * то же самое понятие что в сентябре, что в декабре. Ограничена
   * SCALE_HISTORY_LEN на нейрон, чтобы не течь по памяти на долгой сессии.
   */
  private history = new Map<number, number[]>();
  private frame = 0;
  private lastFrameAt = 0;

  constructor() {
    const head = el("div", "cns__head");
    head.append(
      el("div", "cns__label", "ЦНС / нейронная активность"),
      el("span", "cns__dot"),
    );
    this.canvas.width = 900;
    this.canvas.height = 420;
    this.root.append(head, this.canvas, this.legend);
  }

  setMap(map: CnsMap) {
    this.map = map;
    this.glow = new Float32Array(map.neurons);
    this.target = new Float32Array(map.neurons);
    this.history.clear();
    this.legend.replaceChildren(
      ...map.parts.map((part) => {
        const item = el("span", "cns__legend-item");
        const count = el("i");
        count.append(
          el("span", "cns__legend-count", thousands(part.slots.length)),
          document.createTextNode(" сом"),
        );
        item.append(el("b", undefined, part.title), count);
        return item;
      }),
    );
    if (!this.frame) this.frame = requestAnimationFrame(() => this.draw());
  }

  /**
   * Персональный p95 нейрона по накопленной истории. Одна активация —
   * своей же величины и есть «95-й процентиль»: это не сломанное значение,
   * это единственная разумная оценка на одной точке. Различимость по
   * категориям появляется по мере накопления истории — см. warmup-заметку
   * у SCALE_HISTORY_LEN.
   */
  private personalScale(slot: number): number {
    const hist = this.history.get(slot);
    if (!hist || hist.length === 0) return 1e-5;
    const sorted = [...hist].sort((a, b) => a - b);
    const idx = Math.min(sorted.length - 1, Math.ceil(0.95 * sorted.length) - 1);
    return Math.max(sorted[idx], 1e-5);
  }

  /**
   * Разреженные разряды из снимка задают новую цель (target) для яркости.
   *
   * Нормируем не на пик кадра, а на персональную шкалу нейрона (его p95
   * по истории, см. ASINH_M выше) — иначе картина двигается за структурными
   * хабами графа, а не за категорией. Затем arcsinh — мягче к средним
   * значениям, чем степенная кривая, не выдумывая контраст: нейрон,
   * разрядившийся вдвое чаще своей нормы, светится заметно ярче.
   *
   * target сбрасывается целиком каждый снимок: нейрон, не попавший в
   * sparse на этот раз, честно целится в 0 — реальная активность угасла,
   * а не «была и осталась висеть». К самому target глаз не видит скачков:
   * до него плавно дотягивается glow в draw(), кадр за кадром.
   */
  update(snapshot: Snapshot) {
    if (!this.map) return;
    this.target.fill(0);
    const sparse = snapshot.activity.cns_sparse;
    for (let i = 0; i < sparse.length; i += 2) {
      const slot = sparse[i];
      const count = sparse[i + 1];
      if (slot >= this.target.length) continue;

      let hist = this.history.get(slot);
      if (!hist) {
        hist = [];
        this.history.set(slot, hist);
      }
      hist.push(count);
      if (hist.length > SCALE_HISTORY_LEN) hist.shift();

      const scale = this.personalScale(slot);
      this.target[slot] = Math.min(1, Math.asinh(count / (scale * 0.5)) / ASINH_M);
    }
  }

  private draw() {
    this.frame = requestAnimationFrame(() => this.draw());
    const map = this.map;
    if (!map) return;
    const ctx = this.canvas.getContext("2d")!;
    const { width: w, height: h } = this.canvas;
    ctx.clearRect(0, 0, w, h);

    // Плавный подъём и спад вместо скачка: glow каждый кадр подтягивается
    // к target с постоянной времени ~280 мс — вспышка от клика по категории
    // растягивается в короткую волну, а не мигает вкл/выкл между снимками.
    const now = performance.now();
    const dt = this.lastFrameAt ? now - this.lastFrameAt : 16;
    this.lastFrameAt = now;
    const EASE_TAU_MS = 280;
    const ease = 1 - Math.exp(-dt / EASE_TAU_MS);

    // Два отдела бок о бок: мозг слева, брюшная цепочка справа.
    const pad = 16;
    const gap = 24;
    const boxW = (w - pad * 2 - gap) / 2;
    const boxH = h - pad * 2;

    map.parts.forEach((part, index) => {
      const ox = pad + index * (boxW + gap);
      const oy = pad;
      for (let i = 0; i < part.slots.length; i++) {
        const slot = part.slots[i];
        this.glow[slot] += ((this.target[slot] ?? 0) - this.glow[slot]) * ease;
        const heat = this.glow[slot];
        const x = ox + part.x[i] * boxW;
        const y = oy + part.y[i] * boxH;
        // Один непрерывный градиент активности на все соматы разом —
        // не «свой цвет для мозга, свой для цепочки»: место точки на шкале
        // (heatColor) определяет только её собственная текущая активность,
        // уже сжатая через arcsinh в update() и сглаженная выше. Оба
        // параметра, цвет и размер, растут от одного и того же значения.
        const [r, g, b] = heatColor(heat);
        const alpha = 0.45 + heat * 0.55;
        const size = 1.6 + heat * 7.0;
        ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${alpha})`;
        ctx.fillRect(x - size / 2, y - size / 2, size, size);
      }
    });
  }
}

// ---------- объяснение механики ----------

/** Мушиный вариант объяснения механики — жужжание без человеческих слов. */
const FLY_SPEECH =
  "Бззззззззззззз-бз-бз-бзззззззззззззбзбзбзбзбз-бзззззззззз " +
  "бзз-бз-бззззззззззззз бзбзбз-бззззззззззззззззз-бз-бз бззз-бз " +
  "бз-бз-бзззззззззззззбзбзбзбзбз-бзззззззззз бзз-бз-бззззззззззззз " +
  "бзбзбз-бззззззззззззззззз-бз-бз бззз-бзбзбз-бззззззззззззззззз-бз-бз " +
  "бззз-бз бз-бз-бзззззззззззззбзбзбзбзбз-бзззззззззз " +
  "бзз-бз-бззззззззззззз бзбзбз-бззззззззззззззззз-бз-бз бззз " +
  "бз-бз-бзззззззззззззбзбзбзбзбз-бзззззззззз бзз-бз-бззззззззззззз " +
  "бзбзбз-бззззззззззззззззз-бз-бз бззз-бз " +
  "бз-бз-бзззззззззззззбзбзбзбзбз-бзззззззззз бзз-бз-бззззззззззззз " +
  "бзбзбз-бззззззззззззззззз-бз-бз бззз-бзбзбз-бззззззззззззззззз-бз-бз " +
  "бззз-бз бз-бз-бзззззззззззззбзбзбзбзбз-бзззззззззз бзз-бз.";

/**
 * Без буквы «ё» — намеренный стилистический выбор текста, не опечатка и не
 * ограничение шрифта: не добавлять «ё» обратно при правках.
 * Научный вариант: термины называются прямо (коннектом, боковой рог,
 * грибовидное тело), в отличие от совсем бытового пересказа.
 */
const HUMAN_SECTIONS: { title: string; body: string }[] = [
  {
    title: "Откуда муха",
    body:
      "Использована коннектомная реконструкция MaleCNS v1.0. Полная карта " +
      "синаптических связей мозга самца дрозофилы, 166 700 нейронов, 25,6 млн " +
      "направленных связей. Реконструкция статична (это разметка реального, " +
      "ранее зафиксированного мозга), но связи между нейронами использованы " +
      "как есть, без упрощения или прореживания графа.",
  },
  {
    title: "Как она выбирает кешбэк",
    body:
      "Идентификатор категории детерминированно хешируется в паттерн " +
      "стимуляции обонятельных рецепторных нейронов (гломерулы антеннальной " +
      "доли) — то есть один и тот же id категории всегда дает один и тот же " +
      "паттерн возбуждения, воспроизводимо между запусками.",
  },
  {
    title: "Механизм решения",
    body:
      "Решающий сигнал считывается с бокового рога (lateral horn). Это " +
      "участок мозга насекомого, отвечающий за врожденную, генетически " +
      "заданную оценку запаха, в отличие от грибовидного тела (mushroom " +
      "body), которое у настоящих насекомых участвует в обучении на опыте. " +
      "Поскольку в этой модели синаптическая пластичность отключена (муха не " +
      "обучается), боковой рог оказался единственным путем, дающим " +
      "устойчивый, невырожденный сигнал — это было проверено " +
      "экспериментально, сравнением нескольких кандидатных популяций " +
      "нейронов. Топ 5 категорий по средней частоте разрядов этой популяции " +
      "за окно наблюдения и составляют выбор.",
  },
  {
    title: "При чем тут интересы",
    body:
      "Если категория совпала с чем-то из того, что муха любит, то мы " +
      "делаем этот запах для нее чуть громче, и все. А прозвучит он " +
      "убедительно или нет, это решает уже сама голова мухи. Поэтому часть " +
      "выбора каждый раз проходит мимо ее интересов: слышит она хорошо, но " +
      "выбирает по-своему.",
  },
];

function buildHelp(): HTMLElement {
  const wrap = el("div", "help");

  const tabs = el("div", "help__tabs");
  // Заголовки переключателя внутри блока — тем же кеглем, начертанием и
  // цветом, что и текст объяснений под ними: это подпись, не отдельная
  // кнопка. Заливка/ховер приходят из общего .toggle, единого для всех
  // переключаемых заголовков проекта (title/«Как это работает»/эти два).
  const buzzTab = el("button", "help__tab toggle toggle--body", "Бзззз");
  const humanTab = el("button", "help__tab toggle toggle--body", "На человеческом");
  buzzTab.type = "button";
  humanTab.type = "button";
  buzzTab.dataset.active = "";
  tabs.append(buzzTab, humanTab);

  const buzz = el("div", "help__buzz");
  buzz.append(el("p", "help__buzz-text", FLY_SPEECH));

  const human = el("div", "help__human");
  human.hidden = true;
  for (const section of HUMAN_SECTIONS) {
    const item = el("div", "help__section");
    item.append(
      el("div", "help__section-title", section.title),
      el("p", "help__section-body", section.body),
    );
    human.append(item);
  }

  const show = (showHuman: boolean) => {
    buzz.hidden = showHuman;
    human.hidden = !showHuman;
    buzzTab.toggleAttribute("data-active", !showHuman);
    humanTab.toggleAttribute("data-active", showHuman);
  };
  buzzTab.addEventListener("click", () => show(false));
  humanTab.addEventListener("click", () => show(true));

  wrap.append(tabs, buzz, human);
  return wrap;
}

// ---------- комната: интересы и график ----------

export class RoomPanel {
  readonly root = el("section", "panel panel--room");
  /** Имя мухи в шапке-переключателе — кнопка, а не статичный заголовок. */
  private readonly title = el("button", "room__tab-btn toggle toggle--caps");
  private readonly phase = el("div", "room__phase");
  private readonly explain = el("div", "room__explain");
  private readonly legend = el("div", "room__legend");
  private readonly bubbles = el("ul", "interests");
  private readonly plotHead = el("div", "plot__head");
  private readonly scaleLabel = el("div", "plot__scale");
  private readonly canvas = el("canvas", "plot__canvas");
  private readonly metrics = el("div", "room__metrics");
  private readonly hzValue = el("span", "mini__value");
  private readonly spikeValue = el("span", "mini__value");
  /** Служебная строка переехала сюда из подвала: место ей в приборке. */
  private readonly service = el("div", "room__service");
  /** Первая вкладка — живой ход выбора, вторая — объяснение механики. */
  private readonly liveTab = el("div", "room__tab");
  private readonly helpTab = el("div", "room__tab");
  private readonly helpTabBtn = el("button", "room__tab-btn toggle toggle--caps");
  private poolSize = 0;

  constructor() {
    this.canvas.width = 700;
    this.canvas.height = 110;
    this.plotHead.append(
      el("div", "plot__label", "Частота разрядов бокового рога"),
      this.scaleLabel,
    );

    const left = el("div", "mini");
    left.append(el("span", "mini__label", "Активность"), this.hzValue);
    const right = el("div", "mini");
    right.append(el("span", "mini__label", "Спайков за окно"), this.spikeValue);
    this.metrics.append(left, right);

    // Шапка блока — два переключаемых заголовка, как TRADES/DECISIONS у
    // stonkfly: активный залит акцентом целиком, неактивный — просто текст.
    this.title.type = "button";
    this.title.dataset.active = "";
    this.helpTabBtn.type = "button";
    this.helpTabBtn.textContent = "Как это работает";
    const head = el("div", "room__tabs");
    head.append(this.title, this.helpTabBtn);

    this.liveTab.append(
      el("div", "room__label", "Интересы"),
      this.bubbles,
      this.phase,
      this.explain,
      this.legend,
      this.plotHead,
      this.canvas,
      this.metrics,
      this.service,
    );
    this.helpTab.hidden = true;
    this.helpTab.append(buildHelp());

    const showLive = () => {
      this.liveTab.hidden = false;
      this.helpTab.hidden = true;
      this.title.toggleAttribute("data-active", true);
      this.helpTabBtn.toggleAttribute("data-active", false);
    };
    const showHelp = () => {
      this.liveTab.hidden = true;
      this.helpTab.hidden = false;
      this.title.toggleAttribute("data-active", false);
      this.helpTabBtn.toggleAttribute("data-active", true);
    };
    this.title.addEventListener("click", showLive);
    this.helpTabBtn.addEventListener("click", showHelp);

    this.root.append(head, this.liveTab, this.helpTab);
  }

  setPoolSize(size: number) {
    this.poolSize = size;
  }

  render(snapshot: Snapshot) {
    this.title.textContent = snapshot.room.title;

    const byId = new Map(snapshot.offers.map((o) => [o.id, o]));
    const selected = snapshot.selection.selected;
    const boosted = selected.filter((id) => byId.get(id)?.boosted).length;
    const own = selected.length - boosted;

    const status = this.status(snapshot, boosted, own);
    this.phase.textContent = status.title;
    this.explain.textContent = status.body;

    // Легенда цвета: без неё зритель гадает, почему галочки разного цвета.
    this.legend.replaceChildren();
    for (const [mod, text] of [
      ["boost", "по интересу"],
      ["own", "сама"],
    ] as const) {
      const item = el("span", "legend__item");
      const dot = el("span", "legend__dot");
      dot.dataset.kind = mod;
      item.append(dot, el("span", undefined, text));
      this.legend.append(item);
    }

    const active = activeInterests(snapshot);
    this.bubbles.replaceChildren(
      ...snapshot.room.interests.map((interest) =>
        this.bubble(interest, active.get(interest.id)),
      ),
    );

    const a = snapshot.activity;
    this.hzValue.textContent = `${hz(a.decision_hz)} Гц`;
    this.spikeValue.textContent = thousands(snapshot.spikes.window);
    this.drawPlot(a.decision_history);

    // Подпись — обычным шрифтом, число рядом — пиксельным: тот же приём,
    // что на референсе (подпись «Uptime» простым текстом, само значение
    // крупнее и другим начертанием). Строится через отдельные span, а не
    // одну textContent-строку, иначе нечем было бы разделить два шрифта
    // внутри одной фразы.
    this.service.replaceChildren(
      ...serviceLine([
        ["снимок ", `#${snapshot.sequence}`],
        ["нейронного времени ", `${(snapshot.sim_ms / 1000).toFixed(1)} с`],
        ["циклов ", String(snapshot.cycles_done)],
        ["аптайм ", `${Math.round(snapshot.uptime_seconds)} с`],
      ]),
    );
  }

  /**
   * Статус под интересами: заголовок + текст. Три состояния снимка. Числа
   * N/«3 и 2» — не пример под один прогон, а реальные счётчики текущего
   * снимка: считаются заново на каждый рендер, а не подставляются один раз.
   */
  private status(
    snapshot: Snapshot,
    boosted: number,
    own: number,
  ): { title: string; body: string } {
    const slots = snapshot.selection.slots;
    const picked = boosted + own;

    if (picked >= slots) {
      const parts: string[] = [];
      if (boosted) {
        const verb = boosted === 1 ? "была выбрана" : "были выбраны";
        parts.push(
          `${boosted} ${plural(boosted, "категория", "категории", "категорий")} ` +
          `${verb} по ее интересам`,
        );
      }
      if (own) {
        const verb = own === 1 ? "отозвалась" : "отозвались";
        const sami = own === 1 ? "сама" : "сами";
        const lead = boosted ? "Остальные" : "";
        parts.push(
          `${lead} ${own} ${plural(own, "категория", "категории", "категорий")} ` +
          `${verb} ${sami}, без подсказки от интересов`,
        );
      }
      return {
        title: "Выбор сделан",
        body: parts.map((p) => p.trim()).join(". ") + (parts.length ? "." : ""),
      };
    }

    if (picked > 0) {
      return {
        title: "Отмечает",
        body:
          `${picked} ${plural(picked, "категория", "категории", "категорий")} ` +
          `уже приглянулись, выбирает оставшиеся.`,
      };
    }

    return {
      title: "Выбирает категории",
      body:
        "Категории проходят через обонятельный центр. Муха нюхает категории " +
        "одну за другой и запоминает, на какую отозвалась сильнее.",
    };
  }

  private bubble(interest: Interest, because: string[] | undefined) {
    const item = el("li", "interest", interest.title);
    item.dataset.id = interest.id;
    if (because?.length) {
      item.dataset.active = "";
      item.title = `Сыграл в выборе: ${because.join(", ")}`;
    }
    return item;
  }

  private drawPlot(history: number[]) {
    const ctx = this.canvas.getContext("2d")!;
    const { width: w, height: h } = this.canvas;
    ctx.clearRect(0, 0, w, h);
    if (history.length < 2) {
      this.scaleLabel.textContent = "нет данных";
      return;
    }
    let lo = Math.min(...history);
    let hi = Math.max(...history);
    if (hi - lo < 1e-6) {
      lo -= 0.5;
      hi += 0.5;
    }
    const pad = (hi - lo) * 0.15;
    lo -= pad;
    hi += pad;
    const scaleNodes: Node[] = [
      el("span", "plot__scale-value", `${hz(lo)}–${hz(hi)} Гц`),
      el("span", "plot__scale-label", " · автомасштаб"),
    ];
    if (this.poolSize) {
      scaleNodes.push(
        el("span", "plot__scale-label", " · "),
        el("span", "plot__scale-value", thousands(this.poolSize)),
        el("span", "plot__scale-label", " нейронов"),
      );
    }
    this.scaleLabel.replaceChildren(...scaleNodes);

    const x = (i: number) => (i / (history.length - 1)) * (w - 2) + 1;
    const y = (v: number) => h - 4 - ((v - lo) / (hi - lo)) * (h - 8);

    ctx.strokeStyle = "#b88cff";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    history.forEach((v, i) => (i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v))));
    ctx.stroke();
    ctx.fillStyle = "#b88cff";
    ctx.beginPath();
    ctx.arc(x(history.length - 1), y(history[history.length - 1]!), 2.5, 0, Math.PI * 2);
    ctx.fill();
  }
}


/**
 * Какие интересы реально сыграли: для каждой выбранной категории ищем
 * интересы комнаты, чьи теги с ней пересеклись.
 */
export function activeInterests(snapshot: Snapshot): Map<string, string[]> {
  const result = new Map<string, string[]>();
  const byId = new Map(snapshot.offers.map((o) => [o.id, o]));
  for (const id of snapshot.selection.selected) {
    const offer = byId.get(id);
    if (!offer?.boosted) continue;
    const tags = new Set(offer.tags);
    for (const interest of snapshot.room.interests) {
      if (!interest.tags.some((t) => tags.has(t))) continue;
      const list = result.get(interest.id) ?? [];
      list.push(offer.title);
      result.set(interest.id, list);
    }
  }
  return result;
}
