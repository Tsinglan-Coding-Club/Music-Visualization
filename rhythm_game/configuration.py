"""独立 TOML 配置；所有素材路径相对于配置文件。"""

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import tempfile
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
    glove_enabled: bool = False
    glove_port: str = ""
    glove_baudrate: int = 115200
    glove_debounce_ms: float = 30.0


def load_config(path: str | Path) -> Config:
    source = Path(path).resolve()
    return parse_config_text(source.read_text(encoding="utf-8"), source)


def parse_config_text(text: str, source: str | Path) -> Config:
    source = Path(source).resolve()
    data = tomllib.loads(text)
    allowed = {
        "music": {"path", "volume"},
        "rhythm": {"bpm", "beat_start_seconds", "beat_offset_ms", "beats_per_bar", "lead_in_bars", "note_values"},
        "judgment": {"window_ms", "input_offset_ms"},
        "audio": {"device", "blocksize", "clap_path", "clap_volume"},
        "display": {"width", "height", "lookahead_seconds"},
        "glove": {"enabled", "port", "baudrate", "debounce_ms"},
    }
    for section, values in data.items():
        if section not in allowed or not isinstance(values, dict):
            raise ValueError(f"未知配置区段：{section}")
        unknown = values.keys() - allowed[section]
        if unknown:
            raise ValueError(f"{section} 中有未知配置：{', '.join(sorted(unknown))}")
    for section in allowed:
        if section not in data and section != "glove":
            raise ValueError(f"配置缺少 [{section}]")
    data["glove"] = {"enabled": False, "port": "", "baudrate": 115200, "debounce_ms": 30.0,
                     **data.get("glove", {})}
    if not isinstance(data["glove"]["enabled"], bool):
        raise ValueError("glove.enabled 必须是布尔值")

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
        data["glove"]["enabled"], text("glove", "port").strip(),
        number("glove", "baudrate", 1, 3000000, integer=True),
        number("glove", "debounce_ms", 0, 1000),
    )


EDITABLE_FIELDS = {
    "music_path": ("music", "path"),
    "bpm": ("rhythm", "bpm"),
    "beat_start_seconds": ("rhythm", "beat_start_seconds"),
    "beats_per_bar": ("rhythm", "beats_per_bar"),
    "note_values": ("rhythm", "note_values"),
    "lead_in_bars": ("rhythm", "lead_in_bars"),
    "beat_offset_ms": ("rhythm", "beat_offset_ms"),
    "input_offset_ms": ("judgment", "input_offset_ms"),
    "window_ms": ("judgment", "window_ms"),
    "blocksize": ("audio", "blocksize"),
    "music_volume": ("music", "volume"),
    "clap_volume": ("audio", "clap_volume"),
}


def _toml_value_end(text, start):
    """找到值末端，区分字符串内的 #、数组内注释与真正的行内注释。"""
    position = start
    depth = 0
    quote = None
    comments = []
    while position < len(text):
        char = text[position]
        if quote is not None:
            if quote[0] == '"' and char == "\\":
                position += 2
                continue
            if text.startswith(quote, position):
                position += len(quote)
                quote = None
                continue
        elif char in ("'", '"'):
            quote = char * (3 if text.startswith(char * 3, position) else 1)
            position += len(quote)
            continue
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        elif char == "#":
            if depth == 0:
                break
            end = text.find("\n", position)
            end = len(text) if end == -1 else end
            comments.append(text[position:end].rstrip("\r"))
            position = end
            continue
        elif char in "\r\n" and depth == 0:
            break
        position += 1
    while position > start and text[position - 1].isspace():
        position -= 1
    return position, comments


def prepare_config_update(path, updates, expected_text):
    """批量校验设置并生成 TOML；保留其他字段与注释，不立即写入。"""
    source = Path(path).resolve()
    original = source.read_text(encoding="utf-8")
    if original != expected_text:
        raise ValueError("配置已被外部修改，请返回游戏后重新打开设置")
    if not updates or updates.keys() - EDITABLE_FIELDS.keys():
        raise ValueError("设置包含未知或空字段")
    replacements = {}
    for name, value in updates.items():
        if name == "music_path":
            if not isinstance(value, (str, Path)) or not str(value).strip():
                raise ValueError("music_path 必须是非空音乐路径")
            music_path = (source.parent / value).resolve()
            try:
                stored_path = music_path.relative_to(source.parent)
            except ValueError:
                stored_path = music_path
            serialized = json.dumps(str(stored_path), ensure_ascii=False)
        elif name == "note_values":
            if not isinstance(value, (list, tuple)) or not value:
                raise ValueError("note_values 必须是非空整数数组")
            if any(isinstance(n, bool) or not isinstance(n, int)
                   or n not in (1, 2, 4, 8, 16, 32, 64) for n in value):
                raise ValueError("note_values 只接受 1、2、4、8、16、32、64 的常规音符分母")
            serialized = json.dumps(list(value))
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} 必须是有限数字")
            if name in ("blocksize", "beats_per_bar", "lead_in_bars"):
                if int(value) != value:
                    raise ValueError(f"{name} 必须为整数")
                value = int(value)
            serialized = str(value)
        replacements[EDITABLE_FIELDS[name]] = serialized

    section = None
    position = 0
    copied_until = 0
    parts = []
    while position < len(original):
        end = original.find("\n", position)
        end = len(original) if end == -1 else end + 1
        line = original[position:end]
        header = re.match(r"\s*\[([^\]]+)\]", line)
        if header:
            section = header[1]
        assignment = re.match(r"([ \t]*)(\w+)([ \t]*=[ \t]*)", line)
        if assignment:
            value_start = position + assignment.end()
            value_end, comments = _toml_value_end(original, value_start)
            key = (section, assignment[2])
            if key in replacements:
                parts.append(original[copied_until:position])
                # 多行数组转为单行时，将其内部注释放在字段前，避免丢失说明。
                parts.extend(assignment[1] + comment + "\n" for comment in comments)
                parts.append(original[position:value_start])
                parts.append(replacements.pop(key))
                copied_until = value_end
            end = original.find("\n", value_end)
            end = len(original) if end == -1 else end + 1
        position = end
    if replacements:
        raise ValueError("配置中缺少待修改字段")
    parts.append(original[copied_until:])
    updated = "".join(parts)
    return updated, parse_config_text(updated, source)


def save_config_text(path, text, expected_text):
    """校验并原子替换，失败时不留下部分写入的配置。"""
    path = Path(path).resolve()
    parse_config_text(text, path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, path.stat().st_mode & 0o777)
        if path.read_text(encoding="utf-8") != expected_text:
            raise ValueError("配置已被外部修改，请重新打开设置")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
