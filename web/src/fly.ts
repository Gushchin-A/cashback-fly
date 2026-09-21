/**
 * Процедурная low-poly муха.
 *
 * Геометрия своя, собрана из примитивов. Метод построения подсмотрен в
 * клонах stonkfly/FlyTok (`knowledge/refs/`) и повторён, а не скопирован:
 * тело — икосаэдры низкой детализации с плоским затенением, лапы — цепочки
 * цилиндров с шарнирами, крылья — буферная геометрия с жилками-трубками.
 * Готовых моделей и ассетов со стороны здесь нет.
 *
 * Оси: вперёд +X, анатомически правая сторона +Z, вверх +Y.
 *
 * Иерархия важна для анимации: каждая лапа это Group с тремя сегментами,
 * так что достаточно вращать бедро и голень, чтобы лапа тянулась к экрану.
 * Никакой предзаписанной анимации здесь нет — углы задаёт `motion.ts`
 * из реальной активности моторных нейронов.
 */

import * as THREE from "three";

export interface LegParts {
  root: THREE.Group;
  /** Бедро: вращается от тела. */
  coxa: THREE.Group;
  /** Голень: вращается от колена. */
  femur: THREE.Group;
  tip: THREE.Object3D;
  restCoxa: THREE.Euler;
  restFemur: THREE.Euler;
}

export interface FlyParts {
  group: THREE.Group;
  head: THREE.Group;
  thorax: THREE.Mesh;
  abdomen: THREE.Mesh;
  wings: THREE.Group[];
  legs: LegParts[];
  /** Передняя правая — та, что тянется к экрану телефона. */
  pointer: LegParts;
}

const SHELL = 0x2c3540;
const DARK = 0x161c24;
const METAL = 0x5b6673;
const EYE = 0x8e2436;
const WING = 0xaebfcc;

function material(color: number, extra: THREE.MeshStandardMaterialParameters = {}) {
  return new THREE.MeshStandardMaterial({
    color,
    roughness: 0.55,
    metalness: 0.25,
    flatShading: true,
    ...extra,
  });
}

/** Икосаэдр низкой детализации, растянутый по осям: основной объём тела. */
function orb(
  parent: THREE.Object3D,
  scale: [number, number, number],
  at: [number, number, number],
  mat: THREE.Material,
  detail = 1,
): THREE.Mesh {
  const mesh = new THREE.Mesh(new THREE.IcosahedronGeometry(1, detail), mat);
  mesh.scale.set(...scale);
  mesh.position.set(...at);
  mesh.castShadow = true;
  parent.add(mesh);
  return mesh;
}

/** Цилиндр между двумя точками — сегмент лапы или жилка. */
function rod(
  parent: THREE.Object3D,
  a: [number, number, number],
  b: [number, number, number],
  radius: number,
  mat: THREE.Material,
  radial = 5,
): THREE.Mesh {
  const start = new THREE.Vector3(...a);
  const end = new THREE.Vector3(...b);
  const direction = end.clone().sub(start);
  const mesh = new THREE.Mesh(
    new THREE.CylinderGeometry(radius * 0.8, radius, direction.length(), radial),
    mat,
  );
  mesh.position.copy(start).add(end).multiplyScalar(0.5);
  mesh.quaternion.setFromUnitVectors(
    new THREE.Vector3(0, 1, 0),
    direction.clone().normalize(),
  );
  mesh.castShadow = true;
  parent.add(mesh);
  return mesh;
}

function buildWing(parent: THREE.Object3D, side: 1 | -1): THREE.Group {
  const wing = new THREE.Group();
  wing.position.set(-0.16, 0.34, 0.2 * side);
  parent.add(wing);

  // Контур крыла: вытянутая капля, задаётся вручную, чтобы силуэт читался.
  const outline: [number, number, number][] = [
    [0.08, 0, 0.04 * side],
    [-0.35, 0.03, 0.34 * side],
    [-0.95, 0.05, 0.52 * side],
    [-1.5, 0.04, 0.46 * side],
    [-1.74, 0.01, 0.24 * side],
    [-1.5, -0.01, 0.06 * side],
    [-0.7, 0, 0.02 * side],
  ];

  const shape: number[] = [];
  for (let i = 1; i < outline.length - 1; i++) {
    shape.push(...outline[0], ...outline[i], ...outline[i + 1]);
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(shape, 3));
  geometry.computeVertexNormals();
  const membrane = new THREE.Mesh(
    geometry,
    material(WING, {
      transparent: true,
      opacity: 0.42,
      side: THREE.DoubleSide,
      roughness: 0.2,
      metalness: 0.4,
    }),
  );
  membrane.castShadow = false;
  wing.add(membrane);

  const vein = material(0x8fa3b4, { roughness: 0.35 });
  const loop = [...outline, outline[0]];
  for (let i = 0; i < loop.length - 1; i++) {
    rod(wing, loop[i], loop[i + 1], 0.008, vein, 4);
  }
  for (let i = 2; i < 5; i++) {
    rod(wing, [-0.1, 0, 0.02 * side], outline[i], 0.005, vein, 3);
  }
  return wing;
}

