/**
 * Опрос снимков. Единственный канал данных: клиент только читает.
 *
 * Зритель на расчёт не влияет — никаких POST, никаких параметров запроса,
 * меняющих поведение воркера. Переключение комнаты это просто другой URL.
 */

import type { CnsMap, RoomIndex, Snapshot } from "./types";

export const POLL_INTERVAL_MS = 1000;

export interface Links {
  payUrl: string;
  shareUrl: string;
}

/** Если файл недоступен (первая раздача, опечатка в пути) — не блокируем сайт. */
const DEFAULT_LINKS: Links = {
  payUrl: "https://bank.yandex.ru/_pay/login?product=card",
  shareUrl: "https://cashbackfly.bz",
};

/**
 * Ссылки интерфейса — из web/public/config.json, не из бандла.
 *
 * Правится прямо на сервере (как web/public/icons/index.json): заменили
 * файл — подхватилось на следующей загрузке страницы, пересборка не нужна.
 */
export async function loadLinks(signal?: AbortSignal): Promise<Links> {
  try {
    const response = await fetch("/config.json", { cache: "no-store", signal });
    if (!response.ok) return DEFAULT_LINKS;
    const data = (await response.json()) as Partial<Links>;
    return { ...DEFAULT_LINKS, ...data };
  } catch {
    return DEFAULT_LINKS;
  }
}

export async function loadRoomIndex(signal?: AbortSignal): Promise<RoomIndex> {
  const response = await fetch("/api/rooms.json", { cache: "no-store", signal });
  if (!response.ok) throw new Error(`индекс комнат: HTTP ${response.status}`);
  return (await response.json()) as RoomIndex;
}

/**
 * Карта сом. Грузится один раз и потом только читается из кеша.
 *
 * С повтором: nginx поднимается раньше воркера, и первые секунды после
 * развёртывания файла ещё нет. Разово словить 404 и остаться без карты до
 * перезагрузки страницы — плохо, поэтому просто ждём и пробуем снова.
 */
export async function loadCnsMap(
  signal?: AbortSignal,
  attempts = 5,
): Promise<CnsMap> {
  let lastError: unknown;
  for (let attempt = 0; attempt < attempts; attempt++) {
    try {
      const response = await fetch("/api/cns.json", {
        cache: "force-cache",
        signal,
      });
      if (response.ok) return (await response.json()) as CnsMap;
      lastError = new Error(`карта ЦНС: HTTP ${response.status}`);
    } catch (error) {
      if ((error as Error)?.name === "AbortError") throw error;
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 1000 * (attempt + 1)));
  }
  throw lastError;
}

export type PollHandlers = {
  onSnapshot: (snapshot: Snapshot) => void;
  onError?: (error: unknown) => void;
};

/**
 * Опрашивает снимок одной комнаты раз в секунду.
 *
 * Два правила, без которых картинка дёргается:
 *  • ответы с несвежим `sequence` отбрасываются — сеть может доставить их
 *    не в том порядке, а откат номера на экране выглядит как сбой;
 *  • следующий запрос ставится после завершения предыдущего, а не по
 *    голому интервалу, иначе на медленном соединении запросы копятся.
 */
export function pollRoom(roomId: string, handlers: PollHandlers): () => void {
  const url = `/api/rooms/${encodeURIComponent(roomId)}/snapshot.json`;
  let timer: number | undefined;
  let stopped = false;
  let lastSequence = -1;
  /** Подряд идущие сбои: по ним растёт пауза до следующей попытки. */
  let failures = 0;
  const controller = new AbortController();

  const tick = async () => {
    if (stopped) return;
    const started = performance.now();
    try {
      const response = await fetch(url, {
        cache: "no-store",
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(`снимок: HTTP ${response.status}`);
      const snapshot = (await response.json()) as Snapshot;
      failures = 0;
      if (snapshot.sequence > lastSequence) {
        lastSequence = snapshot.sequence;
        handlers.onSnapshot(snapshot);
      }
    } catch (error) {
      if (!stopped && (error as Error)?.name !== "AbortError") {
        failures += 1;
        // Сообщаем только о первых сбоях: если воркер лежит, незачем
        // сыпать одинаковыми ошибками в консоль до конца времён.
        if (failures <= 3) handlers.onError?.(error);
      }
    }
    if (stopped) return;
    const spent = performance.now() - started;
    // Отступ при сбоях: 1 с → 2 → 4 → … но не дольше 15 с, чтобы восстановление
    // после возврата воркера было быстрым.
    const backoff = Math.min(POLL_INTERVAL_MS * 2 ** failures, 15000);
    const wait = failures ? backoff : POLL_INTERVAL_MS;
    timer = window.setTimeout(tick, Math.max(0, wait - spent));
  };

  void tick();

  return () => {
    stopped = true;
    controller.abort();
    if (timer !== undefined) window.clearTimeout(timer);
  };
}

/** Комната из адреса: `?room=<id>`. Ничего не знает о конфиге воркера. */
export function roomFromLocation(): string | null {
  return new URLSearchParams(window.location.search).get("room");
}

export function setRoomInLocation(roomId: string): void {
  const url = new URL(window.location.href);
  url.searchParams.set("room", roomId);
  window.history.replaceState({}, "", url);
}
