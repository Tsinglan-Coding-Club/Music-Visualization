import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rhythm_game.app import RhythmApp
from rhythm_game.audio import ClockReading
from rhythm_game.glove import ContactTransition
from tests.helpers import CONFIG_TEXT, write_config
from tests.test_app import FakeAudio


class FakeGlove:
    def __init__(self, config, receive, record):
        self.config, self.receive, self.record = config, receive, record
        self.status = "ready"
        self.closed = False

    def start(self):
        pass

    def close(self):
        self.closed = True

    def reconnect(self):
        pass


class GloveAppTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("rhythm_game.app.AudioEngine", FakeAudio))
        self.enterContext(patch("rhythm_game.app.GloveInput", FakeGlove))
        self.clock = self.enterContext(patch("rhythm_game.input_bridge.perf_counter", return_value=100))
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (root / "music.wav").write_bytes(b"fixture")
        config = write_config(root, CONFIG_TEXT + '\n[glove]\nenabled = true\n')
        self.app = RhythmApp(config)
        self.addCleanup(self.app.close)

    def send(self, position=4.03, onset=101):
        self.clock.return_value = onset + .035
        self.app.engine.clock = ClockReading(self.app.expected_generation, position, True)
        self.app.glove.receive(ContactTransition(True, onset, onset + .035, 4000))

    def test_hand_sounds_before_update_then_scores_once(self):
        self.app.start()
        self.send()
        self.assertEqual(self.app.engine.claps, 1)
        self.assertEqual(self.app.session.hits, 0)
        self.app.update()
        self.assertEqual(self.app.session.hits, 0)
        self.app.engine.clock = ClockReading(self.app.expected_generation, 4.150, True)
        self.app.update()
        self.assertEqual(self.app.engine.claps, 1)
        self.assertEqual(self.app.session.hits, 1)
        self.app.log.flush()
        rows = [json.loads(line) for line in self.app.log_path.read_text().splitlines()]
        self.assertEqual(next(row for row in rows if row["event"] == "clap")["source"], "glove")

    def test_confirmation_delay_does_not_turn_in_window_edge_into_miss(self):
        self.app.start()
        self.app.session.arm(4)
        self.app.engine.clock = ClockReading(self.app.expected_generation, 4.115, True)
        self.app.update()
        self.assertEqual(self.app.session.misses, 0)
        reading = self.app.engine.reading
        edge = ClockReading(self.app.expected_generation, 4.090, True)
        self.app.engine.reading = lambda host=None: edge if host == 101 else reading(host)
        self.send(position=4.140)
        self.app.update()
        self.app.engine.clock = ClockReading(self.app.expected_generation, 4.200, True)
        self.app.update()
        self.assertEqual(self.app.session.hits, 1)
        self.assertEqual(self.app.session.misses, 0)

    def test_calibration_input_does_not_score_game(self):
        self.app.start(input_mode="calibration")
        self.send(position=4)
        self.app.engine.clock = ClockReading(self.app.expected_generation, 4.100, True)
        inputs = self.app.update()
        self.assertEqual(len(inputs), 1)
        self.assertEqual(inputs[0].music_time, 4)
        self.assertIsNone(self.app.session.entry_time)
        self.assertEqual(self.app.engine.claps, 1)

    def test_stop_invalidates_already_queued_hand_before_judgment(self):
        self.app.start()
        self.send()
        self.app.stop()
        self.app.update()
        self.assertIsNone(self.app.session.entry_time)
        self.assertEqual(self.app.session.hits, 0)

    def test_hold_that_started_before_playback_does_not_trigger(self):
        self.app.start()
        self.send(onset=99.99)
        self.app.update()
        self.assertEqual(self.app.engine.claps, 0)
        self.assertIsNone(self.app.session.entry_time)

    def test_settings_reload_keeps_same_serial_connection(self):
        source = self.app.glove
        self.assertTrue(self.app.apply_settings({"beat_offset_ms": 20}, self.app.config_path.read_text()))
        self.assertIs(self.app.glove, source)
        self.assertFalse(source.closed)

    def test_closed_source_callback_cannot_affect_restart(self):
        source = self.app.glove
        text = self.app.config_path.read_text().replace("enabled = true", "enabled = false")
        self.app.config_path.write_text(text)
        self.assertTrue(self.app.reload())
        self.app.start()
        self.clock.return_value = 101.04
        self.app.engine.clock = ClockReading(self.app.expected_generation, 4, True)
        source.receive(ContactTransition(True, 101, 101.04, 4000))
        self.app.update()
        self.assertEqual(self.app.engine.claps, 0)
        self.assertEqual(self.app.session.hits, 0)
        self.assertTrue(source.closed)

    def test_close_releases_source_and_is_idempotent(self):
        source = self.app.glove
        self.app.close()
        self.app.close()
        self.assertTrue(source.closed)

    def test_automatic_demo_does_not_accept_hand_inputs(self):
        self.app.auto_input = True
        self.app.start()
        self.send()
        self.app.update()
        self.assertEqual(self.app.engine.claps, 0)

    def test_later_keyboard_input_does_not_rewrite_earlier_hand_onset(self):
        self.app.start()
        self.app.session.arm(4)
        self.app.engine.clock = ClockReading(self.app.expected_generation, 4.110, True)
        self.app.clap(host_time=101.110)
        self.send(position=4.090, onset=101.090)
        self.app.engine.clock = ClockReading(self.app.expected_generation, 4.250, True)
        self.app.update()
        self.assertEqual(self.app.session.hits, 1)
        self.assertEqual(self.app.session.misses, 0)
        self.app.log.flush()
        rows = [json.loads(line) for line in self.app.log_path.read_text().splitlines()]
        claps = [row for row in rows if row["event"] == "clap"]
        self.assertEqual([row["source"] for row in claps], ["glove", "keyboard"])
        self.assertAlmostEqual(claps[0]["error_ms"], 90)
        self.assertAlmostEqual(claps[1]["error_ms"], 110)

    def test_input_older_than_settlement_budget_is_rejected_without_sound(self):
        self.app.start()
        self.app.session.arm(4)
        self.clock.return_value = 101.110
        self.app.engine.clock = ClockReading(self.app.expected_generation, 4.090, True)
        self.app.glove.receive(ContactTransition(True, 101, 101.110, 4000))
        self.app.engine.clock = ClockReading(self.app.expected_generation, 4.250, True)
        self.app.update()
        self.assertEqual(self.app.engine.claps, 0)
        self.assertEqual(self.app.session.misses, 1)

    def test_callback_after_poll_cannot_be_behind_sampled_settlement_watermark(self):
        self.app.start()
        self.app.session.arm(4)
        self.app.engine.clock = ClockReading(self.app.expected_generation, 4.179, True)
        reading = self.app.engine.reading
        edge = ClockReading(self.app.expected_generation, 4.100, True)
        self.app.engine.reading = lambda host=None: edge if host == 101.101 else reading(host)
        poll = self.app.poll_glove_inputs

        def arrive_after_drain():
            poll()
            self.app.engine.clock = ClockReading(self.app.expected_generation, 4.181, True)
            self.clock.return_value = 101.180
            self.app.glove.receive(ContactTransition(True, 101.101, 101.180, 4000))

        with patch.object(self.app, "poll_glove_inputs", side_effect=arrive_after_drain):
            self.app.update()
        self.assertEqual(self.app.session.misses, 0)
        self.app.engine.clock = ClockReading(self.app.expected_generation, 4.250, True)
        self.app.update()
        self.assertEqual(self.app.session.hits, 1)


if __name__ == "__main__":
    unittest.main()
