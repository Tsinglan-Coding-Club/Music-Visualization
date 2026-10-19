"""Arduino 手套串口监测；仅接收数据，不发送指令或修改固件。"""

import argparse
from datetime import datetime
import json
import math
import os
from pathlib import Path
import sys
from time import monotonic, time

import serial
from serial.tools import list_ports

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rhythm_game.glove import ContactDebouncer, parse_line

SERIAL_ERRORS = (serial.SerialException, OSError)
if os.name == "posix":
    import termios
    SERIAL_ERRORS += (termios.error,)


def timing_report(rows):
    """比较两端间隔；不同步的时钟不能用于测量绝对单向延迟。"""
    segments = []
    samples = []
    previous_device = None
    elapsed_ms = 0

    def finish():
        if len(samples) < 2:
            return
        offsets = [host_ms - device_ms for host_ms, device_ms in samples]
        baseline = min(offsets)
        excess = sorted(value - baseline for value in offsets)
        steps = [b - a for a, b in zip(offsets, offsets[1:])]
        segments.append({
            "events": len(samples),
            "duration_seconds": round((samples[-1][0] - samples[0][0]) / 1000, 3),
            "relative_delay_range_ms": round(max(excess), 3),
            "relative_delay_p95_ms": round(excess[max(0, math.ceil(len(excess) * .95) - 1)], 3),
            "largest_interval_difference_ms": round(max(steps, key=abs), 3),
        })

    for row in rows:
        if row.get("type") in ("ready", "serial_error", "configuration"):
            finish()
            samples = []
            previous_device = None
            elapsed_ms = 0
        if (row.get("type") not in (None, "contact", "release")
                or "device_ms" not in row or "host_monotonic" not in row):
            continue
        device = row["device_ms"]
        if previous_device is not None:
            delta = (device - previous_device) % (1 << 32)
            if delta > (1 << 31):
                finish()
                samples = []
                elapsed_ms = 0
            else:
                elapsed_ms += delta
        samples.append((row["host_monotonic"] * 1000, elapsed_ms))
        previous_device = device
    finish()
    return segments


