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
        self.duration = 16.0
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

    def test_music_to_input_sound_judgment_and_log(self):
        self.app.start()
        self.set_position(4.03, playing=True)
        self.app.clap()
        self.app.update()
        self.app.log.flush()
        records = [json.loads(line) for line in self.app.log_path.read_text().splitlines()]
        clap = next(row for row in records if row["event"] == "clap")
        self.assertAlmostEqual(clap["error_ms"], 30)
        self.assertEqual(clap["status"], "hit")
        self.assertEqual(self.app.engine.claps, 1)
        self.assertEqual(self.app.session.hits, 1)

    def test_duplicate_clap_still_sounds_but_does_not_score_twice(self):
        self.app.start()
        self.set_position(4, playing=True)
        self.app.clap()
        self.app.clap()
        self.assertEqual(self.app.engine.claps, 2)
        self.assertEqual(self.app.session.hits, 1)

    def test_clap_starts_later_bar_and_pause_resume(self):
        self.app.start()
        self.set_position(4.5, playing=True)
        self.app.clap()
        self.assertIsNone(self.app.session.entry_time)
        self.set_position(4 + 8 / 3, playing=True)
        self.app.clap()
        self.assertAlmostEqual(self.app.session.entry_time, 4 + 8 / 3)
        self.assertEqual(self.app.session.hits, 1)
        self.app.toggle_pause()
        self.app.clap()
        self.assertEqual(self.app.engine.claps, 2)
        self.app.toggle_pause()
        self.set_position(self.app.session.entry_time + 2 / 3, playing=True)
        self.app.clap()
        self.assertEqual(self.app.session.hits, 2)

    def test_playback_waits_for_first_clap_then_scores_only_one_bar(self):
        self.app.start()
        self.set_position(5, playing=True)
        self.app.update()
        self.assertIsNone(self.app.session.entry_time)
        self.assertEqual(self.app.session.misses, 0)
        self.set_position(4 + 8 / 3, playing=True)
        self.app.clap()
        self.set_position(12.4, playing=True)
        self.app.update()
        self.assertTrue(self.app.session.finished)
        self.assertEqual(self.app.session.hits, 1)
        self.assertEqual(self.app.session.misses, 4)
        self.assertEqual(len(self.app.session.states), 5)
        # 其他拍上拍手不清空上一小节结果。
        self.app.clap()
        self.assertEqual(self.app.session.hits, 1)
        self.assertEqual(self.app.session.misses, 4)
        self.set_position(4 + 4 * 8 / 3, playing=True)
        self.app.clap()
        self.assertEqual(self.app.session.hits, 1)
        self.assertEqual(self.app.session.misses, 0)

    def test_auto_demo_finishes_one_bar_without_starting_later_bars(self):
        self.app.start()
        self.set_position(10, playing=True)
        self.app.inject_due_targets()
        self.app.update()
        self.assertTrue(self.app.session.finished)
        self.assertEqual(self.app.session.hits, 5)
        self.assertEqual(self.app.session.misses, 0)
        self.assertEqual(self.app.engine.claps, 5)

    def enable_lead_in(self):
        self.app.config = replace(self.app.config, lead_in_bars=1)
        self.app.session = RhythmSession(self.app.chart, self.app.config.window_ms,
                                         self.app.config.input_offset_ms, lead_in_bars=1)

    def test_trigger_waits_one_bar_before_formal_first_target(self):
        self.enable_lead_in()
        self.app.start()
        self.set_position(4, playing=True)
        self.app.clap()
        self.assertEqual(self.app.session.hits, 0)
        self.assertAlmostEqual(self.app.session.entry_time, 4 + 8 / 3)
        self.set_position(5, playing=True)
        self.app.clap()
        self.assertEqual(self.app.session.hits, 0)
        self.set_position(4 + 8 / 3, playing=True)
        self.app.clap()
        self.assertEqual(self.app.session.hits, 1)
        self.set_position(10, playing=True)
        self.app.update()
        self.assertTrue(self.app.session.finished)
        self.assertEqual(self.app.session.misses, 4)

    def test_auto_demo_includes_unscored_trigger_and_one_formal_bar(self):
        self.enable_lead_in()
        self.app.start()
        self.set_position(10, playing=True)
        self.app.inject_due_targets()
        self.app.update()
        self.assertTrue(self.app.session.finished)
        self.assertEqual(self.app.session.hits, 5)
        self.assertEqual(self.app.session.misses, 0)
        self.assertEqual(self.app.engine.claps, 6)
        self.assertEqual(set(self.app.session.states), set(range(5, 10)))

    def test_stale_generation_ignored_after_restart(self):
        self.app.start()
        previous = ClockReading(self.app.expected_generation, 4, True)
        self.app.start()
        self.app.engine.clock = previous
        self.app.clap()
        self.assertEqual(self.app.engine.claps, 0)
        self.assertEqual(self.app.session.hits, 0)

    def test_eof_waits_for_compensated_event_before_miss(self):
        self.app.config = replace(self.app.config, input_offset_ms=80)
        self.app.engine.duration = 4.05
        self.app.chart = RhythmChart(90, 4, 4, 0, [4, 8, 8, 4, 4], 4.05)
        self.app.session = RhythmSession(self.app.chart, 100, 80)
        self.app.start()
        # 目标 4s；输入 4.08s 到达，比文件结束晚 30ms，但补偿后恰好命中。
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

    def test_automatic_inputs_keep_known_timestamps_after_visual_stall(self):
        self.app.start()
        self.set_position(5.5, playing=True)
        self.app.inject_due_targets()
        self.app.update()
        self.assertEqual(self.app.session.hits, 4)
        self.assertEqual(self.app.session.misses, 0)

if __name__ == "__main__":
    unittest.main()
