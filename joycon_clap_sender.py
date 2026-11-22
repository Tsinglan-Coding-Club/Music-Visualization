#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Joy-Con A 键 → 游戏拍掌信号发送器。

它只做三件事：
    1. 用 pygame 读取 Joy-Con 的 A 键（get_button(1)）；
    2. 用按下沿检测保证“按一次只发一次、长按不重复”；
    3. 通过本地 Unix socket 把带时间戳的 JSON 行发给游戏。

游戏侧见 rhythm_game/clap_socket.py。发送器和游戏必须同机运行，
因为 host_time 使用两个进程共享的 perf_counter 时钟。

用法：
    .venv/bin/python experiments/joycon_clap_sender.py
    .venv/bin/python experiments/joycon_clap_sender.py --list
    .venv/bin/python experiments/joycon_clap_sender.py --button 1
    .venv/bin/python experiments/joycon_clap_sender.py --socket /tmp/joycon-clap.sock

先配对 Joy-Con（系统设置 → 蓝牙，按住 SYNC 3 秒），再运行本脚本。
游戏没启动时脚本会每秒重试连接；游戏启动后自动接上。
"""

import argparse
import json
from pathlib import Path
import socket
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOCKET = PROJECT_ROOT / "tmp" / "joycon-clap.sock"
DEFAULT_BUTTON = 1          # SDL 对 Switch 手柄的映射：0=B, 1=A
DEFAULT_RELEASE_MS = 40.0
POLL_SECONDS = 0.002        # 500Hz；实际发现延迟仍受手柄 HID 报告率限制

# 复用上一个实验里的状态机，保证“按住只算一次”的规则只有一份实现。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from experiments.joycon_press_edge import PressEdgeDetector  # noqa: E402


def resolve_socket(value):
    path = Path(value).expanduser() if value else DEFAULT_SOCKET
    return path if path.is_absolute() else (PROJECT_ROOT / path)


def connect(path):
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(1.0)
    try:
        connection.connect(str(path))
    except OSError:
        connection.close()
        raise
    return connection


def send(connection, message):
    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    connection.sendall(payload + b"\n")


def hello(device, button):
    return {"v": 1, "type": "hello", "host_time": time.perf_counter(),
            "source": "joycon", "device": device, "button": button}


def clap(host_time, button):
    return {"v": 1, "type": "clap", "host_time": host_time,
            "source": "joycon", "button": button}


def run_hardware(device_index, button, release_ms, socket_path, list_only=False):
    import pygame                  # 延迟导入，--help 不需要 SDL

    pygame.init()
    pygame.joystick.init()
    count = pygame.joystick.get_count()
    print(f"检测到 {count} 个手柄设备")
    for i in range(count):
        joystick = pygame.joystick.Joystick(i)
        joystick.init()
        print(f"  [{i}] {joystick.get_name()}  按键 {joystick.get_numbuttons()}")
    if list_only:
        return 0
    if count == 0:
        print("\n没有检测到手柄：先在系统设置 → 蓝牙配对 Joy-Con，再重新运行本脚本。")
        return 1
    if not 0 <= device_index < count:
        print(f"\n设备编号 {device_index} 超出范围（0～{count - 1}）")
        return 1

    joystick = pygame.joystick.Joystick(device_index)
    joystick.init()
    if not 0 <= button < joystick.get_numbuttons():
        print(f"\n按键编号 {button} 超出范围（0～{joystick.get_numbuttons() - 1}）")
        return 1
    device = joystick.get_name()
    print(f"\n手柄 [{device_index}] {device}")
    print(f"按键 {button}（A 键为 1）；按住只发一次，松开 {release_ms:g} ms 后可再发")
    print(f"游戏 socket：{socket_path}")
    print("游戏没启动时会自动重试。Ctrl+C 退出。\n")

    detector = PressEdgeDetector(release_ms)
    connection = None
    next_attempt = 0.0
    waiting_printed = False
    presses = 0
    sent = 0
    started_at = time.perf_counter()

    try:
        while True:
            now = time.perf_counter()
            if connection is None and now >= next_attempt:
                try:
                    connection = connect(socket_path)
                except OSError:
                    if not waiting_printed:
                        print("等待游戏启动并监听 socket……", flush=True)
                        waiting_printed = True
                    next_attempt = time.perf_counter() + 1.0
                else:
                    waiting_printed = False
                    try:
                        send(connection, hello(device, button))
                    except OSError:
                        connection.close()
                        connection = None
                        next_attempt = time.perf_counter() + 1.0
                    else:
                        print("已连接游戏。\n", flush=True)

            try:
                pygame.event.pump()
                pressed = bool(joystick.get_button(button))
            except (pygame.error, SystemError, KeyError, IndexError) as exc:
                print(f"\n手柄读取失败，可能已断开：{exc}")
                return 1

            now = time.perf_counter()
            edge = detector.update(pressed, now)
            if edge is not None and edge.kind == "press" and edge.counted:
                presses += 1
                if connection is None:
                    print(f"[{now - started_at:7.3f}s] 拍掌 #{presses} 未发送：游戏未连接")
                else:
                    try:
                        send(connection, clap(edge.time, button))
                    except OSError as exc:
                        print(f"[{now - started_at:7.3f}s] 拍掌 #{presses} 发送失败：{exc}；重连")
                        connection.close()
                        connection = None
                        next_attempt = 0.0
                    else:
                        sent += 1
                        print(f"[{now - started_at:7.3f}s] 拍掌 #{presses} 已发送")

            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        duration = time.perf_counter() - started_at
        print(f"\n已退出，运行 {duration:.1f} 秒")
        print(f"按下 {presses} 次；成功发送 {sent} 次；未发送 {presses - sent} 次")
        return 0
    finally:
        if connection is not None:
            connection.close()
        pygame.quit()


def main(argv=None):
    parser = argparse.ArgumentParser(description="把 Joy-Con A 键的按下沿发送给节奏游戏")
    parser.add_argument("--list", action="store_true", help="列出手柄后退出")
    parser.add_argument("--device", type=int, default=0, help="手柄编号，默认 0")
    parser.add_argument("--button", type=int, default=DEFAULT_BUTTON, help="按键编号，默认 1（A）")
    parser.add_argument("--release-ms", type=float, default=DEFAULT_RELEASE_MS,
                        help=f"松开稳定窗口，默认 {DEFAULT_RELEASE_MS:g} ms")
    parser.add_argument("--socket", default=None,
                        help=f"游戏 socket 路径，默认 {DEFAULT_SOCKET}")
    args = parser.parse_args(argv)
    if not 0 <= args.release_ms <= 1000:
        parser.error("--release-ms 必须在 0～1000 之间")
    return run_hardware(args.device, args.button, args.release_ms,
                       resolve_socket(args.socket), args.list)


if __name__ == "__main__":
    sys.exit(main())
