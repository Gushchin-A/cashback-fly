"""Воркер: один процесс, N комнат, снимки на диск.

Число комнат берётся из `config/rooms.json` — по флагу `enabled`. Одна, две,
три или пять поднимаются без правок кода, различается только сколько потоков
и сколько снимков. Проводка при этом одна копия в памяти на всех.

Каждая комната — свой поток. Ядро вызывается через ctypes, который отпускает
GIL на время счёта, поэтому потоки действительно считают параллельно. Барьера
между комнатами нет: в Этапе 0 замер показал, что синхронные пачки на M4
упираются в четыре производительных ядра и на пятом потоке деградируют, а
свободный ход распределяется ровно.

Зритель на расчёт не влияет никак. Снимки пишутся по таймеру независимо от
того, читает их кто-нибудь или нет.

Запуск:
    .venv/bin/python -m worker.main
    .venv/bin/python -m worker.main --snapshots snapshots --once
"""

import argparse
import signal
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from neural.flybrain import FlyBrain, enabled_rooms, load_config

from .snapshot import build, snapshot_path, write_atomic, write_index


class Room:
    """Один цикл комнаты: накопление → фиксация → пауза → следующий месяц."""

    def __init__(self, worker, config, state_id):
        self.w = worker
        self.config = config
        self.sid = state_id
        self.phase = "accumulating"
        self.sequence = 0
        self.cycles_done = 0
        self.pause_until = 0.0
        self.window_budget = 0.0
        # Только для мутаций `_State` (step/start_month) — публикатор его не
        # берёт вовсе, см. snapshot() и _refresh_readout().
        self.lock = threading.Lock()
        # Копия последнего показания, всегда согласованная (readout() уже
        # копирует все поля в обычные list/dict, живых ссылок на массивы
        # состояния не остаётся) — публикатор читает её без лока.
        self._readout = None
        self.checkpoint_dir = worker.state_root / config["id"]

    def _refresh_readout(self):
        """Скопировать показание сразу после мутации `_State`.

        Вызывать только пока держишь self.lock — readout() читает те же
        поля, которые step()/start_month() только что писали.
        """
        self._readout = self.w.fb.readout(self.sid)

    def restore_or_start(self):
        # Однопоточный контекст (до старта потоков в Worker.run) — лок не нужен.
        fb = self.w.fb
        if fb.load_state(self.sid, self.checkpoint_dir):
            r = fb.readout(self.sid)
            self._readout = r
            self.w.log(
                f"{self.config['id']}: состояние восстановлено, "
                f"месяц {r.month}, окон пройдено {r.windows_done}/{r.windows_total}"
            )
            if fb.cycle_complete(self.sid):
                # Цикл был закрыт до остановки: досиживаем паузу и идём дальше.
                self.begin_pause()
            return
        fb.start_month(self.sid, fb.next_month())
        self._refresh_readout()
        self.w.log(f"{self.config['id']}: начинаем с месяца {fb.next_month()}")

    def begin_pause(self):
        self.phase = "pause"
        self.pause_until = time.monotonic() + self.w.pause_seconds

    def advance(self):
        """Один шаг цикла. Возвращает, сколько ждать до следующего."""
        fb = self.w.fb
        if self.phase == "pause":
            remaining = self.pause_until - time.monotonic()
            if remaining > 0:
                return min(remaining, 0.25)
            # Пауза кончилась — следующий месяц в кольце. start_month пишет
            # сразу несколько полей `_State` не атомарно (порядок, расписание,
            # рейтинги, ...) — публикатор не должен увидеть их наполовину
            # обновлёнными, поэтому это тоже под локом, как и step().
            with self.lock:
                current = fb.readout(self.sid).month
                fb.start_month(self.sid, fb.next_month(current))
                self._refresh_readout()
            self.phase = "accumulating"
            self.window_budget = self.w.window_target(self.sid)
            return 0.0

        started = time.monotonic()
        with self.lock:
            fb.step(self.sid)
            st = fb._states[self.sid]
            self.phase = (
                "revealing" if st.cursor >= len(st.schedule) else "accumulating"
            )
            # Копия показания снимается сразу после шага, пока лок ещё держим:
            # readout() сам по себе быстрый (копирует немного списков/словарей,
            # не считает), поэтому публикатор ждёт только эту копию, а не
            # весь fb.step() — см. _refresh_readout и snapshot() ниже.
            self._refresh_readout()
        spent = time.monotonic() - started

        if fb.cycle_complete(self.sid):
            self.cycles_done += 1
            r = self._readout
            picked = ", ".join(
                fb.categories["categories"][c]["title"] for c in r.selected
            )
            self.w.log(f"{self.config['id']} [{r.month}] выбрано: {picked}")
            self.w.save_room(self)
            self.begin_pause()
            return 0.0

        # Темп: если считаем быстрее целевого цикла, ждём. Если медленнее —
        # не ждём вовсе, и цикл просто длиннее заявленного. На слабой машине
        # это честнее, чем делать вид, что мы уложились.
        return max(0.0, self.window_budget - spent)

    def snapshot(self):
        """Публикатор больше не берёт self.lock вообще.

        `self._readout` — уже независимая копия (readout() копирует все поля
        в обычные list/dict, живых ссылок на массивы состояния не остаётся),
        а `self.sequence`/`self.phase` мутирует только этот же поток
        (run_room), поэтому читать их отсюда без лока безопасно. До этой
        правки публикатор ждал на self.lock весь fb.step(), хотя реально ему
        нужно было только уже скопированное показание — из-за этого шаг
        симуляции мог надолго задержать запись снимка, и это была причина
        подвисания UI при смене месяца на живом сервере.
        """
        self.sequence += 1
        readout = self._readout
        if readout is None:  # до первого шага теоретически возможно
            readout = self.w.fb.readout(self.sid)
        return build(
            self.w.fb, self.sid, self.config,
            readout=readout,
            phase=self.phase,
            sequence=self.sequence,
            started_at=self.w.started_at,
            cycles_done=self.cycles_done,
        )


