"""后台输入立即触发声音，判定交回主线程；播放切换隔离旧输入。"""

from collections import deque
from dataclasses import dataclass
import threading
from time import perf_counter


@dataclass(frozen=True)
class RoutedInput:
    epoch: int
    mode: str | None
    contacting: bool
    host_time: float
    confirmed_time: float
    device_ms: int | None
    music_time: float | None
    accepted: bool
    reason: str


@dataclass(frozen=True)
class JudgmentInput:
    host_time: float
    music_time: float
    source: str
    mode: str


class InputBridge:
    def __init__(self):
        self._lock = threading.RLock()
        self._epoch = 0
        self._route = None
        self._source_epoch = 0
        self._pending = deque(maxlen=2048)
        self.dropped = 0

    def activate(self, engine, generation, mode, tail_seconds, max_age_seconds):
        if mode not in ("game", "calibration"):
            raise ValueError("未知输入模式")
        with self._lock:
            self._epoch += 1
            self._route = (engine, generation, mode, perf_counter(), tail_seconds, max_age_seconds)

    def deactivate(self):
        with self._lock:
            self._epoch += 1
            self._route = None

    def invalidate_source(self):
        with self._lock:
            self._source_epoch += 1

    def source_callbacks(self):
        with self._lock:
            self._source_epoch += 1
            source_epoch = self._source_epoch

        def receive(transition):
            with self._lock:
                if source_epoch == self._source_epoch:
                    self.receive(transition)

        def record(event, **fields):
            with self._lock:
                if source_epoch == self._source_epoch:
                    self.record(event, **fields)

        return receive, record

    def _append(self, item):
        if len(self._pending) == self._pending.maxlen:
            self.dropped += 1
        self._pending.append(item)

    def record(self, event, **fields):
        with self._lock:
            self._append((event, fields))

    def receive(self, transition):
        with self._lock:
            route = self._route
            mode, position, accepted, reason = None, None, False, "inactive"
            if not transition.contacting:
                reason = "release"
            elif route is not None:
                engine, generation, mode, started_at, tail_seconds, max_age = route
                if transition.onset_time < started_at:
                    reason = "before_activation"
                elif perf_counter() - transition.onset_time > max_age:
                    reason = "stale"
                else:
                    reading = engine.reading(transition.onset_time)
                    in_tail = reading.position >= engine.duration and reading.tail_seconds <= tail_seconds
                    if reading.generation == generation and (reading.playing or in_tail):
                        position = reading.position + reading.tail_seconds
                        engine.trigger_clap()
                        accepted, reason = True, "accepted"
                    else:
                        reason = "playback_changed"
            event = RoutedInput(self._epoch, mode, transition.contacting, transition.onset_time,
                                transition.confirmed_time, transition.device_ms, position, accepted, reason)
            self._append(("transition", event))

    def drain(self):
        with self._lock:
            result = list(self._pending)
            self._pending.clear()
            return result

    def is_current(self, event):
        with self._lock:
            return self._route is not None and event.epoch == self._epoch and event.mode == self._route[2]
