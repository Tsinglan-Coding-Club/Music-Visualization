"""纯音乐时间轴与节奏判定；时间均为音频内容秒，不读取系统时钟。"""

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from fractions import Fraction
import math
from numbers import Real
from typing import Sequence


# 仅吸收浮点运算误差，不构成额外的玩法容差。
_EPSILON = 1e-12


def _finite(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} 必须为有限数值")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name} 必须为有限数值") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} 必须为有限数值")
    return result


def _positive_integer(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} 必须为正整数")
    return value


@dataclass(frozen=True)
class Target:
    """目标起音；index 从 0 开始，bar 从 1 开始，denominator 为音符分母。"""

    index: int
    time: float
    denominator: int
    bar: int


@dataclass(frozen=True)
class Judgment:
    """一次输入结果；error_ms 负值为早拍，正值为晚拍。"""

    status: str
    target: Target | None
    error_ms: float | None


class RhythmChart:
    """固定 BPM、以四分音符为一拍、整小节模板循环的测试谱面。

    beat_offset_ms 全局平移所有目标。note_values 的每项持续 4 / 分母拍，
    总和必须恰为 beats_per_bar。所有起音严格早于 duration_seconds。
    targets 按时间排序；构造后调用方不应修改该列表。
    """

    def __init__(
        self,
        bpm: float,
        beats_per_bar: int,
        beat_start_seconds: float,
        beat_offset_ms: float,
        note_values: Sequence[int],
        duration_seconds: float,
    ) -> None:
        self.bpm = _finite("bpm", bpm)
        if self.bpm <= 0:
            raise ValueError("bpm 必须大于 0")
        self.beats_per_bar = _positive_integer("beats_per_bar", beats_per_bar)
        beat_start = _finite("beat_start_seconds", beat_start_seconds)
        offset = _finite("beat_offset_ms", beat_offset_ms)
        self.duration_seconds = _finite("duration_seconds", duration_seconds)
        if self.duration_seconds <= 0:
            raise ValueError("duration_seconds 必须大于 0")
        self.start = beat_start + offset / 1000
        if not math.isfinite(self.start) or self.start < 0:
            raise ValueError("校正后的首拍时间必须为有限非负值")
        if self.start >= self.duration_seconds:
            raise ValueError("音频必须包含校正后的首个目标拍点")
        try:
            self.note_values = tuple(note_values)
        except TypeError as exc:
            raise ValueError("note_values 必须为非空音符分母序列") from exc
        if not self.note_values:
            raise ValueError("note_values 不能为空")
        offsets = []
        beats = Fraction(0)
        for value in self.note_values:
            _positive_integer("音符分母", value)
            offsets.append(beats)
            beats += Fraction(4, value)
        if beats != self.beats_per_bar:
            raise ValueError("节奏模板的总时值必须恰好为一个小节")
        self.beat_seconds = 60 / self.bpm
        self.bar_seconds = self.beat_seconds * self.beats_per_bar
        if not math.isfinite(self.bar_seconds) or self.bar_seconds <= 0:
            raise ValueError("小节时长必须为有限正数")

        bar_extent = (self.duration_seconds - self.start) / self.bar_seconds
        if not math.isfinite(bar_extent):
            raise ValueError("BPM 与音频时长产生了不可表示的小节数")
        bar_count = math.ceil(bar_extent)
        self.targets: list[Target] = []
        for bar_index in range(bar_count):
            for denominator, beat_offset in zip(self.note_values, offsets):
                # 每个时间直接来自固定音乐起点，不累加先前目标或输入误差。
                time = self.start + float(bar_index * self.beats_per_bar + beat_offset) * self.beat_seconds
                if time < self.duration_seconds:
                    self.targets.append(Target(len(self.targets), time, denominator, bar_index + 1))
        self._times = tuple(target.time for target in self.targets)
        self._downbeat_times = self._times[::len(self.note_values)]

    def next_bar(self, time: float) -> float | None:
        """返回不早于 time 的下一小节第一拍；音频结束后返回 None。"""
        time = _finite("time", time)
        position = (time - self.start) / self.bar_seconds
        nearest = round(position)
        if math.isclose(position, nearest, rel_tol=0, abs_tol=_EPSILON):
            position = nearest
        bar_index = max(0, math.ceil(position))
        downbeat = self.start + (bar_index * self.beats_per_bar) * self.beat_seconds
        return downbeat if downbeat < self.duration_seconds else None

    def bar_at(self, time: float) -> tuple[int, float] | None:
        """返回 (从 1 开始的小节号, 从 1 开始的四分音符拍位置)。

        例如四四拍的小节中间为 (小节号, 3.0)。首拍前及 EOF 起返回 None。
        """
        time = _finite("time", time)
        if time < self.start or time >= self.duration_seconds:
            return None
        beat_position = (time - self.start) / self.beat_seconds
        nearest = round(beat_position)
        if math.isclose(beat_position, nearest, rel_tol=0, abs_tol=_EPSILON):
            beat_position = float(nearest)
        bar_index = math.floor(beat_position / self.beats_per_bar)
        return bar_index + 1, beat_position - bar_index * self.beats_per_bar + 1

    def visible_targets(
        self, time: float, lookahead: float, lookbehind: float = 0.3
    ) -> list[Target]:
        """用二分查找返回闭区间 [time-lookbehind, time+lookahead] 的目标。"""
        time = _finite("time", time)
        lookahead = _finite("lookahead", lookahead)
        lookbehind = _finite("lookbehind", lookbehind)
        if lookahead < 0 or lookbehind < 0:
            raise ValueError("可见时间范围必须非负")
        left = bisect_left(self._times, time - lookbehind)
        right = bisect_right(self._times, time + lookahead)
        return self.targets[left:right]


