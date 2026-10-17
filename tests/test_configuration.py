from pathlib import Path
import tempfile
import unittest

from rhythm_game.configuration import load_config
from tests.helpers import CONFIG_TEXT, write_config


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "music.wav").write_bytes(b"test")

    def test_paths_resolve_relative_to_config(self):
        cfg = load_config(write_config(self.root))
        self.assertEqual(cfg.music_path, self.root.resolve() / "music.wav")
        self.assertEqual(cfg.bpm, 90)
        self.assertEqual(cfg.beat_start_seconds, 4)
        self.assertEqual(cfg.beat_offset_ms, 0)

    def test_global_and_input_offsets_are_independent(self):
        text = CONFIG_TEXT.replace("beat_offset_ms = 0.0", "beat_offset_ms = -50.0")
        text = text.replace("input_offset_ms = 0.0", "input_offset_ms = 30.0")
        cfg = load_config(write_config(self.root, text))
        self.assertEqual(cfg.beat_offset_ms, -50)
        self.assertEqual(cfg.input_offset_ms, 30)

    def test_invalid_values_rejected_before_opening_audio(self):
        replacements = [
            ('path = "music.wav"', 'path = "missing.mp3"'),
            ("bpm = 90.0", "bpm = 0"),
            ("bpm = 90.0", "bpm = nan"),
            ("bpm = 90.0", "bpm = true"),
            ("beat_offset_ms = 0.0", "beat_offset_ms = -5000"),
            ("beats_per_bar = 4", "beats_per_bar = 4.0"),
            ("lead_in_bars = 0", "lead_in_bars = -1"),
            ("lead_in_bars = 0", "lead_in_bars = 2"),
            ("lead_in_bars = 0", "lead_in_bars = true"),
            ("[4, 8, 8, 4, 4]", "[4, 8, 8]"),
            ("[4, 8, 8, 4, 4]", "[true, 8, 8, 4, 4]"),
            ("window_ms = 100.0", "window_ms = 900"),
            ("blocksize = 128", "blocksize = -1"),
            ("width = 1280", "width = 100"),
            ("volume = 0.5", "volume = 2"),
            ("input_offset_ms = 0.0", "input_offset_ms = inf"),
        ]
        for old, new in replacements:
            with self.subTest(new=new), self.assertRaises(ValueError):
                load_config(write_config(self.root, CONFIG_TEXT.replace(old, new)))

    def test_unknown_key_catches_typo(self):
        with self.assertRaisesRegex(ValueError, "未知配置"):
            load_config(write_config(self.root, CONFIG_TEXT.replace("beat_offset_ms", "beat_ofset_ms")))

    def test_missing_section_has_clear_error(self):
        with self.assertRaisesRegex(ValueError, r"缺少 \[display\]"):
            load_config(write_config(self.root, CONFIG_TEXT.split("[display]")[0]))


if __name__ == "__main__":
    unittest.main()
