"""后台接收手套串口；回调只交付输入和诊断，不操作界面或文件。"""

import os
import threading
from time import perf_counter

import serial
from serial.tools import list_ports

from rhythm_game.glove import ContactDebouncer, parse_line


SERIAL_ERRORS = (serial.SerialException, OSError)
if os.name == "posix":
    import fcntl
    import termios
    SERIAL_ERRORS += (termios.error,)

READ_TIMEOUT = .005
RETRY_SECONDS = 1.0
MAX_LINE_BYTES = 256
MAX_READ_BYTES = 4096
MAX_RELATIVE_LAG = .250
RESET_PULSE_SECONDS = .050


class GloveInput:
    """单个读线程拥有串口；回调须快速、线程安全且不抛出异常。"""

    def __init__(self, config, on_transition, on_record):
        self.config = config
        self.on_transition = on_transition
        self.on_record = on_record
        self._filter = ContactDebouncer(config.glove_debounce_ms)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._reconnect = threading.Event()
        self._thread = None
        self._state = ("disconnected", "手套串口尚未连接", "")
        self._reset_protocol()

    @property
    def status(self):
        with self._lock:
            return self._state[0]

    @property
    def detail(self):
        with self._lock:
            return self._state[1]

    @property
    def port(self):
        with self._lock:
            return self._state[2]

    def _set_status(self, status, detail, port=None):
        with self._lock:
            state = (status, detail, self._state[2] if port is None else port)
            if state == self._state:
                return
            self._state = state
        self.on_record("glove_status", status=state[0], detail=state[1], port=state[2])

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._reconnect.clear()
            self._thread = threading.Thread(target=self._run, name="glove-input", daemon=True)
            self._thread.start()

    def close(self):
        self._stop.set()
        self._reconnect.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        if thread is None or not thread.is_alive():
            self._set_status("disconnected", "手套串口已关闭")

    def reconnect(self):
        self._reconnect.set()
        with self._lock:
            running = self._thread is not None and self._thread.is_alive()
        if not running:
            self.start()

    @staticmethod
    def _available_ports():
        return list(list_ports.comports())

    def _choose_port(self, available):
        configured = self.config.glove_port.strip()
        if configured:
            return configured
        devices = {p.device for p in available if p.vid is not None}
        # macOS 的 cu/tty 是同一设备的两个入口，优先主动连接用的 cu。
        devices = {p for p in devices if not (
            p.startswith("/dev/tty.") and "/dev/cu." + p[len("/dev/tty."):] in devices)}
        if not devices:
            self._set_status("disconnected", "未找到 USB 串口；请连接手套或填写端口", "")
            return None
        if len(devices) != 1:
            self._set_status("error", "检测到多个 USB 串口，请指定手套端口：" + "、".join(sorted(devices)), "")
            return None
        return devices.pop()

    @staticmethod
    def _claim_exclusive(connection):
        if os.name == "posix":
            fcntl.flock(connection.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            if hasattr(termios, "TIOCEXCL"):
                fcntl.ioctl(connection.fileno(), termios.TIOCEXCL)

    def _reset_protocol(self):
        self._filter.reset()
        self._pending = bytearray()
        self._discarding = False
        self._delayed = False
        self._previous_device = None
        self._device_elapsed = 0.0
        self._baseline = None

    def _reset_device(self, connection):
        """Nano 的自动复位使用 DTR 控制线；不发送串口数据。"""
        try:
            connection.dtr = False
            if self._stop.wait(RESET_PULSE_SECONDS):
                return False
            connection.dtr = True
        except SERIAL_ERRORS as exc:
            self.on_record("glove_notice", detail=f"无法切换 DTR 自动复位：{exc}；等待 READY 或稳定松开")
        return not self._stop.is_set()

    def _run(self):
        try:
            while not self._stop.is_set():
                self._reconnect.clear()
                connection = None
                try:
                    self._set_status("connecting", "正在连接手套串口")
                    available = self._available_ports()
                    port = self._choose_port(available)
                    if port is not None and not self._stop.is_set():
                        self._set_status("connecting", "正在打开手套串口", port)
                        connection = serial.Serial(
                            port=port, baudrate=self.config.glove_baudrate,
                            bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE,
                            stopbits=serial.STOPBITS_ONE, timeout=READ_TIMEOUT,
                            xonxoff=False, rtscts=False, dsrdtr=False,
                            exclusive=True if os.name == "posix" else None,
                        )
                        self._claim_exclusive(connection)
                        self._reset_protocol()
                        self._set_status("waiting_release", "请松开手套，等待 READY 或稳定松开")
                        if self._reset_device(connection):
                            self._read(connection, port, any(p.device == port for p in available))
                except SERIAL_ERRORS as exc:
                    self._reset_protocol()
                    self._set_status("disconnected", f"手套串口断开：{exc}；稍后重连")
                finally:
                    if connection is not None:
                        try:
                            connection.close()
                        except SERIAL_ERRORS:
                            pass
                if self._stop.is_set():
                    break
                self._reconnect.wait(RETRY_SECONDS)
        except Exception as exc:
            self._set_status("error", f"手套输入线程异常：{exc}")
        finally:
            if self._stop.is_set():
                self._set_status("disconnected", "手套串口已关闭")

    def _read(self, connection, port, check_presence):
        next_presence_check = perf_counter() + RETRY_SECONDS
        while not self._stop.is_set() and not self._reconnect.is_set():
            chunk = connection.read(min(MAX_READ_BYTES, connection.in_waiting or 1))
            received_at = perf_counter()
            self._consume(chunk, received_at)
            self._advance(received_at)
            if check_presence and received_at >= next_presence_check:
                if not any(p.device == port for p in self._available_ports()):
                    raise serial.SerialException("设备端口已消失")
                next_presence_check = received_at + RETRY_SECONDS

    def _invalidate(self):
        self._filter.reset()
        if not self._delayed:
            self._set_status("waiting_release", "串口格式异常；请稳定松开手套后重试")

    def _consume(self, chunk, received_at):
        for value in chunk:
            if value == 10:
                if not self._discarding:
                    raw = bytes(self._pending).rstrip(b"\r")
                    try:
                        line = raw.decode("ascii")
                    except UnicodeDecodeError:
                        line = repr(raw)
                    self._handle_line(line, received_at)
                self._pending.clear()
                self._discarding = False
            elif not self._discarding:
                if len(self._pending) >= MAX_LINE_BYTES:
                    self.on_record("glove_raw", line=repr(bytes(self._pending)),
                                   input_type="invalid", device_ms=None, host_time=received_at)
                    self._pending.clear()
                    self._discarding = True
                    self._invalidate()
                else:
                    self._pending.append(value)

    def _handle_line(self, line, received_at):
        event = parse_line(line)
        kind = event["type"]
        device_ms = event.get("device_ms")
        self.on_record("glove_raw", line=line, input_type=kind,
                       device_ms=device_ms, host_time=received_at)
        if kind == "ready":
            self._filter.reset(released=True)
            self._delayed = False
            self._previous_device = None
            self._device_elapsed = 0.0
            self._baseline = None
            self._set_status("ready", "手套已就绪")
        elif kind == "invalid":
            self._invalidate()
        elif not self._delayed and self._check_delay(device_ms, received_at):
            self._emit(self._filter.feed(kind == "contact", received_at, device_ms))

    def _check_delay(self, device_ms, received_at):
        if self._previous_device is not None:
            delta = (device_ms - self._previous_device) % (1 << 32)
            if delta > (1 << 31):
                self._mark_delayed("板端时间倒退；请重新连接或等待新的 READY")
                return False
            self._device_elapsed += delta / 1000
        self._previous_device = device_ms
        offset = received_at - self._device_elapsed
        self._baseline = offset if self._baseline is None else min(self._baseline, offset)
        if offset - self._baseline > MAX_RELATIVE_LAG:
            self._mark_delayed("串口相对延迟超过 250ms；请重新连接或等待新的 READY")
            return False
        return True

    def _mark_delayed(self, detail):
        self._delayed = True
        self._filter.reset()
        self._set_status("delayed", detail)

    def _advance(self, timestamp):
        if not self._delayed:
            self._emit(self._filter.advance(timestamp))
            if self.status == "waiting_release" and self._filter.stable_state is False:
                self._set_status("ready", "手套已稳定松开，可以拍掌")

    def _emit(self, transitions):
        for transition in transitions:
            self.on_transition(transition)
