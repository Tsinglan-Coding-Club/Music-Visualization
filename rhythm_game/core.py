"""纯音乐时间轴与节奏判定；时间均为音频内容秒，不读取系统时钟。"""

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from fractions import Fraction
import math
from numbers import Real
from typing import Sequence

from .notation import normalize_pattern, parse_note


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


def _nonnegative_integer(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} 必须为非负整数")
    return value


@dataclass(frozen=True)
class Target:
    """目标起音；index 从 0 开始，bar 从 1 开始。"""

    index: int
    time: float
    denominator: int
    bar: int
    dots: int = 0
    beat_offset: Fraction = Fraction(0)
    duration_beats: Fraction | None = None


@dataclass(frozen=True)
class Judgment:
    """一次输入结果；error_ms 负值为早拍，正值为晚拍。"""

    status: str
    target: Target | None
    error_ms: float | None


class RhythmChart:
    """固定 BPM 谱面；各小节模板按顺序循环，时值采用精确分数。"""

    def __init__(
        self,
        bpm: float,
        beats_per_bar: int,
        beat_start_seconds: float,
        beat_offset_ms: float,
        note_values: Sequence[int | str],
        duration_seconds: float,
        *,
        patterns: Sequence[Sequence[int | str]] | None = None,
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
            self.patterns = tuple(
                normalize_pattern(pattern, self.beats_per_bar)
                for pattern in ((note_values,) if patterns is None else patterns)
            )
        except TypeError as exc:
            raise ValueError("节奏模板必须为非空音符序列") from exc
        if not self.patterns:
            raise ValueError("节奏模板不能为空")
        self.note_values = self.patterns[0]
        self.beat_seconds = 60 / self.bpm
        self.bar_seconds = self.beat_seconds * self.beats_per_bar
        if not math.isfinite(self.bar_seconds) or self.bar_seconds <= 0:
            raise ValueError("小节时长必须为有限正数")
        bar_extent = (self.duration_seconds - self.start) / self.bar_seconds
        if not math.isfinite(bar_extent):
            raise ValueError("BPM 与音频时长产生了不可表示的小节数")
        bar_count = math.ceil(bar_extent)
        self.targets: list[Target] = []
        self._bar_ranges: dict[int, tuple[int, int]] = {}
        downbeats = []
        for bar_index in range(bar_count):
            bar_time = self.start + bar_index * self.beats_per_bar * self.beat_seconds
            if bar_time >= self.duration_seconds:
                break
            downbeats.append(bar_time)
            first_index = len(self.targets)
            beat_offset = Fraction(0)
            for token in self.patterns[bar_index % len(self.patterns)]:
                note = parse_note(token)
                time = self.start + float(bar_index * self.beats_per_bar + beat_offset) * self.beat_seconds
                if time < self.duration_seconds:
                    self.targets.append(Target(len(self.targets), time, note.denominator,
                                               bar_index + 1, note.dots, beat_offset, note.beats))
                beat_offset += note.beats
            self._bar_ranges[bar_index + 1] = (first_index, len(self.targets))
        self._times = tuple(target.time for target in self.targets)
        self._downbeat_times = tuple(downbeats)

    def targets_for_bar(self, bar: int) -> list[Target]:
        """按从 1 开始的小节号取目标；没有该小节时返回空列表。"""
        _positive_integer("bar", bar)
        first, last = self._bar_ranges.get(bar, (0, 0))
        return self.targets[first:last]

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
        """返回 (从 1 开始的小节号, 从 1 开始的四分音符拍位置)。"""
        time = _finite("time", time)
        if time < self.start or time >= self.duration_seconds:
            return None
        beat_position = (time - self.start) / self.beat_seconds
        nearest = round(beat_position)
        if math.isclose(beat_position, nearest, rel_tol=0, abs_tol=_EPSILON):
            beat_position = float(nearest)
        bar_index = math.floor(beat_position / self.beats_per_bar)
        return bar_index + 1, beat_position - bar_index * self.beats_per_bar + 1

    def visible_targets(self, time: float, lookahead: float, lookbehind: float = 0.3) -> list[Target]:
        """用二分查找返回闭区间 [time-lookbehind, time+lookahead] 的目标。"""
        time = _finite("time", time)
        lookahead = _finite("lookahead", lookahead)
        lookbehind = _finite("lookbehind", lookbehind)
        if lookahead < 0 or lookbehind < 0:
            raise ValueError("可见时间范围必须非负")
        return self.targets[bisect_left(self._times, time - lookbehind):
                            bisect_right(self._times, time + lookahead)]


class RhythmSession:
    """拍掌归属小节结束后，练习完整一小节，再正式游玩 game_bars 小节。

    最后一个四分拍的拍掌归属下一小节，其余拍掌归属当前小节。准备拍只供
    练习，不影响正式成绩。arm 是跳过等待与准备期的显式测试接口。
    正 input_offset_ms 从输入时间扣除；advance 保留正偏移的输入处理余量。
    """

    def __init__(self, chart: RhythmChart, window_ms: float, input_offset_ms: float = 0,
                 lead_in_bars: int = 0, game_bars: int = 1, max_mistakes: int = 0) -> None:
        self.chart = chart
        self.window_ms = _finite("window_ms", window_ms)
        self.input_offset_ms = _finite("input_offset_ms", input_offset_ms)
        if self.window_ms < 0:
            raise ValueError("window_ms 必须非负")
        # 旧配置仍可读取；实际启动始终使用一个完整准备小节。
        self.lead_in_bars = _nonnegative_integer("lead_in_bars", lead_in_bars)
        self.game_bars = _positive_integer("game_bars", game_bars)
        self.max_mistakes = _nonnegative_integer("max_mistakes", max_mistakes)
        self._window = self.window_ms / 1000
        self._offset = self.input_offset_ms / 1000
        self.reset()

    def reset(self) -> None:
        """清空本轮、失败记录与输入时间戳顺序检查。"""
        self.entry_time: float | None = None
        self.trigger_time: float | None = None
        self.preparation_start_time: float | None = None
        self.round_end_time: float | None = None
        self.attributed_bar: int | None = None
        self.states: dict[int, str] = {}
        self.preparation_states: dict[int, str] = {}
        self.preparation_targets: tuple[Target, ...] = ()
        self._preparation_times: tuple[float, ...] = ()
        self._entry_index = self._end_index = len(self.chart.targets)
        self._miss_cursor = self._entry_index
        self._prep_cursor = 0
        self._armed_at: float | None = None
        self._last_input: float | None = None
        self._advanced_time: float | None = None
        self._hits = self._misses = self._mistakes = 0
        self.failed = False
        self.failure_reason: str | None = None
        self.failure_time: float | None = None
        self.start_rejection: str | None = None
        self._completed = False

    def _enter(self, entry_bar: int, armed_at: float, *, trigger_time: float | None = None,
               attributed_bar: int | None = None, preparation_start: float | None = None) -> None:
        self.reset()
        self._entry_index = self.chart._bar_ranges[entry_bar][0]
        self.entry_time = self.chart._downbeat_times[entry_bar - 1]
        self.round_end_time = min(self.entry_time + self.game_bars * self.chart.bar_seconds,
                                  self.chart.duration_seconds)
        self._end_index = bisect_left(self.chart._times, self.round_end_time - _EPSILON)
        self.trigger_time = self.entry_time if trigger_time is None else trigger_time
        self.attributed_bar = attributed_bar
        self.preparation_start_time = preparation_start
        self._armed_at = armed_at
        self._miss_cursor = self._entry_index
        if preparation_start is not None:
            self.preparation_targets = tuple(
                Target(beat, preparation_start + beat * self.chart.beat_seconds, 4,
                       entry_bar - 1, 0, Fraction(beat), Fraction(1))
                for beat in range(self.chart.beats_per_bar)
            )
            self._preparation_times = tuple(t.time for t in self.preparation_targets)

    def try_start(self, music_time: float) -> bool:
        """按校正输入归属小节启动；正式的完整 game_bars 小节必须处于音频内。"""
        music_time = _finite("music_time", music_time)
        if self._last_input is not None and music_time < self._last_input:
            raise ValueError("输入音乐时间戳必须按非递减顺序处理")
        if self.entry_time is not None and not self.finished:
            self.start_rejection = "active"
            return False
        corrected = music_time - self._offset
        if corrected < self.chart.start - self._window - _EPSILON:
            self.start_rejection = "before_chart"
            return False
        if corrected >= self.chart.duration_seconds:
            self.start_rejection = "insufficient_music"
            return False
        if corrected < self.chart.start:
            attributed_bar = 1
        else:
            bar, beat = self.chart.bar_at(corrected)
            attributed_bar = bar + (beat >= self.chart.beats_per_bar - _EPSILON)
        preparation_start = self.chart.start + attributed_bar * self.chart.bar_seconds
        entry = preparation_start + self.chart.bar_seconds
        end = entry + self.game_bars * self.chart.bar_seconds
        if end > self.chart.duration_seconds + _EPSILON:
            self.start_rejection = "insufficient_music"
            return False
        self._enter(attributed_bar + 2, music_time, trigger_time=corrected,
                    attributed_bar=attributed_bar, preparation_start=preparation_start)
        return True

    def arm(self, time: float) -> float | None:
        """测试辅助：直接选择 >= time 的正式小节；允许 EOF 截短。"""
        time = _finite("time", time)
        self.reset()
        entry_time = self.chart.next_bar(time)
        if entry_time is not None:
            entry_bar = round((entry_time - self.chart.start) / self.chart.bar_seconds) + 1
            self._enter(entry_bar, time)
        return self.entry_time

    @property
    def formal_targets(self) -> tuple[Target, ...]:
        """本轮正式目标的准确切片，避免用浮点结束时刻重新筛选。"""
        return tuple(self.chart.targets[self._entry_index:self._end_index])

    @property
    def first_formal_bar(self) -> int | None:
        return (self.chart.targets[self._entry_index].bar
                if self._entry_index < self._end_index else None)

    @property
    def last_formal_bar(self) -> int | None:
        return (self.chart.targets[self._end_index - 1].bar
                if self._entry_index < self._end_index else None)

    @property
    def hits(self) -> int:
        return self._hits

    @property
    def misses(self) -> int:
        return self._misses

    @property
    def mistakes(self) -> int:
        return self._mistakes

    @property
    def prep_hits(self) -> int:
        return sum(state == "hit" for state in self.preparation_states.values())

    @property
    def prep_misses(self) -> int:
        return sum(state == "miss" for state in self.preparation_states.values())

    @property
    def finished(self) -> bool:
        return self.failed or self._completed

    def phase_at(self, music_time: float) -> str:
        """用于画面的音乐阶段；失败与完成后保持终态，提前命中不提前切阶段。"""
        music_time = _finite("music_time", music_time)
        if self.failed:
            return "failed"
        if self._completed:
            return "completed"
        if self.entry_time is None:
            return "waiting"
        if music_time >= self.entry_time - _EPSILON:
            return "playing"
        if self.preparation_start_time is not None and music_time >= self.preparation_start_time - _EPSILON:
            return "preparing"
        return "lead_in"

    @staticmethod
    def _nearest(targets: Sequence[Target], times: Sequence[float], time: float,
                 first: int = 0, last: int | None = None) -> Target:
        last = len(targets) if last is None else last
        insertion = bisect_left(times, time, first, last)
        if insertion == first:
            return targets[first]
        if insertion == last:
            return targets[last - 1]
        earlier, later = targets[insertion - 1], targets[insertion]
        return later if later.time - time < time - earlier.time - _EPSILON else earlier

    def _mistake(self, reason: str, music_time: float) -> None:
        self._mistakes += 1
        if self.max_mistakes and self._mistakes >= self.max_mistakes:
            self.failed = True
            self.failure_reason = reason
            self.failure_time = music_time

    def judge(self, music_time: float) -> Judgment:
        """匹配最近起音，等距选较早目标；触发输入与准备练习不计正式成绩。"""
        music_time = _finite("music_time", music_time)
        if self.entry_time is None or music_time < self._armed_at:
            return Judgment("waiting", None, None)
        if self._last_input is not None and music_time < self._last_input:
            raise ValueError("输入音乐时间戳必须按非递减顺序处理")
        self._last_input = music_time
        if self.finished:
            return Judgment("failed" if self.failed else "completed", None, None)
        corrected = music_time - self._offset
        if self.preparation_start_time is not None and music_time == self._armed_at:
            return Judgment("waiting", None, None)
        if corrected < self.entry_time - self._window - _EPSILON:
            if self.preparation_start_time is None or corrected < self.preparation_start_time - self._window - _EPSILON:
                return Judgment("waiting", None, None)
            target = self._nearest(self.preparation_targets, self._preparation_times, corrected)
            error_ms = (corrected - target.time) * 1000
            state = self.preparation_states.get(target.index)
            if abs(corrected - target.time) > self._window + _EPSILON or state == "miss":
                return Judgment("preparing", target, error_ms)
            if state == "hit":
                return Judgment("preparation_duplicate", target, error_ms)
            self.preparation_states[target.index] = "hit"
            return Judgment("preparation_hit", target, error_ms)
        # 输入本身也是结算水位：先处理该输入之前已经确定错过的目标，
        # 避免一次迟到的画面更新让失败之后的音符仍然计分。
        self._settle_formal(corrected - self._window, self._offset)
        if self.failed:
            return Judgment("failed", None, None)
        target = self._nearest(self.chart.targets, self.chart._times, corrected,
                               self._entry_index, self._end_index)
        error = corrected - target.time
        state = self.states.get(target.index)
        if state == "miss":
            return Judgment("late", target, error * 1000)
        if abs(error) > self._window + _EPSILON:
            status = "early" if error < 0 else "late"
            # 迟到输入所对应的漏拍只结算一次；后续补拍不会再次计错。
            if status == "late" and state is None:
                self.states[target.index] = "miss"
                self._misses += 1
                self._mistake("miss", music_time)
            else:
                self._mistake(status, music_time)
            return Judgment(status, target, error * 1000)
        if state == "hit":
            self._mistake("duplicate", music_time)
            return Judgment("duplicate", target, error * 1000)
        self.states[target.index] = "hit"
        self._hits += 1
        return Judgment("hit", target, error * 1000)

    def _settle_formal(self, before: float, failure_offset: float) -> None:
        while self._miss_cursor < self._end_index:
            target = self.chart.targets[self._miss_cursor]
            if before <= target.time + _EPSILON:
                break
            self._miss_cursor += 1
            if target.index not in self.states:
                self.states[target.index] = "miss"
                self._misses += 1
                self._mistake("miss", target.time + self._window + failure_offset)
                if self.failed:
                    return

    def advance(self, music_time: float) -> None:
        """按时间顺序结算漏拍；达到失败次数即冻结，不结算本轮后续目标。"""
        music_time = _finite("music_time", music_time)
        if self.entry_time is None or self.finished:
            return
        self._advanced_time = max(music_time, self._advanced_time if self._advanced_time is not None else music_time)
        allowance = self._window + max(self._offset, 0)
        while self._prep_cursor < len(self.preparation_targets):
            target = self.preparation_targets[self._prep_cursor]
            if music_time <= target.time + allowance + _EPSILON:
                break
            self.preparation_states.setdefault(target.index, "miss")
            self._prep_cursor += 1
        self._settle_formal(music_time - allowance, max(self._offset, 0))
        if self.failed:
            return
        if (music_time >= self.round_end_time + max(self._offset, 0) - _EPSILON
                and len(self.states) == self._end_index - self._entry_index):
            self._completed = True
