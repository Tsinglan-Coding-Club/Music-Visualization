"""本地 socket 拍掌协议的软件测试；不依赖 Joy-Con 或 pygame。"""

import json
from pathlib import Path
import shutil
import socket
import tempfile
import time
from types import SimpleNamespace
import unittest

from rhythm_game.audio import ClockReading
from rhythm_game.clap_socket import ClapSocketInput
from rhythm_game.input_bridge import InputBridge


class ClapSocketInputTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.received = []
        self.records = []
        self.config = SimpleNamespace(source=self.root / "config.toml",
                                      input_socket_path="clap.sock",
                                      input_max_age_ms=250)
        self.input = ClapSocketInput(self.config, self.received.append, self.record)
        self.input.start()
        self.addCleanup(self.input.close)
        self.assertTrue(self.wait_for(lambda: self.input.status == "waiting"))

    def record(self, event, **fields):
        self.records.append((event, fields))

    @staticmethod
    def wait_for(predicate, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(.01)
        return predicate()

    def connect(self):
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(1.0)
        client.connect(str(self.input.path))
        self.addCleanup(client.close)
        self.assertTrue(self.wait_for(lambda: self.input.status == "connected"))
        return client

    @staticmethod
    def data(**fields):
        return (json.dumps(fields) + "\n").encode("utf-8")

    def clap(self, client, host_time=None):
        client.sendall(self.data(v=1, type="clap", source="joycon", button=1,
                                 host_time=time.perf_counter() if host_time is None else host_time))

    def test_clap_message_becomes_contact_transition(self):
        client = self.connect()
        host_time = time.perf_counter()
        self.clap(client, host_time)
        self.assertTrue(self.wait_for(lambda: len(self.received) == 1))
        transition = self.received[0]
        self.assertTrue(transition.contacting)
        self.assertAlmostEqual(transition.onset_time, host_time)
        self.assertGreaterEqual(transition.confirmed_time, transition.onset_time)
        self.assertIsNone(transition.device_ms)
        messages = [fields for event, fields in self.records if event == "input_message"]
        self.assertEqual(messages[0]["source"], "joycon")
        self.assertEqual(messages[0]["button"], 1)
        self.assertGreaterEqual(messages[0]["lag_ms"], 0)

    def test_hello_updates_connection_detail(self):
        client = self.connect()
        client.sendall(self.data(v=1, type="hello", source="joycon", device="Joy-Con (L/R)",
                                 host_time=time.perf_counter()))
        self.assertTrue(self.wait_for(lambda: "Joy-Con (L/R)" in self.input.detail))
        self.assertEqual(self.input.status, "connected")

    def test_invalid_and_unknown_messages_do_not_emit_clap(self):
        client = self.connect()
        client.sendall(b"not json\n")
        client.sendall(self.data(v=1, type="clap"))
        client.sendall(self.data(v=1, type="clap", host_time="soon"))
        client.sendall(self.data(v=1, type="dance", host_time=time.perf_counter()))
        self.assertTrue(self.wait_for(lambda: any(event == "input_ignored" for event, _ in self.records)))
        time.sleep(.05)
        self.assertEqual(self.received, [])
        events = [event for event, _ in self.records]
        self.assertIn("input_invalid", events)
        self.assertIn("input_ignored", events)

    def test_late_message_is_dropped_without_transition(self):
        client = self.connect()
        self.clap(client, time.perf_counter() - .5)
        self.assertTrue(self.wait_for(lambda: any(event == "input_late" for event, _ in self.records)))
        self.assertEqual(self.received, [])

    def test_negative_clock_is_adjusted_to_receive_time(self):
        client = self.connect()
        future = time.perf_counter() + .1
        self.clap(client, future)
        self.assertTrue(self.wait_for(lambda: len(self.received) == 1))
        self.assertLess(self.received[0].onset_time, future)
        self.assertTrue(any(event == "input_clock_adjust" for event, _ in self.records))

    def test_message_split_across_packets_and_multiple_lines(self):
        client = self.connect()
        line = self.data(v=1, type="clap", source="joycon", host_time=time.perf_counter())
        client.sendall(line[:10])
        client.sendall(line[10:])
        self.assertTrue(self.wait_for(lambda: len(self.received) == 1))
        first, second = time.perf_counter(), time.perf_counter()
        client.sendall(self.data(v=1, type="clap", source="joycon", host_time=first)
                      + self.data(v=1, type="clap", source="joycon", host_time=second))
        self.assertTrue(self.wait_for(lambda: len(self.received) == 3))

    def test_reconnect_after_sender_disconnect(self):
        first = self.connect()
        self.clap(first)
        self.assertTrue(self.wait_for(lambda: len(self.received) == 1))
        first.close()
        self.assertTrue(self.wait_for(lambda: self.input.status == "waiting"))
        second = self.connect()
        self.clap(second)
        self.assertTrue(self.wait_for(lambda: len(self.received) == 2))

    def test_socket_message_drives_real_input_bridge(self):
        bridge = InputBridge()
        engine = SimpleNamespace(duration=20, claps=0)
        engine.reading = lambda host: ClockReading(1, host - 100, True)
        engine.trigger_clap = lambda: setattr(engine, "claps", engine.claps + 1)
        config = SimpleNamespace(source=self.root / "config.toml",
                                 input_socket_path="bridge.sock",
                                 input_max_age_ms=250)
        sink = ClapSocketInput(config, bridge.receive, self.record)
        sink.start()
        self.addCleanup(sink.close)
        self.assertTrue(self.wait_for(lambda: sink.status == "waiting"))
        bridge.activate(engine, 1, "game", .1, .25, source="joycon")
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(1.0)
        client.connect(str(sink.path))
        self.addCleanup(client.close)
        host_time = time.perf_counter()
        self.clap(client, host_time)
        self.assertTrue(self.wait_for(lambda: engine.claps == 1))
        events = []
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and not events:
            events = bridge.drain()
            if not events:
                time.sleep(.01)
        self.assertEqual(len(events), 1)
        event = events[0][1]
        self.assertTrue(event.accepted)
        self.assertEqual(event.source, "joycon")
        self.assertAlmostEqual(event.music_time, host_time - 100, places=2)

    def test_close_stops_listener_and_removes_socket(self):
        self.connect()
        path = self.input.path
        self.input.close()
        self.assertFalse(path.exists())
        self.assertEqual(self.input.status, "stopped")

    def test_second_instance_cannot_bind_same_path(self):
        other = ClapSocketInput(self.config, self.received.append, self.record)
        self.addCleanup(other.close)
        other.start()
        self.assertTrue(self.wait_for(lambda: other.status == "error"))
        self.assertIn("占用", other.detail)


if __name__ == "__main__":
    unittest.main()
