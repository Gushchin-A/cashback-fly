/**
 * Усиление движения из измеренной нейронной активности.
 *
 * Здесь нет ни одной заранее записанной анимации. Каждый кадр поза мухи
 * пересчитывается из двух чисел снимка:
 *   motor_hz — средняя частота MN9/DNp09, моторных выходов;
 *   turn_hz  — разница правых и левых DNa02, то есть поворотное смещение.
 *
 * Сырые частоты напрямую в углы не годятся: они скачут от окна к окну и
 * живут в узком диапазоне. Поэтому между данными и позой стоят две вещи.
 *
 * 1. Насыщающая кривая `x/(x+k)`. Она сжимает любой диапазон в 0..1 без
 *    обрезания: вдвое большая частота даёт заметно, но не вдвое большее
 *    движение. Порог k выбран по наблюдаемому размаху (см. NOTE ниже).
 * 2. Огибающая с быстрой атакой и медленным спадом — как у FlyTok. Всплеск
 *    активности виден сразу, но не исчезает в следующем же кадре, иначе
 *    при опросе раз в секунду движение выглядит рваным.
 *
 * Усиление — это художественное преувеличение измеренной величины, а не
 * биомеханическая модель полёта. Так и написано в дисклеймере.
 */

/**
 * NOTE: пороги подобраны под реальные значения этапа 1. В прогонах воркера
 * motor_hz держится около 2–6 Гц, turn_hz в пределах ±15 Гц. Если механика
 * стимуляции изменится, эти числа надо пересматривать вместе с ней.
 */
const MOTOR_HALF = 4;
const TURN_HALF = 10;

const ATTACK_MS = 100;
const RELEASE_MS = 700;
const TURN_SMOOTH_MS = 200;

/** Сжимает 0..∞ в 0..1; при x = half даёт ровно 0.5. */
export function saturate(x: number, half: number): number {
  const v = Math.max(0, x);
  return v / (v + half);
}

/** Экспоненциальное приближение с шагом по времени, устойчивое к дропам кадров. */
function approach(current: number, target: number, tauMs: number, dtMs: number) {
  if (tauMs <= 0) return target;
  return current + (target - current) * (1 - Math.exp(-dtMs / tauMs));
}

export interface MotionState {
  /** 0..1 — общая двигательная активность. */
  drive: number;
  /** −1..1 — поворот головы и корпуса. */
  turn: number;
  /** Фаза взмаха крыльев, радианы. */
  wingPhase: number;
  /** 0..1 — насколько лапа вытянута к экрану. */
  reach: number;
}

export function createMotion(): MotionState {
  return { drive: 0, turn: 0, wingPhase: 0, reach: 0 };
}

export interface MotionInput {
  motorHz: number;
  turnHz: number;
  /** Тянуться к экрану: фаза оглашения слота или только что выбранная категория. */
  reaching: boolean;
}

export function updateMotion(
  state: MotionState,
  input: MotionInput,
  dtMs: number,
): MotionState {
  const targetDrive = saturate(input.motorHz, MOTOR_HALF);
  // Атака быстрая, спад медленный: всплеск заметен, затухание плавное.
  const tau = targetDrive > state.drive ? ATTACK_MS : RELEASE_MS;
  state.drive = approach(state.drive, targetDrive, tau, dtMs);

  const targetTurn =
    Math.sign(input.turnHz) * saturate(Math.abs(input.turnHz), TURN_HALF);
  state.turn = approach(state.turn, targetTurn, TURN_SMOOTH_MS, dtMs);

  // Частота взмаха растёт с активностью, но не падает до нуля: живая муха
  // не замирает полностью.
  const wingHz = 6 + state.drive * 22;
  state.wingPhase = (state.wingPhase + (wingHz * dtMs) / 1000 * Math.PI * 2) % (Math.PI * 2);

  state.reach = approach(
    state.reach,
    input.reaching ? 1 : 0,
    input.reaching ? 180 : 420,
    dtMs,
  );

  return state;
}
