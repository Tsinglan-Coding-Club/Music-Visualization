from types import SimpleNamespace
import unittest
from unittest.mock import patch

from rhythm_game.audio import ClockReading
from rhythm_game.glove import ContactTransition
from rhythm_game.input_bridge import InputBridge


class InputBridgeTests(unittest.TestCase):
    def setUp(self):
        self.clock = self.enterContext(patch("rhythm_game.input_bridge.perf_counter", return_value=100))
        self.engine = SimpleNamespace(duration=20, claps=0)
        self.engine.reading = lambda host: ClockReading(1, host - 97, True)
        self.engine.trigger_clap = self.sound
        self.bridge = InputBridge()

    def sound(self):
        self.engine.claps += 1

    def activate(self, mode="game"):
        self.bridge.activate(self.engine, 1, mode, .1, .25)

    def contact(self, onset=101):
        self.clock.return_value = onset + .035
        return ContactTransition(True, onset, onset + .035, 4000)

    def test_sound_precedes_visual_poll_and_judgment_uses_onset(self):
        self.activate()
        self.bridge.receive(self.contact())
        self.assertEqual(self.engine.claps, 1)
        _, event = self.bridge.drain()[0]
        self.assertTrue(event.accepted)
        self.assertEqual(event.music_time, 4)
        self.assertEqual(event.host_time, 101)
        self.assertTrue(self.bridge.is_current(event))

    def test_dressing_and_contact_started_before_playback_are_ignored(self):
        self.bridge.receive(self.contact(99))
        self.clock.return_value = 100
        self.activate()
        self.bridge.receive(self.contact(99.99))
        self.assertEqual(self.engine.claps, 0)
        self.assertTrue(all(not event.accepted for _, event in self.bridge.drain()))

    def test_pause_and_restart_invalidate_old_queued_judgments(self):
        self.activate()
        self.bridge.receive(self.contact())
        _, event = self.bridge.drain()[0]
        self.bridge.deactivate()
        self.assertFalse(self.bridge.is_current(event))
        self.bridge.receive(self.contact(102))
        self.clock.return_value = 103
        self.activate()
        self.assertFalse(self.bridge.is_current(event))
        self.assertEqual(self.engine.claps, 1)

    def test_replaced_serial_source_cannot_play_against_new_route(self):
        old_receive, old_record = self.bridge.source_callbacks()
        self.activate()
        new_receive, _ = self.bridge.source_callbacks()
        old_receive(self.contact())
        old_record("old_packet")
        self.assertEqual(self.bridge.drain(), [])
        new_receive(self.contact())
        self.assertEqual(self.engine.claps, 1)

    def test_stale_input_and_release_do_not_sound(self):
        self.activate()
        event = self.contact()
        self.clock.return_value = 102
        self.bridge.receive(event)
        self.bridge.receive(ContactTransition(False, 102, 102.04, 5000))
        self.assertEqual(self.engine.claps, 0)

    def test_wrong_audio_generation_and_expired_eof_are_ignored(self):
        self.activate()
        self.engine.reading = lambda _: ClockReading(0, 4, True)
        self.bridge.receive(self.contact())
        self.engine.reading = lambda _: ClockReading(1, 20, False, .2)
        self.bridge.receive(self.contact())
        self.assertEqual(self.engine.claps, 0)

    def test_calibration_route_is_kept_separate(self):
        self.activate("calibration")
        self.bridge.receive(self.contact())
        _, event = self.bridge.drain()[0]
        self.assertEqual(event.mode, "calibration")
        self.assertTrue(event.accepted)


if __name__ == "__main__":
    unittest.main()
