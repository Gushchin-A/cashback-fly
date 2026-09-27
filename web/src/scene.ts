/**
 * 3D-сцена: муха над телефоном, орбитальная камера.
 *
 * Поза мухи каждый кадр считается из `motion.ts`, то есть из реальных
 * моторных частот снимка. Лапа тянется «примерно в район» экрана, а не в
 * конкретную плитку — попадание не вычисляется и не подгоняется.
 */

import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { buildFly, type FlyParts } from "./fly";
import { buildPhone, type PhoneParts } from "./phone";
import { createMotion, updateMotion, type MotionState } from "./motion";
import type { Snapshot } from "./types";

export interface Scene3D {
  mount(container: HTMLElement): void;
  update(snapshot: Snapshot | null): void;
  /** Потянуться к экрану. Момент выбирает вызывающая сторона. */
  reach(durationMs?: number): void;
  resize(): void;
  measure(): unknown;
  dispose(): void;
}

export function createScene(): Scene3D {
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0b0c13);

  const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 100);
  camera.position.set(-2.6, 2.3, 5.5);

  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  let controls: OrbitControls | null = null;
  let container: HTMLElement | null = null;

  scene.add(new THREE.AmbientLight(0x4a5260, 1.1));
  const key = new THREE.DirectionalLight(0xffffff, 2.2);
  key.position.set(4, 6, 3);
  key.castShadow = true;
  key.shadow.mapSize.set(1024, 1024);
  scene.add(key);
  const rim = new THREE.DirectionalLight(0x7376ff, 1.4);
  rim.position.set(-4, 2, -3);
  scene.add(rim);

  // Телефон стоит вертикально ПЕРЕД мухой, экраном к ней: она стоит на полу
  // и тянется к нему передней лапой. Ось +X у мухи — вперёд, туда и ставим
  // экран, близко, чтобы муха смотрела в него в упор.
  const phone: PhoneParts = buildPhone();
  const PHONE_SCALE = 0.78;
  phone.group.scale.setScalar(PHONE_SCALE);
  // Поворот на 90° вокруг X ставит корпус на ребро: длинная сторона (была
  // вдоль Z) становится вертикальной высотой. Поворот на 90° вокруг Z
  // после этого разворачивает экран лицом на муху (в −X, туда, где она
  // стоит) — экран целится в саму муху, а не в камеру зрителя.
  phone.group.rotation.x = -Math.PI / 2;
  phone.group.rotation.z = Math.PI / 2;
  phone.group.position.set(0.85, 1.325 * PHONE_SCALE - 0.05, 0.05);
  scene.add(phone.group);

  const fly: FlyParts = buildFly();
  fly.group.scale.setScalar(0.66);
  // Высота выставлена по замеру габаритов: при 0.46 самая нижняя точка лап
  // уходила под пол на 0.3, поэтому муха «тонула». Держим её ровно на полу.
  fly.group.position.set(-0.7, 0.76, 0);
  fly.group.rotation.y = 0.1;
  scene.add(fly.group);

  const floor = new THREE.Mesh(
    new THREE.PlaneGeometry(24, 24),
    new THREE.MeshStandardMaterial({ color: 0x0e1015, roughness: 0.95 }),
  );
  floor.rotation.x = -Math.PI / 2;
  floor.position.y = -0.05;
  floor.receiveShadow = true;
  scene.add(floor);

  const motion: MotionState = createMotion();
  let snapshot: Snapshot | null = null;
  /** Сколько слотов было в прошлом снимке — по росту видно момент фиксации. */
  let lastSelected = 0;
  let reachUntil = 0;
  let last = performance.now();
  let frame = 0;

  const baseFlyY = fly.group.position.y;

  function render() {
    frame = requestAnimationFrame(render);
    const now = performance.now();
    const dt = Math.min(now - last, 100);
    last = now;

    const activity = snapshot?.activity;
    updateMotion(
      motion,
      {
        motorHz: activity?.motor_hz ?? 0,
        turnHz: activity?.turn_hz ?? 0,
        reaching: now < reachUntil,
      },
      dt,
    );

    // Крылья: размах растёт с активностью.
    const swing = 0.25 + motion.drive * 0.95;
    fly.wings.forEach((wing, i) => {
      const side = i === 0 ? 1 : -1;
      wing.rotation.x = Math.sin(motion.wingPhase) * swing * side;
      wing.rotation.z = side * (0.12 + motion.drive * 0.2);
    });

    // Голова поворачивается по разнице левых и правых DNa02.
    fly.head.rotation.y = motion.turn * 0.45;
    fly.group.rotation.z = -motion.turn * 0.08;

    // Муха стоит, но покачивается — амплитуда из активности, не из таймера.
    const t = now / 1000;
    fly.group.position.y =
      baseFlyY + Math.sin(t * 2.1) * (0.008 + motion.drive * 0.022);

    // Передняя правая тянется к экрану. Угол задаётся долей reach; попадание
    // в конкретную плитку не вычисляется — лапа приходит в район экрана.
    const { pointer } = fly;
    pointer.coxa.rotation.x = pointer.restCoxa.x - motion.reach * 0.75;
    pointer.coxa.rotation.z = pointer.restCoxa.z + motion.reach * 0.18;
    pointer.femur.rotation.x = pointer.restFemur.x + motion.reach * 0.5;

    // Опорные лапы почти неподвижны: она на них стоит. Дрожь только у
    // передней пары, которая на весу.
    fly.legs.forEach((leg, i) => {
      if (leg === pointer) return;
      const standing = i % 3 !== 0;
      const wobble = Math.sin(t * 3 + i) * motion.drive * (standing ? 0.025 : 0.1);
      leg.coxa.rotation.x = leg.restCoxa.x + wobble;
    });

    controls?.update();
    renderer.render(scene, camera);
  }

  return {
    mount(element: HTMLElement) {
      container = element;
      element.appendChild(renderer.domElement);
      controls = new OrbitControls(camera, renderer.domElement);
      controls.enableDamping = true;
      controls.dampingFactor = 0.08;
      controls.enablePan = false;
      controls.minDistance = 2.2;
      controls.maxDistance = 10;
      controls.maxPolarAngle = Math.PI * 0.49;
      controls.target.set(0.25, 0.32, 0);
      this.resize();
      frame = requestAnimationFrame(render);
    },

    update(next: Snapshot | null) {
      snapshot = next;
      phone.draw(next);
      if (!next) return;
      // Момент фиксации слота — единственный триггер жеста. Он приходит из
      // снимка, а не из таймера на клиенте.
      // Рост числа занятых слотов — единственный триггер тяги к экрану.
      const count = next.selection.selected.length;
      if (count > lastSelected) reachUntil = performance.now() + 900;
      lastSelected = count;
    },

    reach(durationMs = 1200) {
      reachUntil = performance.now() + durationMs;
      phone.setPressed(true);
      phone.draw(snapshot);
      window.setTimeout(() => {
        phone.setPressed(false);
        phone.draw(snapshot);
      }, 320);
    },

    resize() {
      if (!container) return;
      const { clientWidth: w, clientHeight: h } = container;
      if (!w || !h) return;
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    },

    /** Габариты для подгонки кадра: муха, телефон и просвет до пола. */
    measure() {
      const box = (o: THREE.Object3D) => new THREE.Box3().setFromObject(o);
      const f = box(fly.group);
      const p = box(phone.group);
      return {
        fly: { min: f.min.toArray(), max: f.max.toArray() },
        phone: { min: p.min.toArray(), max: p.max.toArray() },
        floorY: floor.position.y,
        flyLowestY: f.min.y,
      };
    },

    dispose() {
      cancelAnimationFrame(frame);
      controls?.dispose();
      renderer.dispose();
      renderer.domElement.remove();
    },
  };
}
