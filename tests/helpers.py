from pathlib import Path


CONFIG_TEXT = '''[music]
path = "music.wav"
volume = 0.5
[rhythm]
bpm = 90.0
beat_start_seconds = 4.0
beat_offset_ms = 0.0
beats_per_bar = 4
lead_in_bars = 0
note_values = [4, 8, 8, 4, 4]
[judgment]
window_ms = 100.0
input_offset_ms = 0.0
[audio]
device = ""
blocksize = 128
clap_path = ""
clap_volume = 0.4
[display]
width = 1280
height = 800
lookahead_seconds = 3.0
'''


def write_config(directory, text=CONFIG_TEXT):
    directory = Path(directory)
    path = directory / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path
