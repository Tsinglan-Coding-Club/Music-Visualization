"""固定音高的滚动节奏五线谱；布局为纯函数，Arcade 只负责绘制布局结果。"""

from dataclasses import dataclass, replace
from fractions import Fraction
import math
from typing import Iterable

from .notation import RhythmNote, normalize_pattern, parse_note


STAFF_LEFT = 210.0
STAFF_RIGHT = 1150.0
PLAYHEAD_X = 430.0
NOTE_Y = 257.0
STEM_TOP = 211.0


@dataclass(frozen=True)
class Segment:
    x1: float
    y1: float
    x2: float
    y2: float


@dataclass(frozen=True)
class Beam:
    segment: Segment
    level: int
    note_indices: tuple[int, ...]
    hook: bool = False


@dataclass(frozen=True)
class NotePlan:
    index: int
    bar: int
    denominator: int
    dots: int
    beat_offset: Fraction
    duration_beats: Fraction
    x: float
    y: float
    filled: bool
    stem: Segment | None
    flags: tuple[tuple[tuple[float, float], ...], ...]
    dot_positions: tuple[tuple[float, float], ...]
    preparation: bool = False

    @property
    def beam_count(self) -> int:
        return max(0, self.denominator.bit_length() - 3)


@dataclass(frozen=True)
class RhythmLayout:
    notes: tuple[NotePlan, ...]
    beams: tuple[Beam, ...]


@dataclass(frozen=True)
class BarPlan:
    number: int
    x: float
    end_x: float
    formal: bool
    preparation: bool = False


@dataclass(frozen=True)
class StaffLayout:
    notes: tuple[NotePlan, ...]
    beams: tuple[Beam, ...]
    bars: tuple[BarPlan, ...]
    pixels_per_beat: float
    left: float = STAFF_LEFT
    right: float = STAFF_RIGHT
    playhead_x: float = PLAYHEAD_X


def _flag(stem_x: float, level: int) -> tuple[tuple[float, float], ...]:
    y = STEM_TOP + (level - 1) * 7
    return ((stem_x, y), (stem_x + 14, y + 7), (stem_x + 17, y + 20),
            (stem_x + 10, y + 14), (stem_x, y + 6))


def _layout_notes(inputs: Iterable[tuple[int, int, RhythmNote, Fraction, float, bool]]) -> RhythmLayout:
    """输入为 (目标号, 小节号, RhythmNote, 小节内拍位置, x, 是否预备)。"""
    notes = []
    groups: list[list[int]] = []
    current: list[int] = []
    previous_end = None
    previous_beat = None
    previous_bar = None
    for index, bar, note, offset, x, preparation in inputs:
        plan = NotePlan(
            index, bar, note.denominator, note.dots, offset, note.beats, x, NOTE_Y,
            note.denominator >= 4,
            Segment(x + 7, NOTE_Y, x + 7, STEM_TOP) if note.denominator >= 2 else None,
            (), tuple((x + 14 + dot * 5, NOTE_Y - 6) for dot in range(note.dots)), preparation,
        )
        # 跨越四分拍边界的短音符单独标符尾，避免跨拍连杠。
        beat = offset.numerator // offset.denominator
        beamable = note.denominator >= 8 and offset + note.beats <= beat + 1
        contiguous = (bar == previous_bar and beat == previous_beat and offset == previous_end)
        if current and (not beamable or not contiguous):
            groups.append(current)
            current = []
        notes.append(plan)
        if beamable:
            current.append(len(notes) - 1)
        else:
            groups.append([len(notes) - 1])
        previous_end, previous_beat, previous_bar = offset + note.beats, beat, bar
    if current:
        groups.append(current)

    beams = []
    beamed = set()
    for group in groups:
        if len(group) < 2:
            continue
        beamed.update(group)
        maximum = max(notes[i].beam_count for i in group)
        for level in range(1, maximum + 1):
            run = []
            runs = []
            for i in group:
                if notes[i].beam_count >= level:
                    run.append(i)
                elif run:
                    runs.append(run)
                    run = []
            if run:
                runs.append(run)
            for run in runs:
                y = STEM_TOP + 2 + (level - 1) * 7
                if len(run) >= 2:
                    beams.append(Beam(Segment(notes[run[0]].x + 7, y,
                                              notes[run[-1]].x + 7, y),
                                      level, tuple(notes[i].index for i in run)))
                else:
                    i = run[0]
                    at = group.index(i)
                    # 孤立的次级符杠朝同一拍中的相邻音符，末音符朝左。
                    neighbor = group[at + 1] if at + 1 < len(group) else group[at - 1]
                    direction = 1 if notes[neighbor].x > notes[i].x else -1
                    length = min(11.0, abs(notes[neighbor].x - notes[i].x) * 0.42)
                    x = notes[i].x + 7
                    beams.append(Beam(Segment(x, y, x + direction * length, y),
                                      level, (notes[i].index,), True))
    for i, note in enumerate(notes):
        if note.beam_count and i not in beamed:
            notes[i] = replace(note, flags=tuple(
                _flag(note.x + 7, level) for level in range(1, note.beam_count + 1)
            ))
    return RhythmLayout(tuple(notes), tuple(beams))


