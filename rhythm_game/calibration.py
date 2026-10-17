"""固定速度的跟拍相位估计；不把主观跟拍当成硬件延迟测量。"""

from dataclasses import dataclass
import math
from statistics import median


@dataclass(frozen=True)
class CalibrationEstimate:
    count: int
    used: int
    rejected: int
    offset_ms: float
    first_beat_seconds: float
    spread_ms: float
    ready: bool
    message: str


class BeatCalibrator:
    """拍每个四分音符；估计最接近当前配置的相位，范围为当前值 ±半拍。

    跟拍无法独立区分音乐起点、输入延迟和人的反应偏差，也不能识别整拍
    或整小节错位。因此输入补偿固定使用已设置值，节奏起点由人工粗定位。
    """

    minimum_samples = 8
    maximum_samples = 64

    def __init__(self, bpm, base_start_seconds, current_offset_ms, input_offset_ms):
        values = (bpm, base_start_seconds, current_offset_ms, input_offset_ms)
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
            raise ValueError("校准参数必须是有限数字")
        if bpm <= 0 or base_start_seconds < 0 or base_start_seconds + current_offset_ms / 1000 < 0:
            raise ValueError("校准速度和首拍位置无效")
        self.period = 60 / bpm
        self.base = base_start_seconds
        self.center = current_offset_ms / 1000
        self.input_offset = input_offset_ms / 1000
        self.samples = []
        self.rejected = 0
        self._last_time = None
        self._last_index = None

    def _wrap(self, value):
        return (value + self.period / 2) % self.period - self.period / 2

    def add(self, music_time):
        if isinstance(music_time, bool) or not isinstance(music_time, (int, float)) or not math.isfinite(music_time):
            raise ValueError("拍掌时间必须是有限数字")
        corrected = music_time - self.input_offset
        index = math.floor((corrected - self.base - self.center) / self.period + 0.5)
        if (index < 0 or self._last_time is not None and corrected - self._last_time < self.period * 0.35
                or self._last_index is not None and index <= self._last_index):
            self.rejected += 1
            return False
        self._last_time, self._last_index = corrected, index
        self.samples.append(self.center + self._wrap(corrected - self.base - index * self.period - self.center))
        if len(self.samples) > self.maximum_samples:
            self.samples.pop(0)
        return True

    def estimate(self):
        if not self.samples:
            return CalibrationEstimate(0, 0, self.rejected, self.center * 1000,
                                       self.base + self.center, 0, False, "请听音乐，每一拍按一次空格")
        # 先在圆周上寻找稳健中心，再展开；避免 ±半拍附近的值直接平均为零。
        seed = min(self.samples, key=lambda candidate: median(abs(self._wrap(v - candidate)) for v in self.samples))
        unwrapped = [seed + self._wrap(value - seed) for value in self.samples]
        center = median(unwrapped)
        deviations = [abs(value - center) for value in unwrapped]
        mad = median(deviations)
        cutoff = max(0.040, min(self.period * 0.20, 3 * 1.4826 * mad))
        inliers = [value for value in unwrapped if abs(value - center) <= cutoff]
        center = median(inliers)
        errors = sorted(abs(value - center) for value in inliers)
        spread = errors[max(0, math.ceil(len(errors) * 0.90) - 1)] * 1000
        offset = self.center + self._wrap(center - self.center)
        boundary = abs(self._wrap(offset - self.center)) > self.period / 2 - 0.015
        ready = (len(inliers) >= self.minimum_samples and len(inliers) >= len(self.samples) * 0.75
                 and spread <= 60 and not boundary and self.base + offset >= 0)
        if boundary:
            message = "接近半拍边界，请先手动粗调拍点偏移再校准"
        elif len(inliers) < self.minimum_samples:
            message = f"还需稳定拍掌：有效 {len(inliers)} / {self.minimum_samples}"
        elif not ready:
            message = "跟拍不够稳定，请继续采样或重新校准"
        else:
            message = "结果可预览；加入左侧设置后保存应用"
        return CalibrationEstimate(len(self.samples), len(inliers),
                                   self.rejected + len(self.samples) - len(inliers),
                                   round(offset * 1000, 2), self.base + offset, spread, ready, message)
