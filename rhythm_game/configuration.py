"""独立 TOML 配置；所有素材路径相对于配置文件。"""

from dataclasses import dataclass
import math
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class Config:
    source: Path
    music_path: Path
    music_volume: float
    bpm: float
    beat_start_seconds: float
    beat_offset_ms: float
    beats_per_bar: int
    lead_in_bars: int
    note_values: tuple[int, ...]
    window_ms: float
    input_offset_ms: float
    device: str | None
    blocksize: int
    clap_path: Path | None
    clap_volume: float
    width: int
    height: int
    lookahead_seconds: float


def load_config(path: str | Path) -> Config:
    source = Path(path).resolve()
    with source.open("rb") as stream:
        data = tomllib.load(stream)
    allowed = {
        "music": {"path", "volume"},
        "rhythm": {"bpm", "beat_start_seconds", "beat_offset_ms", "beats_per_bar", "lead_in_bars", "note_values"},
        "judgment": {"window_ms", "input_offset_ms"},
        "audio": {"device", "blocksize", "clap_path", "clap_volume"},
        "display": {"width", "height", "lookahead_seconds"},
    }
    for section, values in data.items():
        if section not in allowed or not isinstance(values, dict):
            raise ValueError(f"未知配置区段：{section}")
        unknown = values.keys() - allowed[section]
        if unknown:
            raise ValueError(f"{section} 中有未知配置：{', '.join(sorted(unknown))}")
    for section in allowed:
        if section not in data:
            raise ValueError(f"配置缺少 [{section}]")

    def value(section, key):
        try:
            return data[section][key]
        except KeyError as exc:
            raise ValueError(f"配置缺少 {section}.{key}") from exc

    def number(section, key, minimum=None, maximum=None, integer=False):
        result = value(section, key)
        if isinstance(result, bool) or not isinstance(result, (int, float)) or not math.isfinite(result):
            raise ValueError(f"{section}.{key} 必须是有限数字")
        if integer and not isinstance(result, int):
            raise ValueError(f"{section}.{key} 必须是整数")
        if minimum is not None and result < minimum or maximum is not None and result > maximum:
            raise ValueError(f"{section}.{key} 超出范围 [{minimum}, {maximum}]")
        return result

    def text(section, key):
        result = value(section, key)
        if not isinstance(result, str):
            raise ValueError(f"{section}.{key} 必须是字符串")
        return result

    music = text("music", "path")
    if not music.strip():
        raise ValueError("music.path 不能为空")
    music_path = source.parent / music
    if not music_path.is_file():
        raise ValueError(f"找不到音乐文件：{music_path}")
    clap = text("audio", "clap_path")
    clap_path = source.parent / clap if clap else None
    if clap_path is not None and not clap_path.is_file():
        raise ValueError(f"找不到拍手音效：{clap_path}")
    note_values = value("rhythm", "note_values")
    if not isinstance(note_values, list) or not note_values:
        raise ValueError("rhythm.note_values 必须是非空整数数组")
    if any(isinstance(n, bool) or not isinstance(n, int) or n not in (1, 2, 4, 8, 16, 32, 64) for n in note_values):
        raise ValueError("rhythm.note_values 只接受 1、2、4、8、16、32、64 的常规音符分母")
    bpm = number("rhythm", "bpm", 1, 600)
    beats_per_bar = number("rhythm", "beats_per_bar", 1, 32, integer=True)
    if not math.isclose(sum(4 / n for n in note_values), beats_per_bar, abs_tol=1e-9):
        raise ValueError("note_values 时值之和必须恰好填满一个小节（4 / 分母 = 拍数）")
    beat_start = number("rhythm", "beat_start_seconds", 0)
    beat_offset = number("rhythm", "beat_offset_ms")
    if beat_start + beat_offset / 1000 < 0:
        raise ValueError("拍点起始时间加整曲偏移后不能小于 0")
    window = number("judgment", "window_ms", 1, 1000)
    if window * 2 / 1000 > min(4 / n for n in note_values) * 60 / bpm:
        raise ValueError("判定窗口过宽，会覆盖相邻目标的中点；请缩小 window_ms")
    return Config(
        source, music_path, number("music", "volume", 0, 1), bpm,
        beat_start, beat_offset, beats_per_bar,
        number("rhythm", "lead_in_bars", 0, 1, integer=True), tuple(note_values), window,
        number("judgment", "input_offset_ms", -1000, 1000),
        text("audio", "device").strip() or None,
        number("audio", "blocksize", 0, 8192, integer=True), clap_path,
        number("audio", "clap_volume", 0, 1),
        number("display", "width", 1000, 3840, integer=True),
        number("display", "height", 700, 2160, integer=True),
        number("display", "lookahead_seconds", 0.5, 10),
    )