def layout_bar(values, beats_per_bar: int, *, start_x: float = 0,
               pixels_per_beat: float = 120, bar: int = 1,
               target_indices: Iterable[int] | None = None) -> RhythmLayout:
    """独立小节布局，可用于谱面预览与符杠／附点测试。"""
    pattern = normalize_pattern(values, beats_per_bar)
    if not math.isfinite(pixels_per_beat) or pixels_per_beat <= 0:
        raise ValueError("每拍像素宽度须为有限正数")
    indices = tuple(range(len(pattern))) if target_indices is None else tuple(target_indices)
    if len(indices) != len(pattern):
        raise ValueError("目标号数量须与音符数量一致")
    inputs = []
    offset = Fraction()
    for index, value in zip(indices, pattern):
        note = parse_note(value)
        inputs.append((index, bar, note, offset, start_x + float(offset) * pixels_per_beat, False))
        offset += note.beats
    return _layout_notes(inputs)


def _is_formal_bar(bar: int, chart, session) -> bool:
    if hasattr(session, "first_formal_bar"):
        first, last = session.first_formal_bar, session.last_formal_bar
        return first is not None and last is not None and first <= bar <= last
    entry = getattr(session, "entry_time", None)
    end = getattr(session, "round_end_time", None)
    start = chart.start + (bar - 1) * chart.bar_seconds
    return entry is not None and end is not None and entry <= start < end


def build_staff_layout(chart, position: float, session=None, *, left: float = STAFF_LEFT,
                       right: float = STAFF_RIGHT, playhead_x: float = PLAYHEAD_X,
                       pixels_per_beat: float | None = None,
                       show_notes: bool = True) -> StaffLayout:
    """按音乐时钟布局多小节；show_notes=False 时只保留空谱面所需尺寸。"""
    if not math.isfinite(position):
        raise ValueError("音乐位置须为有限数值")
    patterns = getattr(chart, "patterns", (chart.note_values,))
    if pixels_per_beat is None:
        shortest = min(parse_note(value).beats for pattern in patterns for value in pattern)
        pixels_per_beat = max(120.0, 20.0 / float(shortest))
    if not math.isfinite(pixels_per_beat) or pixels_per_beat <= 0 or right <= left:
        raise ValueError("谱面视口与每拍宽度无效")
    if not show_notes:
        return StaffLayout((), (), (), pixels_per_beat, left, right, playhead_x)
    clock_beat = (position - chart.start) / chart.beat_seconds
    first_beat = clock_beat + (left - playhead_x - 30) / pixels_per_beat
    last_beat = clock_beat + (right - playhead_x + 30) / pixels_per_beat
    first_bar = max(1, math.floor(first_beat / chart.beats_per_bar) + 1)
    last_bar = min(math.ceil((chart.duration_seconds - chart.start) / chart.bar_seconds),
                   math.floor(last_beat / chart.beats_per_bar) + 1)
    inputs = []
    bars = []
    preparation_targets = getattr(session, "preparation_targets", ())
    preparation_by_bar = {}
    for target in preparation_targets:
        preparation_by_bar.setdefault(target.bar, []).append(target)
    for bar in range(first_bar, last_bar + 1):
        beat = (bar - 1) * chart.beats_per_bar
        x = playhead_x + (beat - clock_beat) * pixels_per_beat
        formal = _is_formal_bar(bar, chart, session)
        preparing = bar in preparation_by_bar
        bars.append(BarPlan(bar, x - 18, x - 18 + chart.beats_per_bar * pixels_per_beat, formal, preparing))
        targets = preparation_by_bar[bar] if preparing else chart.targets_for_bar(bar)
        for target in targets:
            note = parse_note(f"{target.denominator}{'.' * target.dots}")
            offset = target.beat_offset
            note_x = playhead_x + ((target.time - position) / chart.beat_seconds) * pixels_per_beat
            inputs.append((target.index, bar, note, offset, note_x, preparing))
    rhythm = _layout_notes(inputs)
    return StaffLayout(rhythm.notes, rhythm.beams, tuple(bars), pixels_per_beat,
                       left, right, playhead_x)


def clip_segment(segment: Segment, left: float, right: float) -> Segment | None:
    """把线段裁到水平视口；符杠在音符离开视口后也不会越过左侧拍号。"""
    dx = segment.x2 - segment.x1
    if not dx:
        return segment if left <= segment.x1 <= right else None
    t1, t2 = sorted(((left - segment.x1) / dx, (right - segment.x1) / dx))
    low, high = max(0.0, t1), min(1.0, t2)
    if low > high:
        return None
    dy = segment.y2 - segment.y1
    return Segment(segment.x1 + low * dx, segment.y1 + low * dy,
                   segment.x1 + high * dx, segment.y1 + high * dy)


