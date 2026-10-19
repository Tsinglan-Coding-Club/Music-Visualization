"""节奏时值的统一表示与文本格式；以四分音符为一拍，使用精确分数。"""

from dataclasses import dataclass
from fractions import Fraction
import re
from typing import Iterable


_DENOMINATORS = (1, 2, 4, 8, 16, 32, 64)
_NOTE = re.compile(r"([0-9]+)(\.{0,3})", flags=re.ASCII)


@dataclass(frozen=True)
class RhythmNote:
    denominator: int
    dots: int = 0

    def __post_init__(self) -> None:
        if (isinstance(self.denominator, bool)
                or not isinstance(self.denominator, int)
                or self.denominator not in _DENOMINATORS):
            raise ValueError("音符分母须为 1、2、4、8、16、32 或 64")
        if isinstance(self.dots, bool) or not isinstance(self.dots, int) or not 0 <= self.dots <= 3:
            raise ValueError("附点数须为 0 至 3 的整数")

    @property
    def beats(self) -> Fraction:
        return Fraction(4, self.denominator) * (2 - Fraction(1, 2 ** self.dots))

    @property
    def token(self) -> int | str:
        return self.denominator if not self.dots else f"{self.denominator}{'.' * self.dots}"


def parse_note(value: int | str) -> RhythmNote:
    """读取音符分母与最多三个附点，例如 16、'8.'、'4..'。"""
    if isinstance(value, bool):
        raise ValueError("音符须为整数分母或带附点的文本")
    if isinstance(value, int):
        return RhythmNote(value)
    if not isinstance(value, str):
        raise ValueError("音符须为整数分母或带附点的文本")
    match = _NOTE.fullmatch(value.strip())
    if match is None:
        raise ValueError(f"无效音符：{value!r}；示例：4、8、16、8.")
    return RhythmNote(int(match.group(1)), len(match.group(2)))


def normalize_pattern(values: Iterable[int | str], beats_per_bar: int) -> tuple[int | str, ...]:
    """标准化一个完整小节，并精确检查总时值。"""
    if isinstance(beats_per_bar, bool) or not isinstance(beats_per_bar, int) or beats_per_bar <= 0:
        raise ValueError("每小节四分拍数须为正整数")
    if isinstance(values, (str, bytes)):
        raise ValueError("节奏模板须为音符序列")
    try:
        notes = tuple(parse_note(value) for value in values)
    except TypeError as exc:
        raise ValueError("节奏模板须为音符序列") from exc
    if not notes:
        raise ValueError("节奏模板不能为空")
    if sum((note.beats for note in notes), Fraction()) != beats_per_bar:
        raise ValueError("节奏模板的总时值必须恰好为一个小节")
    return tuple(note.token for note in notes)


def parse_pattern(text: str) -> tuple[tuple[int | str, ...], ...]:
    """解析逗号／空白分隔的音符；竖线分隔小节，拍数由调用方检查。"""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("节奏模板不能为空")
    patterns = []
    for bar in text.split("|"):
        tokens = [token for token in re.split(r"[,，\s]+", bar.strip()) if token]
        if not tokens:
            raise ValueError("竖线之间须有完整的小节")
        patterns.append(tuple(parse_note(token).token for token in tokens))
    return tuple(patterns)


def format_pattern(patterns: Iterable[Iterable[int | str]]) -> str:
    """将多个小节转成可编辑文本，保留附点。"""
    return " | ".join(
        ", ".join(str(parse_note(value).token) for value in pattern)
        for pattern in patterns
    )
