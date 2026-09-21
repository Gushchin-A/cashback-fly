/**
 * Контракт снимка воркера. Меняется только вместе с `worker/snapshot.py`.
 *
 * Идентификаторы нейронов приходят строками намеренно: 64-битные body id
 * не переживают преобразование в число JavaScript.
 */

export type Phase = "accumulating" | "revealing" | "pause";

export interface Interest {
  id: string;
  title: string;
  tags: string[];
}

export interface Offer {
  id: string;
  title: string;
  note: string | null;
  rate: number;
  tags: string[];
  /** Категория попала под интерес комнаты — её стимул усилен на входе. */
  boosted: boolean;
  /** Средняя частота решающего пула за окна этой категории, Гц. */
  rating: number;
  windows: number;
}

export interface Snapshot {
  schema: number;
  room: {
    id: string;
    title: string;
    interests: Interest[];
    interest_tags: string[];
  };
  month: { id: string; title: string } | null;
  offers: Offer[];
  selection: {
    slots: number;
    /** Занятые слоты по порядку фиксации. Галочки ставить только по нему. */
    selected: string[];
    /** Занятые плюс лидеры среди оставшихся. */
    top: string[];
    complete: boolean;
  };
  presenting: { id: string; boosted: boolean } | null;
  phase: Phase;
  progress: {
    windows_done: number;
    windows_total: number;
    sweep_index: number;
    sweeps: number;
  };
  activity: {
    decision_hz: number;
    decision_history: number[];
    motor_hz: number;
    turn_hz: number;
    sample_ids: string[];
    sample_types: string[];
    sample_counts: number[];
    /** Плоский [индекс, разряды, …]: нули не передаются. */
    cns_sparse: number[];
    cns_total: number;
  };
  spikes: { window: number; total: number };
  sim_ms: number;
  cycles_done: number;
  uptime_seconds: number;
  timestamp: string;
  /** Строго возрастает. По нему отбрасываются устаревшие ответы. */
  sequence: number;
}

export interface RoomIndexEntry {
  id: string;
  title: string;
  snapshot: string;
}

export interface RoomIndex {
  schema: number;
  rooms: RoomIndexEntry[];
  provenance: Record<string, unknown>;
  disclaimer: string;
}

export interface CnsPart {
  id: "brain" | "vnc";
  title: string;
  /** Места в массиве разрядов, к которым относятся координаты. */
  slots: number[];
  x: number[];
  y: number[];
}

/** Статическая карта сом. Координаты не меняются, грузится один раз. */
export interface CnsMap {
  schema: number;
  neurons: number;
  total_with_soma: number;
  parts: CnsPart[];
  source: string;
}
