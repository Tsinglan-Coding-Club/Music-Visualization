"""Joy-Con 脚本输入的控制器测试；socket 线程用替身，不打开真实端口。"""

import json
from pathlib import Path
import socket
import time
import tempfile
import unittest
from unittest.mock import patch

from rhythm_game.app import RhythmApp
from rhythm_game.audio import ClockReading
from rhythm_game.input_bridge import ContactTransition
from tests.helpers import CONFIG_TEXT, write_config
from tests.test_app import FakeAudio


class FakeClapInput:
    def __init__(self, config, receive, record):
        self.config, self.receive, self.record = config, receive, record
        self.status = "waiting"
        self.closed = False

    def start(self):
        pass

    def close(self):
        self.closed = True

    def restart(self):
        pass


class ClapInputAppTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("rhythm_game.app.AudioEngine", FakeAudio))
        self.enterContext(patch("rhythm_game.app.ClapSocketInput", FakeClapInput))
        self.clock = self.enterContext(patch("rhythm_game.input_bridge.perf_counter", return_value=100))
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (root / "music.wav").write_bytes(b"fixture")
        config = write_config(root, CONFIG_TEXT + '\n[input]\nenabled = true\n')
        self.app = RhythmApp(config)
        self.addCleanup(self.app.close)

    def send(self, position=4.03, onset=101):
        self.clock.return_value = onset + .035
        self.app.engine.clock = ClockReading(self.app.expected_generation, position, True)
        self.app.clap_input.receive(ContactTransition(True, onset, onset + .035, None))

    def test_external_clap_sounds_before_update_then_scores_once(self):
        self.app.start()
        self.app.session.arm(4)
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
        self.assertEqual(next(row for row in rows if row["event"] == "clap")["source"], "joycon")

    def test_external_clap_can_trigger_on_middle_beat_then_prepare_and_play(self):
        self.app.start()
        generation = self.app.expected_generation

        def clap_at(position, onset):
            self.send(position=position, onset=onset)
            self.app.engine.clock = ClockReading(generation, position + .12, True)
            self.app.update()

        clap_at(5.1, 101)
        self.assertEqual(self.app.session.attributed_bar, 1)
        self.assertAlmostEqual(self.app.session.entry_time, 4 + 16 / 3)
        for i, target in enumerate(self.app.session.preparation_targets):
            clap_at(target.time, 102 + i)
        self.assertEqual(self.app.session.prep_hits, 4)
        self.assertEqual(self.app.session.hits, 0)
        clap_at(self.app.session.entry_time, 106)
        self.assertEqual(self.app.session.hits, 1)
        self.assertEqual(self.app.session.mistakes, 0)
        self.assertEqual(self.app.engine.claps, 6)
        self.assertEqual(self.app.expected_generation, generation)

    def test_calibration_input_does_not_score_game(self):
        self.app.start(input_mode="calibration")
        self.send(position=4)
        self.app.engine.clock = ClockReading(self.app.expected_generation, 4.100, True)
        inputs = self.app.update()
        self.assertEqual(len(inputs), 1)
        self.assertEqual(inputs[0].music_time, 4)
        self.assertIsNone(self.app.session.entry_time)
        self.assertEqual(self.app.engine.claps, 1)

    def test_stop_invalidates_already_queued_clap_before_judgment(self):
        self.app.start()
        self.send()
        self.app.stop()
        self.app.update()
        self.assertIsNone(self.app.session.entry_time)
        self.assertEqual(self.app.session.hits, 0)

    def test_clap_that_started_before_playback_does_not_trigger(self):
        self.app.start()
        self.send(onset=99.99)
        self.app.update()
        self.assertEqual(self.app.engine.claps, 0)
        self.assertIsNone(self.app.session.entry_time)

    def test_late_message_is_rejected_without_sound(self):
        self.app.start()
        self.app.session.arm(4)
        self.clock.return_value = 101.300
        self.app.engine.clock = ClockReading(self.app.expected_generation, 4.090, True)
        self.app.clap_input.receive(ContactTransition(True, 101, 101.300, None))
        self.app.update()
        self.assertEqual(self.app.engine.claps, 0)
        self.assertEqual(self.app.session.hits, 0)

    def test_settings_reload_keeps_same_input_source(self):
        source = self.app.clap_input
        self.assertTrue(self.app.apply_settings({"beat_offset_ms": 20}, self.app.config_path.read_text()))
        self.assertIs(self.app.clap_input, source)
        self.assertFalse(source.closed)

    def test_closed_source_callback_cannot_affect_restart(self):
        source = self.app.clap_input
        text = self.app.config_path.read_text().replace("enabled = true", "enabled = false")
        self.app.config_path.write_text(text)
        self.assertTrue(self.app.reload())
        self.app.start()
        self.clock.return_value = 101.04
        self.app.engine.clock = ClockReading(self.app.expected_generation, 4, True)
        source.receive(ContactTransition(True, 101, 101.04, None))
        self.app.update()
        self.assertEqual(self.app.engine.claps, 0)
        self.assertEqual(self.app.session.hits, 0)
        self.assertTrue(source.closed)

    def test_close_releases_source_and_is_idempotent(self):
        source = self.app.clap_input
        self.app.close()
        self.app.close()
        self.assertTrue(source.closed)

    def test_automatic_demo_does_not_accept_external_inputs(self):
        self.app.auto_input = True
        self.app.start()
        self.send()
        self.app.update()
        self.assertEqual(self.app.engine.claps, 0)

    def test_later_keyboard_input_does_not_rewrite_earlier_external_onset(self):
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
        self.assertEqual([row["source"] for row in claps], ["joycon", "keyboard"])
        self.assertAlmostEqual(claps[0]["error_ms"], 90)
        self.assertAlmostEqual(claps[1]["error_ms"], 110)


if __name__ == "__main__":
    unittest.main()


class RealClapSocketAppTests(unittest.TestCase):
    """不替换 socket 来源，验证控制器确实创建并监听本地 socket。"""

    def test_app_opens_socket_and_accepts_clap(self):
        self.enterContext(patch("rhythm_game.app.AudioEngine", FakeAudio))
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (root / "music.wav").write_bytes(b"fixture")
        config = write_config(root, CONFIG_TEXT + '\n[input]\nenabled = true\nsocket_path = "clap.sock"\n')
        app = RhythmApp(config)
        self.addCleanup(app.close)
        for _ in range(200):
            if app.clap_input.status in ("waiting", "error"):
                break
            time.sleep(.01)
        self.assertEqual(app.clap_input.status, "waiting")
        app.start()
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(client.close)
        client.connect(str(app.clap_input.path))
        for _ in range(200):
            if app.clap_input.status == "connected":
                break
            time.sleep(.01)
        self.assertEqual(app.clap_input.status, "connected")
        client.sendall((json.dumps({"v": 1, "type": "clap", "host_time": time.perf_counter(),
                                    "source": "joycon", "button": 1}) + "\n").encode())
        for _ in range(200):
            if app.engine.claps:
                break
            time.sleep(.01)
        self.assertEqual(app.engine.claps, 1)
