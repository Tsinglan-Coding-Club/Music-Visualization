from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from rhythm_game.app import RhythmApp
from rhythm_game.audio import ClockReading
from rhythm_game.core import RhythmChart, RhythmSession
from tests.helpers import write_config


class FakeAudio:
    def __init__(self, config):
        self.config = config
        self.duration = 60.0
        self.sample_rate = 48000
        self.generation = 0
        self.underruns = 0
        self.latency_ms = 6.0
        self.stream = None
        self.clock = ClockReading(0, 0, False)
        self.claps = 0

    def open(self):
        self.stream = SimpleNamespace(active=True)

    def play(self, position=0):
        self.generation += 1
        self.clock = ClockReading(self.generation, position, True)
        return self.generation

    def reading(self, host_time=None):
        return self.clock

    def trigger_clap(self):
        self.claps += 1

    def pause(self):
        self.clock = replace(self.clock, playing=False)

    def resume(self):
        self.clock = replace(self.clock, playing=True)

    def stop(self):
        self.generation += 1
        self.clock = ClockReading(self.generation, 0, False)
        return self.generation

    def close(self):
        self.stream = None


class AppTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("rhythm_game.app.AudioEngine", FakeAudio))
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        root = Path(self.directory)
        (root / "music.wav").write_bytes(b"fixture")
        self.app = RhythmApp(write_config(root))
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.app.close()

    def set_position(self, position, **kwargs):
        self.app.engine.clock = ClockReading(self.app.expected_generation, position, **kwargs)

    def clap_at(self, time):
        self.set_position(time, playing=True)
        self.app.clap()

    def trigger(self, time=4.5):
        self.app.start()
        self.clap_at(time)
        self.assertIsNotNone(self.app.session.entry_time)
        return self.app.session.entry_time

    def test_music_to_input_sound_judgment_and_log(self):
        entry = self.trigger()
        self.clap_at(entry + .03)
        self.app.update()
        self.app.log.flush()
        records = [json.loads(line) for line in self.app.log_path.read_text().splitlines()]
        clap = next(row for row in records if row["event"] == "clap" and row["status"] == "hit")
        self.assertAlmostEqual(clap["error_ms"], 30)
        self.assertEqual(clap["source"], "keyboard")
        self.assertEqual(self.app.engine.claps, 2)  # 启动与正式拍各一次声音。
        self.assertEqual(self.app.session.hits, 1)

    def test_duplicate_clap_sounds_once_per_action_and_counts_extra_error(self):
        entry = self.trigger()
        self.clap_at(entry)
        self.app.clap()
        self.assertEqual(self.app.engine.claps, 3)
        self.assertEqual(self.app.session.hits, 1)
        self.assertEqual(self.app.session.mistakes, 1)

    def test_first_three_beats_start_and_last_beat_maps_to_next_bar(self):
        for time, attributed in ((4.1, 1), (4.9, 1), (5.9, 1), (6.0, 2), (6.6, 2)):
            self.trigger(time)
            self.assertEqual(self.app.session.attributed_bar, attributed)
            self.assertAlmostEqual(self.app.session.preparation_start_time, 4 + attributed * 8 / 3)
            self.assertAlmostEqual(self.app.session.entry_time, 4 + (attributed + 1) * 8 / 3)
            self.assertEqual(self.app.session.hits, 0)

    def test_complete_preparation_bar_is_unscored_and_missing_it_does_not_block(self):
        entry = self.trigger(5.7)
        self.set_position(entry - .2, playing=True)
        self.app.update()
        self.assertEqual(self.app.session.prep_misses, 4)
        self.assertEqual(self.app.session.mistakes, 0)
        self.clap_at(entry)
        self.assertEqual(self.app.session.hits, 1)
        self.assertFalse(self.app.session.failed)

    def test_preparation_accepts_each_quarter_and_preserves_formal_score(self):
        entry = self.trigger()
        for target in self.app.session.preparation_targets:
            self.clap_at(target.time)
        self.assertEqual(self.app.session.prep_hits, 4)
        self.assertEqual(self.app.session.hits, 0)
        self.assertEqual(self.app.session.mistakes, 0)
        self.clap_at(entry)
        self.assertEqual(self.app.session.hits, 1)

    def test_pause_resume_and_reset_keep_music_lifecycle_separate(self):
        entry = self.trigger()
        self.app.toggle_pause()
        generation = self.app.expected_generation
        self.app.clap()
        self.assertEqual(self.app.engine.claps, 1)
        self.assertEqual(self.app.session.entry_time, entry)
        self.app.toggle_pause()
        self.assertEqual(self.app.expected_generation, generation)
        self.clap_at(entry)
        self.assertEqual(self.app.session.hits, 1)
        self.app.reset_round()
        self.assertTrue(self.app.desired_playing)
        self.assertEqual(self.app.expected_generation, generation)
        self.assertIsNone(self.app.session.entry_time)

    def test_playback_does_not_auto_start_without_clap(self):
        self.app.start()
        self.set_position(20, playing=True)
        self.app.update()
        self.assertIsNone(self.app.session.entry_time)
        self.assertEqual(self.app.session.mistakes, 0)

    def test_failure_freezes_round_but_keeps_music_and_allows_new_attempt(self):
        entry = self.trigger()
        generation = self.app.expected_generation
        self.set_position(entry + 2.05, playing=True)
        self.app.update()
        self.assertTrue(self.app.session.failed)
        self.assertEqual(self.app.session.mistakes, 3)
        self.assertEqual(self.app.session.misses, 4)
        self.assertEqual(self.app.session.mistake_beats, frozenset({0, 1, 2}))
        self.assertTrue(self.app.desired_playing)
        self.assertEqual(self.app.expected_generation, generation)
        before = dict(self.app.session.states)
        self.set_position(entry + 2.1, playing=True)
        self.app.update()
        self.assertEqual(self.app.session.states, before)
        self.app.clap()
        self.assertFalse(self.app.session.failed)
        self.assertEqual(self.app.session.mistakes, 0)
        self.assertGreater(self.app.session.entry_time, entry)
        self.assertEqual(self.app.expected_generation, generation)
        self.app.log.flush()
        outcomes = [json.loads(line) for line in self.app.log_path.read_text().splitlines()]
        self.assertEqual([r["outcome"] for r in outcomes if r["event"] == "round_end"], ["failed"])

    def test_extra_claps_share_one_error_per_quarter_before_failure(self):
        entry = self.trigger()
        self.clap_at(entry)
        for i in range(3):
            self.clap_at(entry + .01 * (i + 1))
        self.assertFalse(self.app.session.failed)
        self.assertEqual(self.app.session.mistakes, 1)
        for beat in (1, 2):
            time = entry + beat * self.app.chart.beat_seconds
            self.clap_at(time)
            self.clap_at(time + .01)
        self.assertTrue(self.app.session.failed)
        self.assertEqual(self.app.session.hits, 3)
        self.assertEqual(self.app.session.mistakes, 3)
        self.assertEqual(self.app.session.mistake_beats, frozenset({0, 1, 2}))
        self.assertEqual(self.app.session.entry_time, entry)
        self.assertTrue(self.app.desired_playing)

    def test_auto_demo_covers_preparation_and_all_formal_bars_once(self):
        self.app.auto_input = True
        self.app.start()
        self.set_position(21, playing=True)
        self.app.inject_due_targets()
        self.app.update()
        self.assertTrue(self.app.session.finished)
        self.assertFalse(self.app.session.failed)
        self.assertEqual(self.app.session.hits, 20)
        self.assertEqual(self.app.session.misses, 0)
        self.assertEqual(self.app.session.prep_hits, 4)
        self.assertEqual(self.app.engine.claps, 25)
        self.app.inject_due_targets()
        self.assertEqual(self.app.engine.claps, 25)

    def test_auto_does_not_add_next_round_note_at_floating_bar_end(self):
        text = self.app.config_path.read_text()
        self.assertTrue(self.app.apply_settings({"bpm": 127}, text))
        self.app.auto_input = True
        self.app.start()
        self.set_position(17, playing=True)
        self.app.inject_due_targets()
        self.app.update()
        self.assertTrue(self.app.session.finished)
        self.assertEqual(self.app.session.hits, 20)
        self.assertEqual(self.app.engine.claps, 25)
        self.assertEqual(self.app.session.first_formal_bar, 1)

    def test_auto_initial_trigger_never_has_negative_raw_time(self):
        text = self.app.config_path.read_text()
        self.assertTrue(self.app.apply_settings({"beat_start_seconds": 0, "input_offset_ms": -50,
                                                 "game_bars": 1}, text))
        self.app.auto_input = True
        self.app.start()
        self.set_position(8.1, playing=True)
        self.app.inject_due_targets()
        self.app.update()
        self.assertTrue(self.app.session.finished)
        self.assertEqual(self.app.session.hits, 5)
        self.assertEqual(self.app.engine.claps, 10)
        self.assertEqual(self.app.session.first_formal_bar, 1)

    def test_three_quarter_multibar_dotted_pattern_reaches_actual_judgment(self):
        text = self.app.config_path.read_text()
        first = [8, 16, 16, "8.", 16, 4]
        second = [16, 16, 8, 16, "8.", 4]
        self.assertTrue(self.app.apply_settings({"beats_per_bar": 3, "bpm": 120, "note_values": first,
                          "patterns": [first, second], "game_bars": 3, "max_mistakes": 2}, text))
        entry = self.trigger(4.25)
        self.assertEqual(self.app.session.preparation_start_time, 5.5)
        self.assertEqual(entry, 7)
        for target in self.app.chart.targets:
            if entry <= target.time < self.app.session.round_end_time:
                self.clap_at(target.time)
        self.set_position(self.app.session.round_end_time + .01, playing=True)
        self.app.update()
        self.assertEqual(self.app.session.hits, 18)
        self.assertEqual(self.app.session.mistakes, 0)
        self.assertTrue(self.app.session.finished)
        self.assertTrue(self.app.desired_playing)
        self.app.clap()
        self.assertFalse(self.app.session.finished)
        self.assertEqual(self.app.session.hits, 0)

    def test_late_round_starts_pattern_one_and_log_uses_round_time(self):
        text = self.app.config_path.read_text()
        self.assertTrue(self.app.apply_settings({"note_values": [4, 4, 4, 4],
                         "patterns": [[4, 4, 4, 4], [8] * 8], "game_bars": 2}, text))
        entry = self.trigger(13.2)
        self.assertEqual(self.app.session.first_formal_bar, 1)
        self.assertEqual([t.denominator for t in self.app.session.formal_targets[:4]], [4] * 4)
        self.assertTrue(all(t.bar == 1 for t in self.app.session.formal_targets[:4]))
        for target in self.app.session.preparation_targets:
            self.clap_at(target.time)
        self.assertIsNone(self.app.session.accuracy)
        self.assertEqual(self.app.session.hits, 0)
        self.clap_at(entry)
        self.app.update()
        self.assertEqual(self.app.session.accuracy, 1)
        self.app.log.flush()
        rows = [json.loads(line) for line in self.app.log_path.read_text().splitlines()]
        first = next(r for r in rows if r["event"] == "target")
        self.assertEqual(first["target_index"], 0)
        self.assertAlmostEqual(first["target_time"], entry)

    def test_stale_generation_ignored_after_restart(self):
        self.app.start()
        previous = ClockReading(self.app.expected_generation, 4, True)
        self.app.start()
        self.app.engine.clock = previous
        self.app.clap()
        self.assertEqual(self.app.engine.claps, 0)
        self.assertEqual(self.app.session.hits, 0)

    def test_eof_waits_for_compensated_accepted_event_before_miss(self):
        self.app.config = replace(self.app.config, input_offset_ms=80)
        self.app.engine.duration = 4.05
        self.app.chart = RhythmChart(90, 4, 4, 0, [4, 8, 8, 4, 4], 4.05)
        self.app.session = RhythmSession(self.app.chart, 100, 80)
        self.app.start()
        self.app.session.arm(4)  # 明确测试 EOF 时间轴，不走需要完整小节的启动流程。
        self.set_position(4.05, playing=False, tail_seconds=0.03)
        self.app.update()
        self.assertEqual(self.app.session.misses, 0)
        self.app.clap()
        self.assertEqual(self.app.session.hits, 1)
        self.set_position(4.05, playing=False, tail_seconds=0.2)
        self.app.update()
        self.assertFalse(self.app.desired_playing)

    def test_invalid_reload_preserves_running_engine(self):
        self.app.start()
        previous = self.app.engine
        self.app.config_path.write_text("bad = ")
        self.app.reload()
        self.assertIs(self.app.engine, previous)
        self.assertTrue(self.app.desired_playing)

    def test_settings_apply_updates_disk_and_runtime_together(self):
        original = self.app.config_path.read_text()
        self.assertTrue(self.app.apply_settings({"beat_offset_ms": 80, "input_offset_ms": 30}, original))
        self.assertAlmostEqual(self.app.chart.start, 4.080)
        self.assertEqual(self.app.session.input_offset_ms, 30)
        self.assertIn("beat_offset_ms = 80", self.app.config_path.read_text())
        self.assertFalse(self.app.desired_playing)

    def test_failed_device_or_disk_save_restores_previous_config(self):
        original = self.app.config_path.read_text()
        old_engine = self.app.engine
        replacement = FakeAudio(self.app.config)
        with patch("rhythm_game.app.AudioEngine", return_value=replacement), patch.object(replacement, "open", side_effect=RuntimeError("device failed")):
            self.assertFalse(self.app.apply_settings({"blocksize": 64}, original))
        self.assertIs(self.app.engine, old_engine)
        self.assertTrue(old_engine.stream.active)
        self.assertEqual(self.app.config_path.read_text(), original)
        with patch("rhythm_game.app.save_config_text", side_effect=OSError("disk failed")):
            self.assertFalse(self.app.apply_settings({"beat_offset_ms": 50}, original))
        self.assertIs(self.app.engine, old_engine)
        self.assertTrue(old_engine.stream.active)
        self.assertEqual(self.app.config.beat_offset_ms, 0)
        self.assertEqual(self.app.config_path.read_text(), original)

    def test_automatic_inputs_keep_known_timestamps_after_visual_stall(self):
        self.app.auto_input = True
        self.app.start()
        self.set_position(14.5, playing=True)
        self.app.inject_due_targets()
        self.app.update()
        self.assertEqual(self.app.session.prep_hits, 4)
        self.assertEqual(self.app.session.hits, 10)
        self.assertEqual(self.app.session.misses, 0)
        self.assertEqual(self.app.session.mistakes, 0)

if __name__ == "__main__":
    unittest.main()
