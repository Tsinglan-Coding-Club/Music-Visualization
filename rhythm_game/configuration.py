"""独立 TOML 配置；所有素材路径相对于配置文件。"""

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import tempfile
import tomllib

from .notation import normalize_pattern, parse_note


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
    note_values: tuple[int | str, ...]
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
    patterns: tuple[tuple[int | str, ...], ...] = ()
    game_bars: int = 4
    max_mistakes: int = 3


def load_config(path: str | Path) -> Config:
    source = Path(path).resolve()
    return parse_config_text(source.read_text(encoding="utf-8"), source)


def parse_config_text(text: str, source: str | Path) -> Config:
    source = Path(source).resolve()
    data = tomllib.loads(text)
    allowed = {
        "music": {"path", "volume"},
        "rhythm": {"bpm", "beat_start_seconds", "beat_offset_ms", "beats_per_bar", "lead_in_bars", "note_values", "patterns", "game_bars"},
        "judgment": {"window_ms", "input_offset_ms", "max_mistakes"},
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

    data["rhythm"].setdefault("game_bars", 4)
    data["rhythm"].setdefault("lead_in_bars", 1)
    data["judgment"].setdefault("max_mistakes", 3)

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
    bpm = number("rhythm", "bpm", 1, 600)
    beats_per_bar = number("rhythm", "beats_per_bar", 1, 32, integer=True)
    note_values = normalize_pattern(value("rhythm", "note_values"), beats_per_bar)
    raw_patterns = data["rhythm"].get("patterns", [note_values])
    if not isinstance(raw_patterns, (list, tuple)) or not raw_patterns:
        raise ValueError("rhythm.patterns 必须是非空的小节数组")
    patterns = tuple(normalize_pattern(pattern, beats_per_bar) for pattern in raw_patterns)
    if note_values != patterns[0]:
        raise ValueError("rhythm.note_values 必须与 rhythm.patterns 的第一个小节一致")
    beat_start = number("rhythm", "beat_start_seconds", 0)
    beat_offset = number("rhythm", "beat_offset_ms")
    if beat_start + beat_offset / 1000 < 0:
        raise ValueError("拍点起始时间加整曲偏移后不能小于 0")
    window = number("judgment", "window_ms", 1, 1000)
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
        patterns, number("rhythm", "game_bars", 1, 128, integer=True),
        number("judgment", "max_mistakes", 0, 999, integer=True),
    )


EDITABLE_FIELDS = {
    "music_path": ("music", "path"),
    "bpm": ("rhythm", "bpm"),
    "beat_start_seconds": ("rhythm", "beat_start_seconds"),
    "beats_per_bar": ("rhythm", "beats_per_bar"),
    "note_values": ("rhythm", "note_values"),
    "patterns": ("rhythm", "patterns"),
    "game_bars": ("rhythm", "game_bars"),
    "max_mistakes": ("judgment", "max_mistakes"),
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
        elif name in ("note_values", "patterns"):
            if not isinstance(value, (list, tuple)) or not value:
                raise ValueError(f"{name} 必须是非空数组")
            patterns = [value] if name == "note_values" else value
            normalized = []
            for pattern in patterns:
                if not isinstance(pattern, (list, tuple)) or not pattern:
                    raise ValueError(f"{name} 中每个小节必须是非空音符数组")
                normalized.append(tuple(parse_note(note).token for note in pattern))
            serialized = json.dumps(normalized[0] if name == "note_values" else normalized,
                                    ensure_ascii=False)
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} 必须是有限数字")
            if name in ("blocksize", "beats_per_bar", "lead_in_bars", "game_bars", "max_mistakes"):
                if int(value) != value:
                    raise ValueError(f"{name} 必须为整数")
                value = int(value)
            serialized = str(value)
        replacements[EDITABLE_FIELDS[name]] = serialized

    section = None
    position = 0
    headers = {}
    edits = []
    while position < len(original):
        end = original.find("\n", position)
        end = len(original) if end == -1 else end + 1
        line = original[position:end]
        header = re.match(r"\s*\[([^\]]+)\]", line)
        if header:
            section = header[1]
            headers[section] = end
        assignment = re.match(r"([ \t]*)(\w+)([ \t]*=[ \t]*)", line)
        if assignment:
            value_start = position + assignment.end()
            value_end, comments = _toml_value_end(original, value_start)
            key = (section, assignment[2])
            if key in replacements:
                # 多行数组转为单行时，将其内部注释放在字段前，避免丢失说明。
                prefix = "".join(assignment[1] + comment + "\n" for comment in comments)
                edits.append((position, value_end, prefix + original[position:value_start]
                              + replacements.pop(key)))
            end = original.find("\n", value_end)
            end = len(original) if end == -1 else end + 1
        position = end
    optional = {("rhythm", "patterns"), ("rhythm", "game_bars"), ("judgment", "max_mistakes")}
    if replacements.keys() - optional:
        raise ValueError("配置中缺少待修改字段")
    insertions = {}
    for (section, key), serialized in replacements.items():
        if section not in headers:
            raise ValueError(f"配置缺少 [{section}]")
        insertions.setdefault(headers[section], []).append(f"{key} = {serialized}\n")
    for position, values in insertions.items():
        prefix = "" if original[:position].endswith("\n") else "\n"
        edits.append((position, position, prefix + "".join(values)))
    parts = []
    copied_until = 0
    for start, end, replacement in sorted(edits):
        parts.extend((original[copied_until:start], replacement))
        copied_until = end
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
