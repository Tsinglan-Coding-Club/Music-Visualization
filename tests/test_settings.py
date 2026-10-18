"""设置工作流使用真实配置与假音频，不打开串口或图形窗口。"""

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from rhythm_game.app import RhythmApp
from rhythm_game.settings import SettingsPage
from rhythm_game.input_bridge import JudgmentInput
from tests.helpers import write_config
from tests.test_app import FakeAudio


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("rhythm_game.app.AudioEngine", FakeAudio))
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.directory).resolve()
        (self.root / "music.wav").write_bytes(b"fixture")
        self.path = write_config(self.root)
        self.original = self.path.read_text()
        self.app = RhythmApp(self.path, enable_glove=False)
        self.addCleanup(self.app.close)
        self.closed = False
        self.window = SimpleNamespace(controller=self.app, close_settings=self.close_settings)
        self.page = SettingsPage(self.window)

    def close_settings(self, saved=False):
        self.closed = self.page.leave(saved)

    def test_edit_tempo_meter_and_pattern_rebuilds_actual_chart(self):
        self.page.values.update(bpm="120", beats_per_bar="3", note_values="4, 8, 8, 4", beat_start_seconds="1.5")
        self.page.save()
        self.assertTrue(self.closed)
        self.assertEqual(self.app.config.bpm, 120)
        self.assertEqual(self.app.config.beats_per_bar, 3)
        self.assertEqual(self.app.config.note_values, (4, 8, 8, 4))
        self.assertEqual(self.app.chart.start, 1.5)
        self.assertEqual(self.app.chart.beat_seconds, .5)
        self.assertAlmostEqual(self.app.chart.targets[1].time, 2)

    def test_invalid_pattern_and_fractional_meter_keep_original(self):
        for changes in ({"note_values": "4, 8,"}, {"beats_per_bar": "3.5"}, {"note_values": "4, 4"}):
            before = dict(self.page.values)
            self.page.values.update(changes)
            self.page.save()
            self.assertFalse(self.closed)
            self.assertEqual(self.path.read_text(), self.original)
            self.page.values = before

    def test_new_song_is_used_for_calibration_preview_then_cancel_restores(self):
        song = self.root / "第二首.wav"
        song.write_bytes(b"fixture")
        self.page.select_music(song)
        self.page.values["bpm"] = "120"
        self.page.start_calibration()
        self.assertTrue(self.page.calibrating)
        self.assertEqual(self.app.config.music_path, song)
        self.assertEqual(self.app.config.bpm, 120)
        self.assertEqual(self.path.read_text(), self.original)
        self.close_settings()
        self.assertTrue(self.closed)
        self.assertEqual(self.app.config.music_path, self.root / "music.wav")
        self.assertEqual(self.app.config.bpm, 90)
        self.assertEqual(self.path.read_text(), self.original)

    def test_tempo_then_phase_results_require_explicit_save(self):
        self.page.start_calibration()
        for i in range(16):
            self.page.calibrator.add(2 + i * .5)
        self.page.finish_calibration()
        self.page.use_estimate()
        self.assertEqual(self.page.values["bpm"], "120")
        self.assertEqual(self.app.config.bpm, 90)
        self.assertEqual(self.path.read_text(), self.original)
        self.page.select_calibration("phase")
        self.assertIsNone(self.page.calibrator)
        self.page.start_calibration()
        self.assertEqual(self.app.config.bpm, 120)
        for i in range(16):
            self.page.calibrator.add(4.08 + i * .5)
        self.page.finish_calibration()
        self.page.use_estimate()
        self.assertAlmostEqual(float(self.page.values["beat_offset_ms"]), 80)
        self.page.save()
        self.assertTrue(self.closed)
        self.assertEqual(self.app.config.bpm, 120)
        self.assertAlmostEqual(self.app.chart.start, 4.08)

    def test_tempo_calibration_does_not_require_first_beat_within_short_song(self):
        self.page.values["beat_start_seconds"] = "25"
        self.page.start_calibration()
        self.assertTrue(self.page.calibrating)
        self.assertEqual(self.page.values["beat_start_seconds"], "25")
        self.assertEqual(self.app.config.beat_start_seconds, 0)
        self.assertEqual(self.path.read_text(), self.original)
        self.page.finish_calibration()
        self.page.select_calibration("phase")
        self.page.start_calibration()
        self.assertFalse(self.page.calibrating)

    def test_manual_finish_includes_last_captured_unsettled_tap(self):
        self.page.start_calibration()
        for i in range(7):
            self.page.calibrator.add(2 + i * .5)
        self.app._pending_inputs.append(JudgmentInput(20, 5.5, "glove", "calibration"))
        self.page.finish_calibration()
        self.assertEqual(self.page.calibrator.estimate().count, 8)
        self.assertTrue(self.page.calibrator.estimate().ready)
        self.assertEqual(self.app._pending_inputs, [])

    def test_accepted_settled_sample_is_kept_during_audio_eof_tail(self):
        self.page.start_calibration()
        self.app.engine.pause()  # 音频已到末尾，窗口仍在结算先前接受的输入。
        self.page.tap(10, 15.98, play_sound=False, source="glove", settled=True)
        self.assertEqual(self.page.calibrator.estimate().count, 1)
        self.page.tap(11, 15.99, play_sound=False, source="keyboard", settled=False)
        self.assertEqual(self.page.calibrator.estimate().count, 1)

    def test_invalid_file_does_not_replace_audio_or_start_sampling(self):
        engine = self.app.engine
        self.page.values["music_path"] = "missing.wav"
        self.page.start_calibration()
        self.assertFalse(self.page.calibrating)
        self.assertIs(self.app.engine, engine)
        self.assertEqual(self.path.read_text(), self.original)

    def test_typing_path_and_pattern_keeps_unicode_and_separators(self):
        self.page.focus, self.page.select_all = "music_path", True
        self.page.text("music/第二 首 #1.wav")
        self.assertEqual(self.page.values["music_path"], "music/第二 首 #1.wav")
        self.page.focus, self.page.select_all = "note_values", True
        self.page.text("4，8，8，4，4")
        self.assertEqual(self.page.updates()["note_values"], [4, 8, 8, 4, 4])

    def test_changed_calibration_inputs_discard_previous_estimate(self):
        for name in ("bpm", "beat_start_seconds", "beat_offset_ms", "input_offset_ms", "music_path"):
            self.page.calibrator = object()
            self.page._edited(name)
            self.assertIsNone(self.page.calibrator)

    def test_external_change_during_preview_keeps_draft_visible_on_failure(self):
        self.page.start_calibration()
        self.path.write_text("bad = ")
        self.close_settings()
        self.assertFalse(self.closed)
        self.assertTrue(self.page.status)


if __name__ == "__main__":
    unittest.main()