class RhythmSession:
    """由首拍拍掌启动、仅覆盖一个小节的最近目标判定。

    try_start 用校正后的拍掌时间寻找触发小节第一拍，再等待 lead_in_bars
    个小节进入正式判定。lead_in_bars=0 时 judge 同一事件可命中第一目标；
    大于 0 时触发拍掌不计正式成绩。未成功的启动不改变上一轮结果。
    arm 保留为显式选择正式进入小节的测试接口，不增加准备期。
    正 input_offset_ms 表示输入迟到，judge 会从输入音乐时间中扣除此值。
    输入时间戳必须按非递减顺序调用 judge；画面应先处理事件，再 advance。
    advance 仅在目标窗口及正输入偏移余量过去后记 miss；结果不会被翻转。
    states 以目标 index 为键，仅含当前轮已经结算的 hit / miss。
    """

    def __init__(
        self,
        chart: RhythmChart,
        window_ms: float,
        input_offset_ms: float = 0,
        lead_in_bars: int = 0,
    ) -> None:
        self.chart = chart
        self.window_ms = _finite("window_ms", window_ms)
        self.input_offset_ms = _finite("input_offset_ms", input_offset_ms)
        if self.window_ms < 0:
            raise ValueError("window_ms 必须非负")
        if isinstance(lead_in_bars, bool) or not isinstance(lead_in_bars, int) or lead_in_bars < 0:
            raise ValueError("lead_in_bars 必须为非负整数")
        self.lead_in_bars = lead_in_bars
        self._window = self.window_ms / 1000
        self._offset = self.input_offset_ms / 1000
        self.reset()

    def reset(self) -> None:
        """清空本轮，包括输入时间戳顺序检查。"""
        self.entry_time: float | None = None
        self.trigger_time: float | None = None
        self.round_end_time: float | None = None
        self.states: dict[int, str] = {}
        self._entry_index = len(self.chart.targets)
        self._end_index = self._entry_index
        self._miss_cursor = self._entry_index
        self._armed_at: float | None = None
        self._last_input: float | None = None
        self._advanced_time: float | None = None
        self._hits = 0
        self._misses = 0

    def _enter(self, entry_index: int, armed_at: float, trigger_time: float | None = None) -> None:
        self.reset()
        self._entry_index = entry_index
        self._end_index = min(entry_index + len(self.chart.note_values), len(self.chart.targets))
        self.entry_time = self.chart.targets[entry_index].time
        self.trigger_time = self.entry_time if trigger_time is None else trigger_time
        self.round_end_time = min(self.entry_time + self.chart.bar_seconds, self.chart.duration_seconds)
        self._armed_at = armed_at
        self._miss_cursor = entry_index

    def try_start(self, music_time: float) -> bool:
        """拍掌在首拍的闭窗口内时触发，正式进入由 lead_in_bars 决定。

        用 music_time - input_offset_ms / 1000 匹配首拍，等距选较早首拍。
        trigger_time 为触发首拍，entry_time 为准备期之后的正式首拍。
        准备期及正式小节内不可重启；无效输入或正式首拍不在音频内时保留旧结果。
        lead_in_bars=0 时调用方应 judge 同一事件，将触发拍掌计入第一目标。
        """
        music_time = _finite("music_time", music_time)
        if self._last_input is not None and music_time < self._last_input:
            raise ValueError("输入音乐时间戳必须按非递减顺序处理")
        corrected = music_time - self._offset
        if self.round_end_time is not None and corrected < self.round_end_time - _EPSILON:
            return False
        downbeats = self.chart._downbeat_times
        insertion = bisect_left(downbeats, corrected)
        candidates = []
        if insertion > 0:
            candidates.append(insertion - 1)
        if insertion < len(downbeats):
            candidates.append(insertion)
        selected = candidates[0]
        if len(candidates) == 2:
            earlier, later = candidates
            if downbeats[later] - corrected < corrected - downbeats[earlier] - _EPSILON:
                selected = later
        trigger_time = downbeats[selected]
        if abs(corrected - trigger_time) > self._window + _EPSILON:
            return False
        if self.trigger_time is not None and trigger_time <= self.trigger_time + _EPSILON:
            return False
        entry_bar_index = selected + self.lead_in_bars
        if entry_bar_index >= len(downbeats):
            return False
        self._enter(entry_bar_index * len(self.chart.note_values), music_time, trigger_time)
        return True

    def arm(self, time: float) -> float | None:
        """选择 >= time 的小节第一拍，返回进入时间；无后续小节则解除进入。"""
        time = _finite("time", time)
        self.reset()
        entry_time = self.chart.next_bar(time)
        if entry_time is not None:
            entry_index = bisect_left(self.chart._times, entry_time - _EPSILON)
            self._enter(entry_index, time)
        return self.entry_time

    @property
    def hits(self) -> int:
        return self._hits

    @property
    def misses(self) -> int:
        return self._misses

    @property
    def finished(self) -> bool:
        """一小节已结束（含正输入偏移余量），且本轮所有目标已结算。

        最后一目标提前命中不会令整轮提前结束；需继续调用 advance。
        """
        return (
            self.entry_time is not None
            and self._advanced_time is not None
            and self._advanced_time >= self.round_end_time + max(self._offset, 0) - _EPSILON
            and len(self.states) == self._end_index - self._entry_index
        )

    def judge(self, music_time: float) -> Judgment:
        """匹配最近目标；等距选较早目标，容差两端包含在窗口内。

        status 为 waiting / early / late / hit / duplicate。已漏拍目标返回 late，
        已命中目标仅在窗口内返回 duplicate；两者均不转而寻找更远的目标。
        arm 时刻之后、进入窗口内的提前输入可命中第一目标。
        """
        music_time = _finite("music_time", music_time)
        if self.entry_time is None or music_time < self._armed_at:
            return Judgment("waiting", None, None)
        if self._last_input is not None and music_time < self._last_input:
            raise ValueError("输入音乐时间戳必须按非递减顺序处理")
        self._last_input = music_time
        corrected = music_time - self._offset
        if corrected < self.entry_time - self._window - _EPSILON:
            return Judgment("waiting", None, None)
        insertion = bisect_left(self.chart._times, corrected, self._entry_index, self._end_index)
        candidates = []
        if insertion > self._entry_index:
            candidates.append(self.chart.targets[insertion - 1])
        if insertion < self._end_index:
            candidates.append(self.chart.targets[insertion])
        target = candidates[0]
        if len(candidates) == 2:
            earlier, later = candidates
            if later.time - corrected < corrected - earlier.time - _EPSILON:
                target = later
        error = corrected - target.time
        if self.states.get(target.index) == "miss":
            return Judgment("late", target, error * 1000)
        if abs(error) > self._window + _EPSILON:
            return Judgment("early" if error < 0 else "late", target, error * 1000)
        if self.states.get(target.index) == "hit":
            return Judgment("duplicate", target, error * 1000)
        self.states[target.index] = "hit"
        self._hits += 1
        return Judgment("hit", target, error * 1000)

    def advance(self, music_time: float) -> None:
        """把已过 target+窗口+正输入偏移余量的未命中目标记为漏拍。"""
        music_time = _finite("music_time", music_time)
        if self.entry_time is None:
            return
        self._advanced_time = max(music_time, self._advanced_time if self._advanced_time is not None else music_time)
        allowance = self._window + max(self._offset, 0)
        while self._miss_cursor < self._end_index:
            target = self.chart.targets[self._miss_cursor]
            if music_time <= target.time + allowance + _EPSILON:
                break
            if target.index not in self.states:
                self.states[target.index] = "miss"
                self._misses += 1
            self._miss_cursor += 1
