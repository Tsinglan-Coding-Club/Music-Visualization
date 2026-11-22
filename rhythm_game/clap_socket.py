"""接收 Joy-Con 拍掌脚本的本地 socket 输入。

协议：Unix domain stream socket，一行一条 UTF-8 JSON。
    {"v":1,"type":"hello","host_time":123.4,"source":"joycon","device":"..."}
    {"v":1,"type":"clap","host_time":123.4,"source":"joycon","button":1}

游戏侧只把 type="clap" 转成 ContactTransition，交给 InputBridge：
声音在 socket 线程里立即触发，判定仍由主线程下一帧结算。
"""

import json
import math
import os
from pathlib import Path
import socket
import threading
from time import perf_counter

from .input_bridge import ContactTransition


MAX_LINE_BYTES = 1024
MAX_BUFFER_BYTES = 4096
ACCEPT_TIMEOUT = .5
CLIENT_TIMEOUT = .5
# 发送端与游戏必须同机运行；超过这个差值就认为时间戳不可信，改用接收时间。
CLOCK_SKEW_SECONDS = .25


class ClapSocketInput:
    """单个监听线程；回调只交付输入和诊断，不操作界面或文件。"""

    def __init__(self, config, on_transition, on_record):
        self.config = config
        self.on_transition = on_transition
        self.on_record = on_record
        path = Path(config.input_socket_path)
        self.path = path if path.is_absolute() else config.source.parent / path
        self.max_age = config.input_max_age_ms / 1000
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._listener = None
        self._connection = None
        self._bound_path = None
        self._state = ("stopped", "拍掌输入尚未启动")

    @property
    def status(self):
        with self._lock:
            return self._state[0]

    @property
    def detail(self):
        with self._lock:
            return self._state[1]

    @property
    def socket_path(self):
        return self.path

    def _set_status(self, status, detail):
        with self._lock:
            state = (status, detail)
            if state == self._state:
                return
            self._state = state
        self.on_record("input_status", status=status, detail=detail, socket=str(self.path))

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._set_status("starting", "拍掌输入启动中")
            self._thread = threading.Thread(target=self._run, name="clap-socket", daemon=True)
            self._thread.start()

    def restart(self):
        """设置页的“重连”：关掉监听并重新 bind，已连接脚本会被断开。"""
        self.close()
        self.start()

    def close(self):
        self._stop.set()
        with self._lock:
            listener, connection, thread = self._listener, self._connection, self._thread
            self._listener = None
            self._connection = None
        for item in (connection, listener):
            if item is None:
                continue
            try:
                item.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                item.close()
            except OSError:
                pass
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._unlink_socket()
        self._set_status("stopped", "拍掌输入已关闭")

    def _check_path(self):
        if len(os.fsencode(self.path)) > 100:
            raise ValueError(f"socket 路径过长（上限约 100 字节）：{self.path}")

    def _make_listener(self):
        self._check_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            # 先探一下是否有游戏实例正在监听；连不上就是上次崩溃留下的死文件。
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            probe.settimeout(.2)
            try:
                probe.connect(str(self.path))
            except OSError:
                self.path.unlink(missing_ok=True)
            else:
                raise OSError(f"{self.path} 已被另一个游戏实例占用")
            finally:
                probe.close()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(self.path))
            os.chmod(self.path, 0o600)
            listener.listen(4)
            listener.settimeout(ACCEPT_TIMEOUT)
        except Exception:
            listener.close()
            raise
        with self._lock:
            self._listener = listener
            self._bound_path = self.path
        return listener

    def _run(self):
        try:
            listener = self._make_listener()
            self._set_status("waiting", f"等待 Joy-Con 脚本连接：{self.path}")
            while not self._stop.is_set():
                try:
                    connection, _ = listener.accept()
                except socket.timeout:
                    continue
                except OSError as exc:
                    if self._stop.is_set():
                        break
                    raise exc
                with connection:
                    with self._lock:
                        self._connection = connection
                    self._set_status("connected", "Joy-Con 脚本已连接")
                    self.on_record("input_connected", socket=str(self.path))
                    try:
                        self._serve(connection)
                    except OSError as exc:
                        if not self._stop.is_set():
                            self.on_record("input_error", detail=str(exc))
                    finally:
                        with self._lock:
                            self._connection = None
                        self.on_record("input_disconnected", socket=str(self.path))
                    if not self._stop.is_set():
                        self._set_status("waiting", "Joy-Con 脚本已断开；等待重新连接")
        except Exception as exc:
            if not self._stop.is_set():
                self._set_status("error", f"拍掌输入监听失败：{exc}")
        finally:
            with self._lock:
                listener, self._listener = self._listener, None
            if listener is not None:
                try:
                    listener.close()
                except OSError:
                    pass
            self._unlink_socket()

    def _unlink_socket(self):
        with self._lock:
            path, self._bound_path = self._bound_path, None
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def _serve(self, connection):
        connection.settimeout(CLIENT_TIMEOUT)
        buffer = bytearray()
        while not self._stop.is_set():
            try:
                chunk = connection.recv(4096)
            except socket.timeout:
                continue
            if not chunk:
                break
            buffer.extend(chunk)
            if len(buffer) > MAX_BUFFER_BYTES:
                self.on_record("input_invalid", line=bytes(buffer[:80]).decode("utf-8", "replace"),
                               error="缓冲超过上限，已丢弃")
                buffer.clear()
                continue
            while True:
                newline = buffer.find(b"\n")
                if newline < 0:
                    break
                line = bytes(buffer[:newline]).strip()
                del buffer[:newline + 1]
                if line:
                    self._handle_line(line)

    def _handle_line(self, line):
        if len(line) > MAX_LINE_BYTES:
            self.on_record("input_invalid", line=line[:80].decode("utf-8", "replace"),
                           error="单行超过上限")
            return
        try:
            message = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.on_record("input_invalid", line=line[:80].decode("utf-8", "replace"),
                           error=str(exc))
            return
        if not isinstance(message, dict):
            self.on_record("input_invalid", line=line[:80].decode("utf-8", "replace"),
                           error="消息必须是 JSON 对象")
            return
        version = message.get("v", 1)
        if isinstance(version, bool) or version != 1:
            self.on_record("input_ignored", reason="unsupported_version", version=version)
            return
        kind = message.get("type")
        if kind == "hello":
            device = message.get("device")
            source = message.get("source", "joycon")
            detail = f"Joy-Con 脚本已连接：{device}" if isinstance(device, str) and device else "Joy-Con 脚本已连接"
            self._set_status("connected", detail)
            self.on_record("input_hello", device=device, source=source,
                           host_time=message.get("host_time"))
            return
        if kind != "clap":
            self.on_record("input_ignored", reason="unknown_type", message_type=kind)
            return

        host_time = message.get("host_time")
        if (isinstance(host_time, bool) or not isinstance(host_time, (int, float))
                or not math.isfinite(host_time)):
            self.on_record("input_invalid", line=line[:80].decode("utf-8", "replace"),
                           error="host_time 必须是有限数字")
            return
        source = message.get("source", "joycon")
        if not isinstance(source, str) or not source.strip() or len(source) > 32:
            self.on_record("input_invalid", line=line[:80].decode("utf-8", "replace"),
                           error="source 必须是短字符串")
            return
        button = message.get("button")
        if button is not None and (isinstance(button, bool) or not isinstance(button, int)):
            self.on_record("input_invalid", line=line[:80].decode("utf-8", "replace"),
                           error="button 必须是整数")
            return

        now = perf_counter()
        lag = now - host_time
        if lag < 0:
            # 发送端时钟略快时，未来时间会让 engine.reading 找不到锚点。
            self.on_record("input_clock_adjust", lag_ms=lag * 1000)
            host_time, lag = now, 0.0
        elif lag > self.max_age:
            self.on_record("input_late", lag_ms=lag * 1000, source=source, button=button)
            return

        self.on_record("input_message", message_type="clap", host_time=host_time,
                       lag_ms=lag * 1000, source=source, button=button)
        self.on_transition(ContactTransition(True, host_time, now, None))