function buildLeg(parent: THREE.Object3D, index: number, side: 1 | -1): LegParts {
  const root = new THREE.Group();
  // Три пары лап вдоль груди: передняя ближе к голове.
  root.position.set(0.3 - index * 0.34, -0.05, 0.22 * side);
  parent.add(root);

  const metal = material(METAL, { roughness: 0.4, metalness: 0.5 });
  const dark = material(DARK);

  // Каждый сустав — вложенная группа, чтобы вращать сегменты независимо.
  const coxa = new THREE.Group();
  root.add(coxa);
  const kneeAt: [number, number, number] = [0.1 * side, -0.42, 0.42 * side];
  rod(coxa, [0, 0, 0], kneeAt, 0.032, metal);
  orb(coxa, [0.05, 0.05, 0.05], kneeAt, material(SHELL), 0);

  const femur = new THREE.Group();
  femur.position.set(...kneeAt);
  coxa.add(femur);
  const ankleAt: [number, number, number] = [0.12 * side, -0.46, 0.2 * side];
  rod(femur, [0, 0, 0], ankleAt, 0.02, metal);

  const tip = new THREE.Object3D();
  tip.position.set(ankleAt[0] * 1.25, ankleAt[1] * 1.3, ankleAt[2] * 1.4);
  femur.add(tip);
  rod(femur, ankleAt, tip.position.toArray() as [number, number, number], 0.011, dark);

  // Поза покоя: муха СТОИТ. Задние две пары упираются в пол и разведены
  // наружу, передняя пара вынесена вперёд — ею она и тянется к телефону.
  // От этой позы отсчитываются все отклонения при анимации.
  const spread = side * (0.1 + index * 0.16);
  if (index === 0) {
    // Передняя пара: вынесена вперёд и приподнята.
    coxa.rotation.set(-0.55, 0, spread * 0.6);
    femur.rotation.set(0.75, 0, -spread * 0.4);
  } else {
    // Средняя и задняя: уперты в пол, чем дальше назад, тем шире развод.
    coxa.rotation.set(0.28 + index * 0.22, 0, spread);
    femur.rotation.set(-0.12, 0, -spread * 0.5);
  }

  return {
    root,
    coxa,
    femur,
    tip,
    restCoxa: coxa.rotation.clone(),
    restFemur: femur.rotation.clone(),
  };
}

export function buildFly(): FlyParts {
  const group = new THREE.Group();

  const shell = material(SHELL);
  const dark = material(DARK, { metalness: 0.2 });

  // Брюшко с сегментными кольцами.
  const abdomen = orb(group, [0.82, 0.36, 0.4], [-0.78, -0.02, 0], dark);
  for (let i = 0; i < 5; i++) {
    const ring = new THREE.Mesh(
      new THREE.TorusGeometry(0.33 - i * 0.045, 0.03, 4, 12),
      material(0x212a34 + i * 0x040404),
    );
    ring.position.set(-0.6 - i * 0.16, -0.01, 0);
    ring.rotation.y = Math.PI / 2;
    ring.scale.z = 0.92;
    group.add(ring);
  }

  const thorax = orb(group, [0.6, 0.48, 0.45], [-0.04, 0.06, 0], shell);

  // Щетинки на груди — мелкая деталь, ловит контровой свет.
  const bristle = material(0x0e1318);
  for (let i = 0; i < 6; i++) {
    const angle = (i / 6) * Math.PI * 2;
    rod(
      group,
      [-0.04 + Math.cos(angle) * 0.18, 0.4, Math.sin(angle) * 0.2],
      [-0.04 + Math.cos(angle) * 0.26, 0.62, Math.sin(angle) * 0.3],
      0.009,
      bristle,
      3,
    );
  }

  // Голова: отдельная группа, чтобы поворачивалась от DNa02.
  const head = new THREE.Group();
  head.position.set(0.58, 0.16, 0);
  group.add(head);
  orb(head, [0.37, 0.36, 0.36], [0, 0, 0], material(0x38434f));

  const eyeMaterial = material(EYE, {
    roughness: 0.25,
    metalness: 0.15,
    emissive: 0x3a0d16,
  });
  for (const side of [1, -1] as const) {
    orb(head, [0.26, 0.34, 0.23], [0.1, 0.03, 0.26 * side], eyeMaterial, 2);
    // Усики.
    rod(
      head,
      [0.22, 0.26, 0.13 * side],
      [0.44, 0.44, 0.2 * side],
      0.011,
      material(METAL),
      4,
    );
    orb(head, [0.035, 0.025, 0.025], [0.44, 0.44, 0.2 * side], dark, 0);
  }
  // Хоботок.
  rod(head, [0.25, -0.16, 0], [0.46, -0.32, 0], 0.04, material(METAL), 5);
  orb(head, [0.055, 0.07, 0.1], [0.46, -0.32, 0], dark, 0);

  const wings = [buildWing(group, 1), buildWing(group, -1)];

  const legs: LegParts[] = [];
  for (const side of [1, -1] as const) {
    for (let i = 0; i < 3; i++) legs.push(buildLeg(group, i, side));
  }
  // Передняя правая: индекс 0 при side = +1.
  const pointer = legs[0];

  return { group, head, thorax, abdomen, wings, legs, pointer };
}
