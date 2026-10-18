"""假串口与可控时钟验证；不打开真实硬件端口。"""

import os
import queue
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import serial

from rhythm_game.glove_input import GloveInput, MAX_LINE_BYTES, SERIAL_ERRORS


def port(device="/dev/cu.test", vid=0x1A86):
    return SimpleNamespace(device=device, vid=vid)


class FakeSerial:
    def __init__(self, *chunks):
        self.chunks = queue.Queue()
        for chunk in chunks:
            self.chunks.put(chunk)
        self.closed = False
        self.read_sizes = []
        self.dtr_changes = []
        self.on_dtr = None

    @property
    def dtr(self):
        return self.dtr_changes[-1] if self.dtr_changes else True

    @dtr.setter
    def dtr(self, value):
        self.dtr_changes.append(value)
        if self.on_dtr is not None:
            self.on_dtr(value)

    @property
    def in_waiting(self):
        return 1

    def read(self, size):
        self.read_sizes.append(size)
        try:
            item = self.chunks.get(timeout=.005)
        except queue.Empty:
            return b""
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self):
        self.closed = True


class GloveInputTests(unittest.TestCase):
    def setUp(self):
        self.transitions = []
        self.records = []
        self.condition = threading.Condition()
        config = SimpleNamespace(glove_port="", glove_baudrate=115200, glove_debounce_ms=30)
        self.input = GloveInput(config, self.transition, self.record)
        self.addCleanup(self.input.close)

    def transition(self, transition):
        with self.condition:
            self.transitions.append(transition)
            self.condition.notify_all()

    def record(self, event_name, **fields):
        with self.condition:
            self.records.append((event_name, fields))
            self.condition.notify_all()

    def wait_for(self, predicate):
        with self.condition:
            self.assertTrue(self.condition.wait_for(predicate, timeout=2), self.records)

    def send(self, data, timestamp):
        self.input._consume(data, timestamp)
        self.input._advance(timestamp)

    def test_fragmented_ready_contact_release_and_idle_confirmation(self):
        self.send(b"REA", 1)
        self.send(b"DY\r\n1,100", 1.1)
        self.assertEqual(self.input.status, "ready")
        self.send(b"\r\n", 1.2)
        self.input._advance(1.229)
        self.assertEqual(self.transitions, [])
        self.input._advance(1.230)
        self.send(b"0,140\r\n", 1.240)
        self.input._advance(1.270)
        self.assertEqual([t.contacting for t in self.transitions], [True, False])
        self.assertEqual(self.transitions[0].onset_time, 1.2)
        self.assertEqual(self.transitions[0].device_ms, 100)
        raw = [fields for name, fields in self.records if name == "glove_raw"]
        self.assertEqual([r["input_type"] for r in raw], ["ready", "contact", "release"])

    def test_bounce_short_pulse_and_held_contact(self):
        self.send(b"READY\n1,0\n", 0)
        self.send(b"0,20\n", .020)
        self.input._advance(.100)
        self.assertEqual(self.transitions, [])
        self.send(b"1,110\n", .110)
        self.send(b"0,125\n", .125)
        self.send(b"1,135\n", .135)
        self.input._advance(.165)
        self.send(b"1,180\n", .180)
        self.input._advance(.300)
        self.assertEqual([t.contacting for t in self.transitions], [True])

    def test_initial_high_and_invalid_line_require_stable_low(self):
        self.input._set_status("waiting_release", "test")
        self.send(b"1,0\n", 0)
        self.input._advance(.030)
        self.assertEqual(self.transitions, [])
        self.assertEqual(self.input.status, "waiting_release")
        self.send(b"0,50\n", .050)
        self.input._advance(.080)
        self.assertEqual(self.input.status, "ready")
        self.send(b"bad\n1,100\n", .100)
        self.input._advance(.130)
        self.assertFalse(any(t.contacting for t in self.transitions))
        self.assertEqual(self.input.status, "waiting_release")
        self.send(b"0,150\n", .150)
        self.input._advance(.180)
        self.send(b"1,200\n", .200)
        self.input._advance(.230)
        self.assertEqual(sum(t.contacting for t in self.transitions), 1)

    def test_stable_low_arms_without_ready_or_a_synthetic_transition(self):
        self.input._set_status("waiting_release", "test")
        self.send(b"0,0\n", 0)
        self.input._advance(.029)
        self.assertEqual(self.input.status, "waiting_release")
        self.input._advance(.030)
        self.assertEqual(self.input.status, "ready")
        self.assertEqual(self.transitions, [])
        self.send(b"1,50\n", .050)
        self.input._advance(.080)
        self.assertEqual(len(self.transitions), 1)

    def test_invalid_ascii_and_overlong_line_are_bounded_and_discarded(self):
        self.send(b"READY\n1,0\n", 0)
        self.send(b"\xff\n", .010)
        self.input._advance(.040)
        self.assertEqual(self.transitions, [])
        self.send(b"x" * (MAX_LINE_BYTES + 10000), .050)
        self.assertLessEqual(len(self.input._pending), MAX_LINE_BYTES)
        self.send(b"READY\n", .060)  # 仍属于过长的一行，不能误当 READY。
        self.assertEqual(self.input.status, "waiting_release")
        self.send(b"READY\n1,100\n", .100)
        self.input._advance(.130)
        self.assertEqual(len(self.transitions), 1)
        self.assertTrue(all(len(r["line"]) < 1100 for n, r in self.records if n == "glove_raw"))

    def test_relative_delay_latches_and_ready_recovers(self):
        self.send(b"READY\n0,0\n", 10)
        self.send(b"1,100\n", 10.4)
        self.input._advance(10.5)
        self.assertEqual(self.input.status, "delayed")
        self.send(b"0,200\n1,300\n", 10.6)
        self.input._advance(11)
        self.assertEqual(self.transitions, [])
        self.send(b"READY\n1,0\n", 11.1)
        self.input._advance(11.130)
        self.assertEqual(self.input.status, "ready")
        self.assertEqual(len(self.transitions), 1)

    def test_uint32_rollover_and_backwards_jump(self):
        self.send(b"READY\n0,4294967280\n", 20)
        self.send(b"1,16\n", 20.032)
        self.input._advance(20.062)
        self.assertEqual(len(self.transitions), 1)
        self.send(b"0,15\n", 20.070)
        self.assertEqual(self.input.status, "delayed")

    def test_constant_unknown_offset_is_not_absolute_delay(self):
        self.send(b"READY\n0,900000\n", 500)
        self.send(b"1,900100\n", 500.1)
        self.input._advance(500.13)
        self.assertEqual(self.input.status, "ready")
        self.assertEqual(len(self.transitions), 1)

    def test_auto_port_selection_excludes_non_usb_and_tty_duplicate(self):
        self.assertEqual(self.input._choose_port([
            port("/dev/tty.test"), port("/dev/cu.test"), port("/dev/cu.bluetooth", None)
        ]), "/dev/cu.test")
        self.assertEqual(self.input._choose_port([port("COM3")]), "COM3")
        self.assertIsNone(self.input._choose_port([port("COM3"), port("COM4")]))
        self.assertEqual(self.input.status, "error")
        self.assertIn("COM3", self.input.detail)
        self.assertIsNone(self.input._choose_port([port("COM3", None)]))
        self.assertEqual(self.input.status, "disconnected")
        self.input.config.glove_port = "COM7"
        self.assertEqual(self.input._choose_port([]), "COM7")

    def run_with_fake_serial(self, factory):
        self.enterContext(patch("rhythm_game.glove_input.serial.Serial", side_effect=factory))
        self.enterContext(patch.object(self.input, "_available_ports", return_value=[port()]))
        self.enterContext(patch.object(self.input, "_claim_exclusive"))
        self.enterContext(patch("rhythm_game.glove_input.RETRY_SECONDS", .001))
        self.addCleanup(self.input.close)

    def test_background_timeout_confirmation_start_and_close_idempotent(self):
        connection = FakeSerial(b"READY\n1,10\n")
        opened = []

        def factory(**kwargs):
            opened.append(kwargs)
            return connection

        self.run_with_fake_serial(factory)
        self.input.start()
        self.input.start()
        self.wait_for(lambda: self.input.status == "ready")
        # 空读取推进稳定确认；无需后续串口边沿才能确认持续接触。
        self.wait_for(lambda: bool(self.transitions))
        self.input.close()
        self.input.close()
        self.assertTrue(connection.closed)
        self.assertFalse(self.input._thread.is_alive())
        self.assertEqual(len(opened), 1)
        self.assertLessEqual(opened[0]["timeout"], .005)
        self.assertEqual(opened[0]["baudrate"], 115200)
        self.assertEqual(opened[0]["bytesize"], serial.EIGHTBITS)
        self.assertFalse(opened[0]["xonxoff"])
        self.assertEqual(self.input.status, "disconnected")

    def test_dtr_reset_pulses_with_a_bounded_stop_aware_wait(self):
        connection = FakeSerial()
        with patch.object(self.input._stop, "wait", return_value=False) as wait:
            self.assertTrue(self.input._reset_device(connection))
        self.assertEqual(connection.dtr_changes, [False, True])
        wait.assert_called_once_with(.050)

    def test_stopping_during_dtr_pulse_closes_without_reading(self):
        connection = FakeSerial(b"READY\n")
        connection.on_dtr = lambda value: self.input._stop.set() if value is False else None
        self.run_with_fake_serial(lambda **kwargs: connection)
        self.input.start()
        self.wait_for(lambda: connection.closed)
        self.input.close()
        self.assertEqual(connection.dtr_changes, [False])
        self.assertEqual(connection.read_sizes, [])
        self.assertFalse(self.input._thread.is_alive())

    def test_unsupported_dtr_reports_notice_and_still_receives(self):
        connection = FakeSerial(b"READY\n")

        def unsupported(value):
            raise serial.SerialException("modem controls unsupported")

        connection.on_dtr = unsupported
        self.run_with_fake_serial(lambda **kwargs: connection)
        self.input.start()
        self.wait_for(lambda: self.input.status == "ready")
        self.assertTrue(any(n == "glove_notice" and "unsupported" in f["detail"]
                            for n, f in self.records))

    def test_open_error_read_error_and_automatic_reconnect(self):
        broken = FakeSerial(serial.SerialException("removed"))
        healthy = FakeSerial(b"READY\n")
        opens = []

        def factory(**kwargs):
            opens.append(kwargs)
            if len(opens) == 1:
                raise serial.SerialException("busy")
            return broken if len(opens) == 2 else healthy

        self.run_with_fake_serial(factory)
        self.input.start()
        self.wait_for(lambda: self.input.status == "ready" and len(opens) >= 3)
        self.assertTrue(broken.closed)
        details = [f["detail"] for n, f in self.records if n == "glove_status"]
        self.assertTrue(any("busy" in d for d in details))
        self.assertTrue(any("removed" in d for d in details))

    def test_explicit_reconnect_closes_old_connection_and_resets_arming(self):
        first = FakeSerial(b"READY\n")
        second = FakeSerial()
        connections = [first, second]
        opened = []

        def factory(**kwargs):
            opened.append(kwargs)
            return connections[len(opened) - 1]

        self.run_with_fake_serial(factory)
        self.input.start()
        self.wait_for(lambda: self.input.status == "ready")
        self.input.reconnect()
        self.wait_for(lambda: self.input.status == "waiting_release" and len(opened) == 2)
        self.assertTrue(first.closed)
        self.assertIsNone(self.input._filter.stable_state)

    def test_enumerated_device_disappearance_is_detected_without_data(self):
        connection = FakeSerial(b"")
        with patch("rhythm_game.glove_input.perf_counter", side_effect=[0, 2]), \
                patch.object(self.input, "_available_ports", return_value=[]):
            with self.assertRaisesRegex(serial.SerialException, "消失"):
                self.input._read(connection, "/dev/cu.test", True)

    @unittest.skipUnless(os.name == "posix", "POSIX termios only")
    def test_termios_errors_are_expected_serial_errors(self):
        import termios
        self.assertIsInstance(termios.error(6, "Device not configured"), SERIAL_ERRORS)

    @unittest.skipUnless(os.name == "posix", "POSIX exclusive access only")
    def test_posix_claim_requests_flock_and_tiocexcl(self):
        import fcntl
        import termios
        connection = SimpleNamespace(fileno=lambda: 123)
        with patch("rhythm_game.glove_input.fcntl.flock") as flock, \
                patch("rhythm_game.glove_input.fcntl.ioctl") as ioctl:
            self.input._claim_exclusive(connection)
        flock.assert_called_once_with(123, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if hasattr(termios, "TIOCEXCL"):
            ioctl.assert_called_once_with(123, termios.TIOCEXCL)


if __name__ == "__main__":
    unittest.main()
