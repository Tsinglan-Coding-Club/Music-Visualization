"""轻量枚举本地曲目；候选扩展名不保证文件实际可解码。"""

import os
from pathlib import Path

import soundfile as sf


# 只列出常见曲目格式，并按当前 libsndfile 的格式能力筛选。
# M4A 不在当前解码器支持的格式中；实际解码仍由 AudioEngine 完成。
_FORMAT_EXTENSIONS = {
    "WAV": {".wav"},
    "MP3": {".mp3"},
    "OGG": {".ogg"},
    "FLAC": {".flac"},
    "AIFF": {".aif", ".aiff"},
}


def discover_music(config_path: str | Path, current_path: str | Path | None = None) -> tuple[Path, ...]:
    """递归查找配置旁 music/ 的候选文件，也保留现有的当前曲目。

    不解码或读取音频内容。忽略无法访问的目录和文件；相对当前路径以
    配置文件所在目录为基准。返回已解析、去重并稳定排序的绝对路径。
    """
    base = Path(config_path).resolve().parent
    formats = sf.available_formats()
    extensions = set().union(*(suffixes for name, suffixes in _FORMAT_EXTENSIONS.items()
                               if name in formats))
    candidates = set()

    def add_file(path):
        try:
            if path.is_file():
                candidates.add(path.resolve())
        except (OSError, RuntimeError, ValueError):
            # 文件可能在枚举期间被移走，或符号链接已经失效。
            pass

    for directory, _, filenames in os.walk(base / "music"):
        for filename in filenames:
            if Path(filename).suffix.lower() in extensions:
                add_file(Path(directory) / filename)
    if isinstance(current_path, (str, Path)) and str(current_path).strip():
        add_file(base / current_path)
    return tuple(sorted(candidates, key=lambda path: (str(path).casefold(), str(path))))
