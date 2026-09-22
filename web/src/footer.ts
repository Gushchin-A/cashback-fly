/**
 * Подвал: честная строка о том, чем это написано.
 *
 * Объяснение механики переехало во вкладку панели комнаты, поэтому здесь
 * остались только кнопки действий и признание про вайбкодинг — ТЗ требует
 * не прятать, что проект собран ИИ-агентом.
 */

import type { Links } from "./api";

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

/** Сколько тултип «Ссылка скопирована» держится перед тем как погаснуть. */
const TOOLTIP_MS = 1800;

/**
 * `navigator.share` есть и на десктопном Chrome/Edge — не только на
 * телефонах, вопреки расчёту. Без этой проверки клик по «Поделиться» на
 * десктопе открывал системную шторку «отправить в почту/мессенджер»,
 * которой там не место. Признак мобильного — грубый указатель (палец, не
 * мышь) вместе с UA; одного UA мало, десктоп с сенсорным экраном не мобильный.
 */
function isMobileDevice(): boolean {
  const coarsePointer = window.matchMedia?.("(pointer: coarse)").matches ?? false;
  const mobileUa = /Mobi|Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
  return coarsePointer && mobileUa;
}

export class Footer {
  readonly root = el("footer", "bottom");
  private tooltipTimer = 0;

  constructor(links: Links) {
    const pay = el("a", "action action--primary");
    pay.href = links.payUrl;
    pay.target = "_blank";
    pay.rel = "noopener noreferrer";
    // Прописная «Й» у Press Start 2P нарисована неудачно (см. .custom-yi
    // ниже) — визуально берём «И» того же шрифта и дорисовываем краткую
    // сверху через ::after. aria-hidden прячет подделку от скринридера,
    // а рядом sr-only-спан отдаёт настоящую букву — читается как «Пэй»,
    // не «Пэи».
    const yi = el("span", "custom-yi");
    yi.setAttribute("aria-hidden", "true");
    yi.textContent = "И";
    pay.append(
      document.createTextNode("Открыть карту Пэ"),
      yi,
      el("span", "sr-only", "Й"),
    );

    const shareWrap = el("div", "share");
    const share = el("button", "action", "Поделиться");
    share.type = "button";
    const tooltip = el("span", "share__tooltip", "Ссылка скопирована");
    shareWrap.append(share, tooltip);

    share.addEventListener("click", () => void this.share(links.shareUrl, tooltip));

    const actions = el("div", "actions");
    actions.append(pay, shareWrap);

    const credit = el("p", "credit");
    // Разрыв после «смм-щиком!» нужен только на мобильном (три строки
    // вместо двух) — на десктопе фраза остаётся одной строкой, поэтому
    // сам <br> скрыт по умолчанию и появляется через .credit__mobile-break
    // в media (см. base.css). Пробел перед «Поэтому» не рвёт десктопный
    // вид: в потоке текста он просто разделяет слова как обычно.
    const mobileBreak = el("br", "credit__mobile-break");
    const repoLink = el("a", "credit__link", "GitHub: cashback-fly");
    repoLink.href = "https://github.com/Gushchin-A/cashback-fly";
    repoLink.target = "_blank";
    repoLink.rel = "noopener noreferrer";
    credit.append(
      document.createTextNode("Проект навайбкожен смм-щиком!"),
      mobileBreak,
      document.createTextNode(" Поэтому не придирайтесь, плиз"),
      document.createElement("br"),
      repoLink,
    );

    this.root.append(actions, credit);
  }

  /**
   * Мобильные — системный лист «Поделиться» (`navigator.share`), если он
   * есть. Десктоп всегда идёт в копирование, даже если браузер технически
   * поддерживает Web Share API (см. isMobileDevice выше). Отмену
   * пользователем (крестик в системном листе) не показываем как ошибку —
   * это не сбой.
   */
  private async share(url: string, tooltip: HTMLElement) {
    const nav = navigator as Navigator & { share?: (data: ShareData) => Promise<void> };
    if (nav.share && isMobileDevice()) {
      try {
        await nav.share({ url, title: "CASHBACKFLY" });
      } catch (error) {
        if ((error as Error)?.name !== "AbortError") this.copyFallback(url, tooltip);
      }
      return;
    }
    this.copyFallback(url, tooltip);
  }

  private copyFallback(url: string, tooltip: HTMLElement) {
    navigator.clipboard
      .writeText(url)
      .then(() => {
        window.clearTimeout(this.tooltipTimer);
        tooltip.dataset.visible = "";
        this.tooltipTimer = window.setTimeout(() => {
          delete tooltip.dataset.visible;
        }, TOOLTIP_MS);
      })
      .catch((error) => {
        // Разрешение на буфер не выдано/отозвано браузером — не ломаем
        // страницу, просто не показываем тултип об успехе, которого не было.
        console.error("не удалось скопировать ссылку", error);
      });
  }
}
