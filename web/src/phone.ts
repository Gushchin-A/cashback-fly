/**
 * Телефон: коробка с экраном-текстурой.
 *
 * Всё, что на экране, рисуется в canvas низкого разрешения и натягивается
 * с `NearestFilter` — отсюда пиксельный вид без сглаживания. Это не эффект
 * поверх HTML-текста: HTML-текста на экране телефона нет вообще, иначе он
 * не жил бы в 3D-пространстве и не поворачивался бы вместе с устройством.
 *
 * Содержимое экрана обновляется по реальным событиям снимка, а не по
 * собственному таймеру или анимации.
 */

import * as THREE from "three";
import type { Snapshot } from "./types";

/** Низкое разрешение и есть источник пиксельности; не поднимать без нужды. */
export const SCREEN_W = 260;
export const SCREEN_H = 460;

export interface PhoneParts {
  group: THREE.Group;
  screen: THREE.Mesh;
  /** Куда целится лапа: центр экрана в мировых координатах. */
  target: THREE.Object3D;
  draw(snapshot: Snapshot | null): void;
  /** Подсветка нажатия кнопки. Вызывает сцена, когда лапа дотянулась. */
  setPressed(value: boolean): void;
}

export function buildPhone(): PhoneParts {
  const group = new THREE.Group();

  const body = new THREE.Mesh(
    new THREE.BoxGeometry(1.5, 0.08, 2.65),
    new THREE.MeshStandardMaterial({
      color: 0x1b1f26,
      roughness: 0.45,
      metalness: 0.6,
    }),
  );
  body.receiveShadow = true;
  body.castShadow = true;
  group.add(body);

  const canvas = document.createElement("canvas");
  canvas.width = SCREEN_W;
  canvas.height = SCREEN_H;
  const ctx = canvas.getContext("2d")!;
  ctx.imageSmoothingEnabled = false;

  const texture = new THREE.CanvasTexture(canvas);
  texture.magFilter = THREE.NearestFilter;
  texture.minFilter = THREE.NearestFilter;
  texture.generateMipmaps = false;
  texture.colorSpace = THREE.SRGBColorSpace;

  const screen = new THREE.Mesh(
    new THREE.PlaneGeometry(1.38, 2.5),
    new THREE.MeshBasicMaterial({ map: texture, toneMapped: false }),
  );
  // Доворот на 180° вокруг своей нормали — иначе при постановке телефона
  // вертикально (в scene.ts) содержимое экрана оказывается вверх ногами:
  // без этого «верх» канваса смотрел в нижний край корпуса.
  screen.rotation.set(-Math.PI / 2, 0, Math.PI);
  screen.position.y = 0.041;
  group.add(screen);

  let pressed = false;

  const target = new THREE.Object3D();
  target.position.set(0, 0.06, 0);
  group.add(target);

  function draw(snapshot: Snapshot | null) {
    ctx.fillStyle = "#0b0c13";
    ctx.fillRect(0, 0, SCREEN_W, SCREEN_H);

    if (!snapshot) {
      ctx.fillStyle = "#4a4d5c";
      ctx.font = "12px monospace";
      ctx.textAlign = "center";
      ctx.fillText("нет связи", SCREEN_W / 2, SCREEN_H / 2);
      texture.needsUpdate = true;
      return;
    }

    const selected = new Set(snapshot.selection.selected);
    const presenting = snapshot.presenting?.id ?? null;
    const complete = snapshot.selection.complete;

    ctx.textAlign = "left";
    ctx.fillStyle = "#ffffff";
    ctx.font = "bold 13px monospace";
    const month = (snapshot.month?.title ?? "").toLowerCase();
    ctx.fillText(`Кешбэк на ${month}`, 10, 20);
    ctx.fillStyle = "#8b8e9e";
    ctx.font = "11px monospace";
    ctx.fillText(
      `выбрано ${selected.size} из ${snapshot.selection.slots}`,
      10,
      36,
    );

    // Список: рисуем столько, сколько влезает над кнопкой.
    const buttonH = 34;
    const top = 48;
    const bottom = SCREEN_H - buttonH - 12;
    const rowH = 21;
    const visible = Math.floor((bottom - top) / rowH);

    snapshot.offers.slice(0, visible).forEach((offer, i) => {
      const y = top + i * rowH;
      const isSelected = selected.has(offer.id);
      const isPresenting = offer.id === presenting;

      if (isPresenting) {
        ctx.fillStyle = "#222a3a";
        ctx.fillRect(5, y - 1, SCREEN_W - 10, rowH - 3);
      }
      // Чекбокс строго по selection.selected.
      ctx.strokeStyle = isSelected ? "#b88cff" : "#3a3c48";
      ctx.lineWidth = 1.5;
      ctx.strokeRect(SCREEN_W - 24.5, y + 2.5, 12, 12);
      if (isSelected) {
        ctx.fillStyle = "#b88cff";
        ctx.fillRect(SCREEN_W - 22, y + 5, 7, 7);
      }

      ctx.fillStyle = offer.boosted ? "#b88cff" : "#7c8194";
      ctx.font = "bold 11px monospace";
      ctx.fillText(`${offer.rate}%`, 10, y + 12);

      ctx.fillStyle = isSelected ? "#ffffff" : "#a8acbd";
      ctx.font = "12px monospace";
      const title =
        offer.title.length > 18 ? offer.title.slice(0, 17) + "…" : offer.title;
      ctx.fillText(title, 52, y + 12);
    });

    // Кнопка есть всегда и занимает место; активна только на полном наборе.
    const by = SCREEN_H - buttonH - 6;
    ctx.fillStyle = complete ? (pressed ? "#d1b3ff" : "#b88cff") : "#23252f";
    ctx.fillRect(10, by, SCREEN_W - 20, buttonH - 4);
    ctx.fillStyle = complete ? "#0b0c13" : "#55596a";
    ctx.font = "bold 13px monospace";
    ctx.textAlign = "center";
    ctx.fillText("Выбрать", SCREEN_W / 2, by + buttonH / 2 + 1);
    ctx.textAlign = "left";

    texture.needsUpdate = true;
  }

  draw(null);
  return {
    group,
    screen,
    target,
    draw,
    setPressed(value: boolean) {
      pressed = value;
    },
  };
}
