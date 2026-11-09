#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ↑ 用 python3 执行，文件为 UTF-8（中文注释在 macOS 上不乱码）

"""
Joy-Con 拍掌边沿实验：把“按下/松开两个状态”变成“按一次记一次，长按不重复”。

问题
----
test_joycon.py 打印的是每一帧的按键状态。如果主程序按“当前是否按着”来记拍掌，
按住 1 秒就会在 50Hz 轮询下记 50 次，松开还会再记一次。节奏游戏要的不是状态，
而是边沿：

    1. 只有“松开 → 按下”的那一瞬间，产生一次拍掌；
    2. 从按下到松开之间，按多久都不再产生拍掌（长按不重复）；
    3. 松开本身不产生拍掌，只负责让下一次按下可以再记；
    4. 松开后要稳定一小段时间（默认 40ms）才重新允许记账，避免手抖或按键抖动
       把一次按下拆成两次。

注意语义：这里“长按不算”指按住期间不重复计数。长按的第一下仍然算一次。
如果你要的是“按住超过 X 毫秒就整次作废”，那必须等松开才能决定，会牺牲发声时机，
是另一条规则，不在本实验里。

用法
----
    .venv/bin/python experiments/joycon_press_edge.py --selftest
    .venv/bin/python experiments/joycon_press_edge.py --list
    .venv/bin/python experiments/joycon_press_edge.py                  # 0 号手柄的 ZR(7)
    .venv/bin/python experiments/joycon_press_edge.py --button 5       # 改按 R
    .venv/bin/python experiments/joycon_press_edge.py --release-ms 60  # 改松开稳定窗口
    .venv/bin/python experiments/joycon_press_edge.py --device 1

退出：Ctrl+C，会打印统计（新逻辑记了几次、旧“看状态”逻辑会记几次）。
"""

import argparse
import sys
import time
from dataclasses import dataclass


DEFAULT_BUTTON = 7          # 组合模式下的 ZR；单只 Joy-Con 请先用 test_joycon.py 确认编号
DEFAULT_RELEASE_MS = 40.0   # 松开稳定窗口；40ms 约等于每秒最多 25 次按下
POLL_SECONDS = 0.005        # 200Hz 轮询，比 test_joycon.py 的 50Hz 时间分辨率更高

# 仅用于打印，纯展示；组合模式下索引可能与单只不同，以实机编号为准。
BUTTON_NAMES = {
    0: "B", 1: "A", 2: "Y", 3: "X",
    4: "L", 5: "R", 6: "ZL", 7: "ZR",
    8: "Minus", 9: "Plus",
    10: "左摇杆按下", 11: "右摇杆按下",
    12: "Home", 13: "Capture",
    14: "SL", 15: "SR",
}


@dataclass(frozen=True)
class Edge:
    """一次物理状态切换。counted 只对 press 有意义。"""

    kind: str                      # "press" 或 "release"
    time: float                    # 主机单调时间（perf_counter）
    counted: bool = False          # 这次按下是否记了一次拍掌
    hold_seconds: float | None = None   # 松开时回填本次按住的时长


class PressEdgeDetector:
    """把“每帧的按下状态”压成“每次按下只出一次”的边沿检测器。

    只依赖时间戳，不依赖 pygame，方便单独测试，也方便以后搬进主程序。
    调用方每帧调用 update(pressed, now)，只有状态切换时才返回 Edge。
    """

    def __init__(self, release_stable_ms=DEFAULT_RELEASE_MS, start_pressed=False):
        if isinstance(release_stable_ms, bool) or not 0 <= release_stable_ms <= 1000:
            raise ValueError("松开稳定窗口必须在 0～1000 毫秒之间")
        self.release_stable = release_stable_ms / 1000
        self._raw = None             # 上一帧看到的物理状态；None 表示还没采样
        self._armed = not start_pressed   # 是否允许下一次按下计数
        self._press_started = None   # 当前这一按的开始时间，用于统计按住时长
        self._release_started = None # 本次松开从何时开始，用于稳定窗口
        self._last_time = None
        self.last_hold = None        # 最近一次完整按住的时长

    def update(self, pressed, now):
        """pressed: 当前物理状态；now: perf_counter。返回 Edge 或 None。"""
        if not isinstance(pressed, bool):
            raise ValueError("pressed 必须是 bool")
        if self._last_time is not None and now < self._last_time:
            raise ValueError("时间必须单调递增")
        self._last_time = now

        # 第一次采样只建立基线。如果开机时就按着，必须先松开再按才算，
        # 否则“握住手柄”会被误记成一次拍掌。
        if self._raw is None:
            self._raw = pressed
            if pressed:
                self._armed = False
                self._press_started = now
            return None

        # 已经松开、但还没重新武装：等稳定窗口够了再允许下一次按下。
        # 这行放在比较状态之前，保证“松开一段时间后”能自然恢复。
        if not self._raw and not self._armed and self._release_started is not None:
            if now - self._release_started + 1e-9 >= self.release_stable:
                self._armed = True
                self._release_started = None

        if pressed == self._raw:
            return None              # 状态没变：按着或松着，都不产生事件

        self._raw = pressed

        if pressed:
            # 松开 → 按下：这是唯一可能记账的边沿。
            self._release_started = None
            self._press_started = now
            if not self._armed:
                # 还没稳定松开就重新按下（抖动/连按），这次不记。
                return Edge("press", now, counted=False)
            self._armed = False       # 记完立刻上锁，按住期间不会再次进入这里
            return Edge("press", now, counted=True)

        # 按下 → 松开：不记账，只回填按住时长并开始稳定窗口。
        hold = None if self._press_started is None else now - self._press_started
        self._press_started = None
        self.last_hold = hold
        self._release_started = now
        return Edge("release", now, hold_seconds=hold)