class Worker:
    def __init__(self, args):
        self.args = args
        self.snapshot_root = Path(args.snapshots)
        self.state_root = Path(args.state)
        self.stop = threading.Event()
        self.started_at = datetime.now(timezone.utc)
        self.config = load_config("config/simulation.json")
        self.cycle_seconds = float(self.config["cycle_seconds"])
        self.pause_seconds = float(self.config["pause_seconds"])
        self.snapshot_interval = float(args.snapshot_interval)

        rooms = enabled_rooms()
        if not rooms:
            raise SystemExit("В config/rooms.json нет ни одной включённой комнаты")
        self.log(f"Комнат включено: {len(rooms)} — {', '.join(r['id'] for r in rooms)}")

        started = time.perf_counter()
        self.fb = FlyBrain()
        self.rooms = [
            Room(self, cfg, self.fb.create_state(cfg["interests"], room=cfg["id"]))
            for cfg in rooms
        ]
        n = len(self.rooms)
        plural = "комнату" if n % 10 == 1 and n % 100 != 11 else (
            "комнаты" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else "комнат"
        )
        self.log(
            f"Граф загружен за {time.perf_counter() - started:.1f} с: "
            f"{self.fb.n} нейронов, одна копия проводки на {n} {plural}"
        )
        for room in self.rooms:
            room.restore_or_start()
            room.window_budget = self.window_target(room.sid)

    def log(self, message):
        stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print(f"[{stamp}] {message}", flush=True)

    def window_target(self, state_id):
        """Сколько секунд отводится на одно окно, чтобы цикл занял заявленное."""
        windows = max(1, self.fb.windows_per_cycle(state_id))
        return max(0.0, (self.cycle_seconds - self.pause_seconds) / windows)

    def save_room(self, room):
        try:
            self.fb.save_state(room.sid, room.checkpoint_dir)
        except Exception as e:  # чекпойнт не должен ронять вещание
            self.log(f"{room.config['id']}: чекпойнт не сохранён: {e}")

    # ---------- потоки ----------

    def run_room(self, room):
        while not self.stop.is_set():
            try:
                wait = room.advance()
            except Exception as e:
                self.log(f"{room.config['id']}: сбой шага: {e}")
                self.stop.set()
                raise
            if wait > 0:
                self.stop.wait(wait)

    def run_publisher(self):
        """Снимки пишутся по таймеру, независимо от темпа комнат."""
        while not self.stop.is_set():
            deadline = time.monotonic() + self.snapshot_interval
            for room in self.rooms:
                try:
                    write_atomic(
                        snapshot_path(self.snapshot_root, room.config["id"]),
                        room.snapshot(),
                    )
                except Exception as e:
                    self.log(f"{room.config['id']}: снимок не записан: {e}")
            self.stop.wait(max(0.0, deadline - time.monotonic()))

    def run(self):
        write_index(
            self.snapshot_root,
            [r.config for r in self.rooms],
            self.fb.provenance(),
        )
        # Координаты сом не меняются — отдаём один раз отдельным файлом,
        # в снимке едут только разряды этих же нейронов.
        write_atomic(self.snapshot_root / "cns.json", self.fb.cns_map())
        threads = [
            threading.Thread(target=self.run_room, args=(r,), name=r.config["id"])
            for r in self.rooms
        ]
        threads.append(threading.Thread(target=self.run_publisher, name="publisher"))
        for t in threads:
            t.daemon = True
            t.start()

        if self.args.once:
            # Режим проверки: ждём, пока каждая комната закроет один цикл.
            while not self.stop.is_set() and any(
                r.cycles_done == 0 for r in self.rooms
            ):
                self.stop.wait(0.25)
            self.stop.set()

        while not self.stop.is_set():
            self.stop.wait(0.5)
        for t in threads:
            t.join(timeout=30)
        self.shutdown()

    def shutdown(self):
        self.log("Останавливаемся, сохраняем состояние")
        for room in self.rooms:
            self.save_room(room)
            try:
                write_atomic(
                    snapshot_path(self.snapshot_root, room.config["id"]),
                    room.snapshot(),
                )
            except Exception as e:
                self.log(f"{room.config['id']}: финальный снимок не записан: {e}")
        self.log("Состояние сохранено")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--snapshots", default="snapshots", help="куда писать снимки")
    p.add_argument("--state", default="checkpoints", help="куда писать чекпойнты")
    p.add_argument("--snapshot-interval", type=float, default=1.0)
    p.add_argument(
        "--once", action="store_true",
        help="остановиться, когда каждая комната закроет один цикл",
    )
    a = p.parse_args()

    worker = Worker(a)

    def handle(signum, frame):
        worker.log(f"Получен сигнал {signal.Signals(signum).name}")
        worker.stop.set()

    signal.signal(signal.SIGINT, handle)
    signal.signal(signal.SIGTERM, handle)
    worker.run()


if __name__ == "__main__":
    main()
