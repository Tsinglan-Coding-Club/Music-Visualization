"""按音乐时钟计算预备倒计时与灯状态；不参与成绩判定。"""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class PreparationIndicator:
    remaining: int
    current: int
    lights: tuple[str, ...]


def preparation_indicator(session, music_time):
    """当前灯按拍钟亮起，命中另用绿色表示；不需要先拍中才开始计数。"""
    if session.phase_at(music_time) != "preparing":
        return None
    count = len(session.preparation_targets)
    elapsed = (music_time - session.preparation_start_time) / session.chart.beat_seconds
    current = min(count - 1, max(0, math.floor(elapsed + 1e-9)))
    lights = []
    for index in range(count):
        if index == current:
            lights.append("current_hit" if session.preparation_states.get(index) == "hit" else "current")
        elif index < current:
            lights.append("hit" if session.preparation_states.get(index) == "hit" else "past")
        else:
            lights.append("pending")
    return PreparationIndicator(count - current, current, tuple(lights))
