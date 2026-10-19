"""接触状态的稳定时间去抖；不把持续接触当作连续拍掌。"""

from dataclasses import dataclass
import math


def parse_line(line):
    if line == "READY":
        return {"type": "ready"}
    parts = line.split(",")
    if (len(parts) == 2 and parts[0] in ("0", "1") and len(parts[1]) <= 10
            and parts[1].isascii() and parts[1].isdigit() and int(parts[1]) <= 0xFFFFFFFF):
        return {"type": "contact" if parts[0] == "1" else "release", "device_ms": int(parts[1])}
    return {"type": "invalid"}


@dataclass(frozen=True)
class ContactTransition:
    contacting: bool
    onset_time: float
    confirmed_time: float
    device_ms: int | None


class ContactDebouncer:
    """使用接收端单调时钟；首次稳定松开后才允许产生拍掌。"""

    def __init__(self, stable_ms=30.0):
        if isinstance(stable_ms, bool) or not math.isfinite(stable_ms) or not 0 <= stable_ms <= 1000:
            raise ValueError("去抖时间必须在 0～1000 毫秒之间")
        self.stable_seconds = stable_ms / 1000
        self.reset()

    def reset(self, released=False):
        self.stable_state = False if released else None
        self._candidate = None
        self._since = None
        self._device_ms = None
        self._last_time = None

    def _check_time(self, timestamp):
        if not math.isfinite(timestamp) or (self._last_time is not None and timestamp < self._last_time):
            raise ValueError("去抖时间必须有限且单调递增")
        self._last_time = timestamp

    def advance(self, timestamp):
        self._check_time(timestamp)
        if self._candidate is None or self._candidate == self.stable_state:
            return []
        if timestamp - self._since + 1e-9 < self.stable_seconds:
            return []
        previous = self.stable_state
        self.stable_state = self._candidate
        # 上电／错误后不能假设已经松开；只建立状态，不生成首次拍掌。
        if previous is None:
            return []
        return [ContactTransition(self.stable_state, self._since, timestamp, self._device_ms)]

    def feed(self, contacting, timestamp, device_ms=None):
        if not isinstance(contacting, bool):
            raise ValueError("接触状态必须为 bool")
        transitions = self.advance(timestamp)
        if contacting != self._candidate:
            self._candidate = contacting
            self._since = timestamp
            self._device_ms = device_ms
        if self.stable_seconds == 0:
            transitions.extend(self.advance(timestamp))
        return transitions
