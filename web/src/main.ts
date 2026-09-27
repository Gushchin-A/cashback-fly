/**
 * Точка сборки: индекс комнат → опрос снимка → панели и сцена.
 *
 * Состояние клиента параметризовано комнатой с самого начала, хотя комната
 * сейчас одна. Переключатель скрыт, пока в индексе меньше двух комнат, но
 * место под него в разметке есть, а загрузка идёт по идентификатору. Вторая
 * комната — это флаг в конфиге воркера и показ переключателя, не переписывание
 * фронтенда.
 */

import "./styles/base.css";
import {
  loadCnsMap,
  loadLinks,
  loadRoomIndex,
  pollRoom,
  roomFromLocation,
  setRoomInLocation,
} from "./api";
import { CategoriesPanel, CnsPanel, RoomPanel } from "./panels";
import { Footer } from "./footer";
import { createScene } from "./scene";
import type { RoomIndex, Snapshot } from "./types";

const app = document.getElementById("app")!;

function element<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
  text?: string,
) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

class App {
  private readonly stage = element("div", "zone zone--stage");
  private readonly switcher = element("nav", "rooms");
  private footer!: Footer;
  /**
   * Когда муха потянется к кнопке подтверждения. 0 — не запланировано.
   *
   * Отсчёт идёт не от момента, когда воркер закрыл цикл, а от момента, когда
   * зритель увидел все пять галочек: панель показывает их с задержкой, и жать
   * раньше значило бы сменить месяц у человека на полуслове.
   */
  private confirmAt = 0;
  private confirmDone = false;
  private readonly categories = new CategoriesPanel();
  private readonly cns = new CnsPanel();
  private readonly room = new RoomPanel();
  readonly scene = createScene();
  private stopPolling: (() => void) | null = null;
  private index: RoomIndex | null = null;

  async mount() {
    // Ссылки — из config.json на сервере, не из бандла (см. api.ts). Нужны
    // и лого Пэй, и подвалу, поэтому ждём их до сборки разметки: сеть тут —
    // тот же файл, что и статика страницы, задержка не заметна.
    const links = await loadLinks();
    this.footer = new Footer(links);

    const header = element("header", "top");
    const left = element("div", "top__left");
    const brand = element("div", "brand");
    const brandIcon = element("img", "brand__icon");
    brandIcon.src = "/icons/fly-pixel.svg";
    brandIcon.alt = "";
    brand.append(
      brandIcon,
      element("span", "brand__cashback", "CASHBACK"),
      element("span", "brand__fly", "FLY"),
    );
    left.append(brand);
    const partner = element("div", "top__partner");
    // Ведёт туда же, куда кнопка «Открыть карту Пэй» — тот же вход.
    const partnerLink = element("a", "partner__link");
    partnerLink.href = links.payUrl;
    partnerLink.target = "_blank";
    partnerLink.rel = "noopener noreferrer";
    const partnerLogo = element("img", "partner__logo");
    partnerLogo.src = "/icons/yandex-pay.svg";
    partnerLogo.alt = "Яндекс Пэй";
    partnerLink.append(partnerLogo);
    partner.append(partnerLink);
    header.append(left, partner);

    const caption = element("p", "stage-caption");
    caption.append(
      document.createTextNode(TAGLINE_LINE_1),
      document.createElement("br"),
      document.createTextNode(TAGLINE_LINE_2),
    );

    const grid = element("main", "grid");
    // Порядок в DOM важен только для мобильного потока (.grid превращается
    // в display:flex; flex-direction:column, а там всё идёт по исходнику).
    // На десктопе раскладка идёт по grid-template-areas и от порядка ниже
    // не зависит — там ЦНС и интересы всё равно встанут по своим клеткам.
    grid.append(caption, this.stage, this.categories.root, this.room.root, this.cns.root);

    app.append(header, this.switcher, grid, this.footer.root);
    this.scene.mount(this.stage);

    // window.resize не ловит изменение высоты самого блока: она зависит от
    // соседней панели категорий (число офферов и скелетон/загружено меняют
    // её высоту без ресайза окна). Без наблюдателя рендерер держит старый
    // размер буфера, а CSS растягивает картинку в новый — отсюда «сжатый
    // как JPEG» вид мухи при смене месяца.
    new ResizeObserver(() => this.scene.resize()).observe(this.stage);
    window.addEventListener("resize", () => this.scene.resize());
    void this.start();
  }

  private async start() {
    try {
      this.index = await loadRoomIndex();
    } catch (error) {
      console.error("воркер не отвечает", error);
      return;
    }

    const pool = Number(
      (this.index.provenance as Record<string, unknown>).decision_pool_size ?? 0,
    );
    this.room.setPoolSize(pool);
    try {
      this.cns.setMap(await loadCnsMap());
    } catch (error) {
      console.error("карта ЦНС не загрузилась", error);
    }

    this.renderSwitcher();
    const requested = roomFromLocation();
    const known = this.index.rooms.some((r) => r.id === requested);
    // updateUrl только если комната и так была в адресе: молчаливый дефолт
    // на первую комнату не должен дописывать ?room= в чистый URL.
    this.select(known ? requested! : this.index.rooms[0]?.id, known);
  }

  /** Переключатель появляется сам, когда комнат становится больше одной. */
  private renderSwitcher() {
    const rooms = this.index?.rooms ?? [];
    this.switcher.hidden = rooms.length < 2;
    this.switcher.replaceChildren(
      ...rooms.map((room) => {
        const button = element("button", "rooms__item", room.title);
        button.type = "button";
        button.dataset.id = room.id;
        button.addEventListener("click", () => this.select(room.id));
        return button;
      }),
    );
  }

  /** Смена комнаты — только другой URL снимка. На сервер ничего не уходит. */
  private select(roomId: string | undefined, updateUrl = true) {
    if (!roomId) {
      console.error("в индексе нет ни одной комнаты");
      return;
    }
    this.stopPolling?.();
    if (updateUrl) setRoomInLocation(roomId);
    for (const button of this.switcher.querySelectorAll("button")) {
      button.toggleAttribute("data-current", button.dataset.id === roomId);
    }
    this.stopPolling = pollRoom(roomId, {
      onSnapshot: (snapshot) => this.render(snapshot),
      onError: (error) => console.error("снимок недоступен", error),
    });
  }

  private render(snapshot: Snapshot) {
    this.categories.render(snapshot);
    this.cns.update(snapshot);
    this.room.render(snapshot);
    this.scene.update(snapshot);
    this.scheduleConfirm(snapshot);
  }

  /** Муха жмёт «Выбрать» сама — через паузу после показа последней галочки. */
  private scheduleConfirm(snapshot: Snapshot) {
    if (!this.categories.allShown(snapshot)) {
      this.confirmAt = 0;
      this.confirmDone = false;
      return;
    }
    const now = performance.now();
    if (!this.confirmAt) {
      this.confirmAt = now + CONFIRM_DELAY_MS;
      return;
    }
    if (!this.confirmDone && now >= this.confirmAt) {
      this.confirmDone = true;
      this.scene.reach();
      this.categories.press();
    }
  }
}

const TAGLINE_LINE_1 = "Просто муха выбирает категории кешбэка в Яндекс Пэй.";
const TAGLINE_LINE_2 = "Или сны смм-щика при температуре 39.";

/** Сколько зритель смотрит на готовый набор, прежде чем муха подтвердит. */
const CONFIRM_DELAY_MS = 5000;

const app_instance = new App();
void app_instance.mount();
// Отладочная ручка: габариты сцены из консоли, без перезапуска.
(window as unknown as Record<string, unknown>).__fly = app_instance;