def monitor(port, baudrate, seconds, debounce_ms=30.0):
    log_dir = Path(__file__).resolve().parents[1] / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"glove-serial-{datetime.now():%Y%m%d-%H%M%S-%f}.jsonl"
    counts = {"ready": 0, "contact": 0, "release": 0, "invalid": 0}
    filtered_counts = {"clap": 0, "release": 0}
    debouncer = ContactDebouncer(debounce_ms)
    received_bytes = 0
    pending = bytearray()
    max_read_ms = 0.0
    max_loop_gap_ms = 0.0
    with log_path.open("w", encoding="utf-8") as log:
        def record(**row):
            log.write(json.dumps({"host_unix": time(), "host_monotonic": monotonic(), **row}, ensure_ascii=False) + "\n")
            log.flush()
        def emit(transitions):
            for transition in transitions:
                kind = "clap" if transition.contacting else "release"
                filtered_counts[kind] += 1
                record(type="debounced", transition=kind, onset_host=transition.onset_time,
                       confirmed_host=transition.confirmed_time, device_ms=transition.device_ms)
                print(f"  >>> 去抖确认：{'拍掌' if transition.contacting else '松开'}"
                      f"  等待 {(transition.confirmed_time - transition.onset_time) * 1000:.1f}ms", flush=True)
        record(type="configuration", port=port, baudrate=baudrate, framing="8N1", seconds=seconds,
               debounce_ms=debounce_ms)
        try:
            read_timeout = min(.01, max(.001, debounce_ms / 2000))
            with serial.Serial(port, baudrate, timeout=read_timeout, exclusive=True if os.name == "posix" else None) as connection:
                if os.name == "posix":
                    import fcntl
                    import termios
                    if hasattr(termios, "TIOCEXCL"):
                        fcntl.ioctl(connection.fileno(), termios.TIOCEXCL)
                print(f"监听 {port} · {baudrate} baud · 8N1 · {seconds:g} 秒", flush=True)
                print("打开端口可能使板子复位，请先松开手套。Ctrl+C 结束。", flush=True)
                print(f"原始记录：{log_path}", flush=True)
                print(f"接触与松开连续稳定 {debounce_ms:g}ms 才确认；READY 或稳定松开后可接收拍掌。", flush=True)
                start = monotonic()
                last_status = start
                previous_read_end = start
                while monotonic() - start < seconds:
                    read_start = monotonic()
                    loop_gap_ms = (read_start - previous_read_end) * 1000
                    chunk = connection.read(connection.in_waiting or 1)
                    received_at = monotonic()
                    read_ms = (received_at - read_start) * 1000
                    previous_read_end = received_at
                    max_read_ms = max(max_read_ms, read_ms)
                    max_loop_gap_ms = max(max_loop_gap_ms, loop_gap_ms)
                    received_unix = time()
                    if chunk:
                        record(type="chunk", host_monotonic=received_at, host_unix=received_unix,
                               raw_hex=chunk.hex(), read_ms=read_ms, loop_gap_ms=loop_gap_ms)
                    received_bytes += len(chunk)
                    pending.extend(chunk)
                    while b"\n" in pending:
                        raw, _, rest = pending.partition(b"\n")
                        pending = bytearray(rest)
                        raw = raw.rstrip(b"\r")
                        try:
                            line = raw.decode("ascii")
                        except UnicodeDecodeError:
                            line = repr(bytes(raw))
                        event = parse_line(line)
                        counts[event["type"]] += 1
                        record(line=line, host_monotonic=received_at, host_unix=received_unix, **event)
                        if event["type"] == "ready":
                            debouncer.reset(released=True)
                        elif event["type"] in ("contact", "release"):
                            emit(debouncer.feed(event["type"] == "contact", received_at, event["device_ms"]))
                        else:
                            debouncer.reset()
                            record(type="debounce_reset", reason="invalid_line")
                        label = {"ready": "就绪", "contact": "接触", "release": "离开", "invalid": "格式异常"}[event["type"]]
                        print(f"+{monotonic() - start:8.3f}s  {line}  [{label}]", flush=True)
                    if len(pending) > 4096:
                        counts["invalid"] += 1
                        record(type="invalid", partial_hex=pending.hex())
                        pending.clear()
                        debouncer.reset()
                    emit(debouncer.advance(monotonic()))
                    if monotonic() - last_status >= 15:
                        print(f"状态：收到 {received_bytes} 字节，原始 {counts}，去抖 {filtered_counts}", flush=True)
                        last_status = monotonic()
        except KeyboardInterrupt:
            print("监测已停止。", flush=True)
        except SERIAL_ERRORS as exc:
            record(type="serial_error", error=str(exc))
            raise
        finally:
            if pending:
                record(type="partial", partial_hex=pending.hex())
            record(type="summary", received_bytes=received_bytes, counts=counts, filtered_counts=filtered_counts,
                   max_read_ms=max_read_ms, max_loop_gap_ms=max_loop_gap_ms)
            print(f"汇总：{received_bytes} 字节，原始 {counts}，去抖 {filtered_counts}", flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description="接收 Arduino 手套的 READY、1,毫秒、0,毫秒，保存原始记录")
    parser.add_argument("--list", action="store_true", help="列出串口后退出")
    parser.add_argument("--analyze", type=Path, help="分析已有 JSONL 两端时间间隔，不打开串口")
    parser.add_argument("--port", help="例如 /dev/cu.usbserial-110 或 COM3")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--debounce-ms", type=float, default=30, help="接触和松开的稳定确认时间，默认 30 毫秒")
    args = parser.parse_args()
    if args.analyze:
        with args.analyze.open(encoding="utf-8") as source:
            report = timing_report(json.loads(line) for line in source if line.strip())
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print("相对延迟以同段最小接收偏移为零，包含时钟漂移；不是绝对输入延迟或接触到出声延迟。")
        return 0
    if args.list:
        for port in list_ports.comports():
            print(f"{port.device} | {port.description} | {port.hwid}")
        return 0
    if not args.port:
        parser.error("请用 --port 指定端口；--list 可查看可用串口")
    if not 0 < args.seconds <= 3600 or args.baud <= 0:
        parser.error("监测时间须在 0～3600 秒之间，波特率须大于 0")
    if not 0 <= args.debounce_ms <= 1000:
        parser.error("去抖时间须在 0～1000 毫秒之间")
    try:
        return monitor(args.port, args.baud, args.seconds, args.debounce_ms)
    except SERIAL_ERRORS as exc:
        print(f"无法监测串口：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