def run_sequence(events, release_ms=DEFAULT_RELEASE_MS):
    """把 [(pressed, dt_seconds), ...] 喂给检测器，返回 (detector, edges)。"""
    detector = PressEdgeDetector(release_ms)
    now = 1000.0
    edges = []
    for pressed, dt in events:
        now += dt
        edge = detector.update(pressed, now)
        if edge is not None:
            edges.append(edge)
    return detector, edges


def counted_presses(edges):
    return sum(1 for edge in edges if edge.kind == "press" and edge.counted)


def expect(label, got, want):
    if got != want:
        raise AssertionError(f"{label}：期望 {want}，实际 {got}")
    print(f"  PASS  {label} -> {got}")


def selftest():
    """不接硬件、不导入 pygame，纯逻辑自检。"""
    print("PressEdgeDetector 自检（不接硬件）")

    # 1) 长按约 2 秒：只在按下沿记 1 次，按住期间 400 帧都不再记。
    seq = ([(False, 0.005), (True, 0.005)]
           + [(True, 0.005)] * 400
           + [(False, 0.005)] + [(False, 0.005)] * 20)
    detector, edges = run_sequence(seq)
    expect("长按 2 秒只记 1 次", counted_presses(edges), 1)
    expect("按住时长被记录（>1.9s）", detector.last_hold > 1.9, True)
    print("        对照：旧“看状态”逻辑会在这 400 帧里记 400 次")

    # 2) 启动时已经按住：不算；先稳定松开，再按才算。
    seq = ([(True, 0.005)] + [(True, 0.005)] * 100
           + [(False, 0.005)] + [(False, 0.005)] * 20
           + [(True, 0.005), (False, 0.005)] + [(False, 0.005)] * 20)
    _, edges = run_sequence(seq)
    expect("启动时已按住不误记，松开后再按记 1 次", counted_presses(edges), 1)

    # 3) 两次正常点击，中间稳定松开：各记 1 次。
    seq = ([(False, 0.005), (True, 0.005), (False, 0.005)]
           + [(False, 0.005)] * 20
           + [(True, 0.005), (False, 0.005)]
           + [(False, 0.005)] * 20)
    _, edges = run_sequence(seq)
    expect("两次正常点击记 2 次", counted_presses(edges), 2)

    # 4) 抖动：按下后只松开 5ms 又按回去，不应变成第二次；稳定后再按才记。
    seq = ([(False, 0.005), (True, 0.005), (False, 0.005), (True, 0.005), (False, 0.005)]
           + [(False, 0.005)] * 20
           + [(True, 0.005), (False, 0.005)]
           + [(False, 0.005)] * 20)
    _, edges = run_sequence(seq)
    expect("5ms 抖动不重复计数，之后正常按再记 1 次", counted_presses(edges), 2)

    # 5) 时间倒退必须报错，避免把乱序样本当成新事件。
    detector = PressEdgeDetector()
    detector.update(False, 10.0)
    try:
        detector.update(True, 9.0)
    except ValueError:
        print("  PASS  时间倒退被拒绝")
    else:
        raise AssertionError("时间倒退没有被拒绝")

    print("\n自检全部通过。")
    return 0


