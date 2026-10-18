import json
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
        self.assertFalse(cfg.glove_enabled)

    def test_glove_config_survives_settings_save(self):
        original = CONFIG_TEXT + '\n[glove]\nenabled = true\nport = "COM7"\n'
        path = write_config(self.root, original)
        text, config = prepare_config_update(path, {"beat_offset_ms": 30}, original)
        self.assertTrue(config.glove_enabled)
        self.assertEqual(config.glove_port, "COM7")
        self.assertEqual(config.glove_baudrate, 115200)
        self.assertEqual(config.glove_debounce_ms, 30)
        self.assertIn('port = "COM7"', text)

    def test_glove_rejects_invalid_boolean_and_timing(self):
        for field in ('enabled = "true"', 'debounce_ms = nan', 'debounce_ms = -1', 'baudrate = 115200.5'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                load_config(write_config(self.root, CONFIG_TEXT + '\n[glove]\n' + field + '\n'))

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

    def test_rhythm_fields_validate_as_one_update(self):
        path = write_config(self.root)
        updates = {"bpm": 120, "beat_start_seconds": 1.25, "beats_per_bar": 3,
                   "note_values": (4, 8, 8, 4), "lead_in_bars": 1}
        text, config = prepare_config_update(path, updates, CONFIG_TEXT)
        self.assertEqual(config.bpm, 120)
        self.assertEqual(config.beat_start_seconds, 1.25)
        self.assertEqual(config.beats_per_bar, 3)
        self.assertEqual(config.note_values, (4, 8, 8, 4))
        self.assertEqual(config.lead_in_bars, 1)
        self.assertEqual(config.blocksize, 128)
        self.assertEqual(path.read_text(), CONFIG_TEXT)
        save_config_text(path, text, CONFIG_TEXT)
        self.assertEqual(load_config(path), config)

    def test_coupled_rhythm_checks_remain_enabled(self):
        path = write_config(self.root)
        for updates in ({"beats_per_bar": 3}, {"note_values": [4]},
                        {"bpm": 600}, {"note_values": [64] * 64},
                        {"beat_start_seconds": 0, "beat_offset_ms": -1}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                prepare_config_update(path, updates, CONFIG_TEXT)
        _, config = prepare_config_update(
            path, {"bpm": 600, "note_values": [4] * 4, "window_ms": 40}, CONFIG_TEXT)
        self.assertEqual(config.bpm, 600)
        self.assertEqual(config.window_ms, 40)

    def test_editable_field_types_are_checked(self):
        path = write_config(self.root)
        invalid = [
            {"bpm": True}, {"bpm": float("nan")}, {"bpm": float("inf")},
            {"bpm": "120"}, {"beat_start_seconds": -1}, {"beat_start_seconds": False},
            {"beats_per_bar": 3.5}, {"beats_per_bar": True}, {"beats_per_bar": 0},
            {"lead_in_bars": 0.5}, {"lead_in_bars": 2}, {"lead_in_bars": False},
            {"note_values": []}, {"note_values": "4 4 4 4"},
            {"note_values": [True, 4, 4, 4]}, {"note_values": [4.0] * 4},
            {"note_values": [3] * 3}, {"music_path": 3}, {"music_path": "  "},
            {"music_path": "missing.wav"},
        ]
        for updates in invalid:
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                prepare_config_update(path, updates, CONFIG_TEXT)
            self.assertEqual(path.read_text(), CONFIG_TEXT)

    def test_music_path_roundtrip_preserves_unicode_quotes_hash_and_backslash(self):
        old_music = self.root / r'旧 # \"曲.wav'
        old_music.write_bytes(b"test")
        original = CONFIG_TEXT.replace('path = "music.wav"',
                                       "path = " + json.dumps(str(old_music.name), ensure_ascii=False)
                                       + "   # 保留曲目说明")
        path = write_config(self.root, original)
        folder = self.root / "music"
        folder.mkdir()
        music = folder / r'新 # \"曲.wav'
        music.write_bytes(b"test")
        text, config = prepare_config_update(path, {"music_path": music}, original)
        self.assertIn("   # 保留曲目说明", text)
        self.assertIn('path = ' + json.dumps(str(music.relative_to(self.root)), ensure_ascii=False), text)
        self.assertEqual(config.music_path, music.resolve())
        save_config_text(path, text, original)
        self.assertEqual(load_config(path).music_path, music.resolve())

    def test_music_relative_draft_and_external_paths(self):
        folder = self.root / "music"
        folder.mkdir()
        music = folder / "另一首.mp3"
        music.write_bytes(b"test")
        path = write_config(self.root)
        text, config = prepare_config_update(path, {"music_path": "music/../music/另一首.mp3"}, CONFIG_TEXT)
        self.assertIn('path = "music/另一首.mp3"', text)
        self.assertEqual(config.music_path, music.resolve())
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside) / '外部 # "音乐.wav'
            external.write_bytes(b"test")
            text, config = prepare_config_update(path, {"music_path": str(external)}, CONFIG_TEXT)
            self.assertIn("path = " + json.dumps(str(external.resolve()), ensure_ascii=False), text)
            self.assertEqual(config.music_path, external.resolve())

    def test_multiline_note_template_keeps_comments_and_unrelated_settings(self):
        original = CONFIG_TEXT.replace("note_values = [4, 8, 8, 4, 4]", """note_values = [
    4, # 第一拍
    8, 8, 4, 4,
]  # 小节结束""")
        path = write_config(self.root, original)
        text, config = prepare_config_update(path, {"note_values": [2, 2]}, original)
        self.assertIn("# 第一拍", text)
        self.assertIn("note_values = [2, 2]  # 小节结束", text)
        self.assertIn('device = ""', text)
        self.assertEqual(config.note_values, (2, 2))

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