def _clip_polygon(points, left, right):
    result = list(points)
    for boundary, keep_greater in ((left, True), (right, False)):
        clipped = []
        if not result:
            break
        previous = result[-1]
        previous_inside = previous[0] >= boundary if keep_greater else previous[0] <= boundary
        for current in result:
            inside = current[0] >= boundary if keep_greater else current[0] <= boundary
            if inside != previous_inside:
                t = (boundary - previous[0]) / (current[0] - previous[0])
                clipped.append((boundary, previous[1] + t * (current[1] - previous[1])))
            if inside:
                clipped.append(current)
            previous, previous_inside = current, inside
        result = clipped
    return result


def _beat_label(offset: Fraction) -> str:
    whole = offset.numerator // offset.denominator
    fraction = offset - whole
    if not fraction:
        return str(whole + 1)
    return {Fraction(1, 4): "e", Fraction(1, 2): "&", Fraction(3, 4): "a"}.get(fraction, "·")


def note_status(note: NotePlan, position: float, chart, session) -> str | None:
    """准备拍使用独立目标号与成绩，不与正式谱面的目标号混用。"""
    states = getattr(session, "preparation_states", {}) if note.preparation else session.states
    state = states.get(note.index)
    if state is not None:
        return state
    if getattr(session, "failed", False):
        return None
    time = chart.start + ((note.bar - 1) * chart.beats_per_bar
                          + float(note.beat_offset)) * chart.beat_seconds
    start = getattr(session, "preparation_start_time", None) if note.preparation else session.entry_time
    end = session.entry_time if note.preparation else session.round_end_time
    in_phase = (start is not None and end is not None) if note.preparation else _is_formal_bar(note.bar, chart, session)
    if in_phase and abs(position - time) <= getattr(session, "window_ms", 0) / 1000:
        return "active"
    return None


def draw_staff(window, position, chart, session):
    """在主窗口绘制白底 demo 五线谱；所有移动位置来自实际音乐时间。"""
    import arcade
    from .app import TEXT, MUTED, GREEN, PURPLE, RED

    show_notes = session.entry_time is not None and not session.finished
    layout = build_staff_layout(chart, position, session, show_notes=show_notes)
    left, right = layout.left, layout.right
    for y in (233, 245, 257, 269, 281):
        window._line(left, y, right, y, (175, 175, 175), 1)
    window._text("signature-top", chart.beats_per_bar, 172, 231, 24, TEXT, anchor_x="center")
    window._text("signature-bottom", "4", 172, 259, 24, TEXT, anchor_x="center")
    if not show_notes:
        return

    def line(segment, color, width=1):
        clipped = clip_segment(segment, left + width / 2, right - width / 2)
        if clipped is not None:
            window._line(clipped.x1, clipped.y1, clipped.x2, clipped.y2, color, width)

    for bar in layout.bars:
        if bar.formal:
            line(Segment(bar.x, 197, bar.end_x, 197), PURPLE, 2)
        line(Segment(bar.x, 226, bar.x, 284), MUTED, 1)
        if left + 18 <= bar.x <= right - 50:
            window._text(("staff-bar", bar.number % 32), f"{bar.number}" + (" 预备" if bar.preparation else ""),
                         bar.x + 3, 177, 13, PURPLE if bar.formal or bar.preparation else MUTED)
    if layout.bars:
        line(Segment(layout.bars[-1].end_x, 226, layout.bars[-1].end_x, 284), MUTED, 1)
    window._line(layout.playhead_x, 205, layout.playhead_x, 286, (190, 180, 210), 1)

    colors = {}
    for note in layout.notes:
        status = note_status(note, position, chart, session)
        colors[(note.preparation, note.index)] = {
            "hit": GREEN, "miss": RED, "active": PURPLE,
        }.get(status, TEXT)
    for beam in layout.beams:
        beam_colors = {colors[(False, i)] for i in beam.note_indices}
        # 共同符杠保持中性；每个符头／符干仍独立显示命中或漏拍。
        color = next(iter(beam_colors)) if len(beam_colors) == 1 else TEXT
        line(beam.segment, color, 4)
    for note in layout.notes:
        color = colors[(note.preparation, note.index)]
        if note.stem is not None:
            line(note.stem, color, 1.7)
        for flag in note.flags:
            clipped = _clip_polygon(flag, left, right)
            if len(clipped) >= 3:
                arcade.draw_polygon_filled([window._point(*point) for point in clipped], color)
        width = 19 if note.denominator == 1 else 16
        # 头部完整进入视口再绘制，防止半个符头覆盖拍号。
        if left + width / 2 <= note.x <= right - width / 2:
            if note.filled:
                arcade.draw_ellipse_filled(*window._point(note.x, note.y), width * window._scale,
                                           11 * window._scale, color, tilt_angle=18, num_segments=32)
            else:
                arcade.draw_ellipse_outline(*window._point(note.x, note.y), width * window._scale,
                                            11 * window._scale, color, 2 * window._scale,
                                            tilt_angle=18, num_segments=32)
        for x, y in note.dot_positions:
            if left + 2 <= x <= right - 2:
                window._circle(x, y, 1.8, color)
        if left + 12 <= note.x <= right - 12:
            window._text(("staff-beat", note.preparation, note.index % 512), _beat_label(note.beat_offset),
                         note.x, 294, 13, MUTED, anchor_x="center")
