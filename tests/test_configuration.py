from pathlib import Path
import tempfile
import unittest

from rhythm_game.configuration import load_config, prepare_config_update, save_config_text
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

    def test_settings_save_preserves_comments_paths_and_other_fields(self):
        original = CONFIG_TEXT.replace("beat_offset_ms = 0.0", "beat_offset_ms = 0.0   # 整曲偏移")
        path = write_config(self.root, original)
        text, config = prepare_config_update(path, {"beat_offset_ms": 75, "music_volume": 0.25}, original)
        self.assertIn("beat_offset_ms = 75   # 整曲偏移", text)
        self.assertEqual(config.music_path, self.root.resolve() / "music.wav")
        self.assertEqual(config.input_offset_ms, 0)
        self.assertEqual(path.read_text(), original)
        save_config_text(path, text, original)
        self.assertEqual(load_config(path).beat_offset_ms, 75)
        self.assertEqual(load_config(path).music_volume, 0.25)
        self.assertFalse(list(self.root.glob(".*.tmp")))

    def test_invalid_or_external_updates_preserve_file(self):
        path = write_config(self.root)
        for updates in ({"window_ms": 1000}, {"blocksize": 1.5}, {"music_volume": 2},
                        {"beat_offset_ms": True}, {"device": 1}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                prepare_config_update(path, updates, CONFIG_TEXT)
            self.assertEqual(path.read_text(), CONFIG_TEXT)
        text, _ = prepare_config_update(path, {"beat_offset_ms": 20}, CONFIG_TEXT)
        external = CONFIG_TEXT + "\n# 其他人的修改\n"
        path.write_text(external)
        with self.assertRaisesRegex(ValueError, "外部修改"):
            save_config_text(path, text, CONFIG_TEXT)
        self.assertEqual(path.read_text(), external)
        with self.assertRaisesRegex(ValueError, "外部修改"):
            prepare_config_update(path, {"beat_offset_ms": 40}, CONFIG_TEXT)
        self.assertFalse(list(self.root.glob(".*.tmp")))


if __name__ == "__main__":
    unittest.main()