def run_hardware(device_index, button, release_ms, list_only=False):
    import pygame                  # 延迟导入：--selftest 不需要 pygame，也不需要窗口

    pygame.init()                  # 与 test_joycon.py 相同；SDL 手柄枚举依赖这步
    pygame.joystick.init()

    count = pygame.joystick.get_count()
    print(f"检测到 {count} 个手柄设备")
    for i in range(count):
        joystick = pygame.joystick.Joystick(i)
        joystick.init()
        print(f"  [{i}] {joystick.get_name()}")
        print(f"      轴 {joystick.get_numaxes()}　按键 {joystick.get_numbuttons()}　"
              f"GUID {joystick.get_guid()}")
    if list_only:
        return 0
    if count == 0:
        print("\n没有检测到手柄，请检查：")
        print("  1) 系统设置 → 蓝牙，看 Joy-Con 是否显示为已连接")
        print("  2) 没配对：按住 SYNC 键 3 秒，灯快闪后点连接")
        print("  3) 连上后要重新运行脚本（SDL 只在启动时枚举一次设备）")
        return 1
    if not 0 <= device_index < count:
        print(f"\n设备编号 {device_index} 超出范围（0～{count - 1}）")
        return 1

    joystick = pygame.joystick.Joystick(device_index)
    joystick.init()
    buttons = joystick.get_numbuttons()
    if not 0 <= button < buttons:
        print(f"\n按键编号 {button} 超出范围（0～{buttons - 1}）")
        return 1

    name = BUTTON_NAMES.get(button, f"未知{button}")
    print(f"\n正在监控 [{device_index}] {joystick.get_name()}")
    print(f"按键 {button}（{name}）；按住只记一次，松开稳定 {release_ms:g} ms 后才接受下一次")
    print("按下、按住、松开看输出。Ctrl+C 退出\n")

    detector = PressEdgeDetector(release_ms)
    claps = 0
    naive_frames = 0               # 旧逻辑：每帧按着都算，用来做对照
    intervals = []
    holds = []
    last_clap = None
    started_at = time.perf_counter()

    try:
        while True:
            try:
                pygame.event.pump()                 # 让 SDL 读一次底层设备状态
                pressed = bool(joystick.get_button(button))
            except (pygame.error, SystemError, KeyError, IndexError) as exc:
                print(f"\n手柄读取失败，可能已断开：{exc}")
                return 1

            now = time.perf_counter()
            if pressed:
                naive_frames += 1
            edge = detector.update(pressed, now)

            if edge is not None:
                elapsed = now - started_at
                if edge.kind == "press":
                    if edge.counted:
                        claps += 1
                        if last_clap is not None:
                            intervals.append(now - last_clap)
                        last_clap = now
                        print(f"[{elapsed:7.3f}s] 拍掌 #{claps}   ← 记一次")
                    else:
                        print(f"[{elapsed:7.3f}s] 按下（未记：启动时已按住，或还没稳定松开）")
                else:
                    hold_ms = (edge.hold_seconds or 0) * 1000
                    holds.append(edge.hold_seconds or 0)
                    print(f"[{elapsed:7.3f}s] 松开（不记拍掌；本次按住 {hold_ms:.0f} ms）")

            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        duration = time.perf_counter() - started_at
        print(f"\n已退出，运行 {duration:.1f} 秒")
        print(f"新逻辑（按下沿）：记了 {claps} 次拍掌")
        print(f"旧逻辑（每帧按着都算）：会记 {naive_frames} 次")
        if intervals:
            print(f"两次拍掌间隔：最短 {min(intervals) * 1000:.0f} ms，"
                  f"最长 {max(intervals) * 1000:.0f} ms")
        if holds:
            print(f"最长一次按住 {max(holds):.3f} 秒；按住期间没有重复计数")
        return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Joy-Con 按下沿实验：按一次记一次，长按不重复")
    parser.add_argument("--selftest", action="store_true", help="只跑逻辑自检，不接手柄")
    parser.add_argument("--list", action="store_true", help="列出设备后退出")
    parser.add_argument("--device", type=int, default=0, help="手柄编号，默认 0")
    parser.add_argument("--button", type=int, default=DEFAULT_BUTTON,
                        help=f"按键编号，默认 {DEFAULT_BUTTON}（组合模式 ZR）")
    parser.add_argument("--release-ms", type=float, default=DEFAULT_RELEASE_MS,
                        help=f"松开稳定窗口，默认 {DEFAULT_RELEASE_MS:g} ms")
    args = parser.parse_args(argv)

    if not 0 <= args.release_ms <= 1000:
        parser.error("--release-ms 必须在 0～1000 之间")
    if args.selftest:
        return selftest()
    return run_hardware(args.device, args.button, args.release_ms, args.list)


if __name__ == "__main__":
    sys.exit(main())
