"""游戏内设置与听音乐跟拍的首拍校准页。"""

import math
import textwrap

import arcade

from .app import BG, PANEL, TEXT, MUTED, GREEN, PURPLE, RED
from .calibration import BeatCalibrator


FIELDS = (
    ("beat_offset_ms", "全曲拍点偏移", "ms · 正值后移，负值提前", 5),
    ("input_offset_ms", "输入延迟补偿", "ms · 正值从输入时间扣除", 5),
    ("window_ms", "判定容差", "± ms · 目标前后允许误差", 5),
    ("blocksize", "音频缓冲", "帧 · 0 由设备自动选择", 64),
    ("music_volume", "音乐音量", "% · 保存应用后生效", 5),
    ("clap_volume", "拍手音量", "% · 保存应用后生效", 5),
)


class SettingsPage:
    def __init__(self, window):
        self.window = window
        self.app = window.controller
        self.original_text = self.app.config_path.read_text(encoding="utf-8")
        self.values = {}
        for name, *_ in FIELDS:
            value = getattr(self.app.config, name)
            if name.endswith("volume"):
                value *= 100
            self.values[name] = f"{value:g}"
        self.focus = None
        self.select_all = False
        self.calibrator = None
        self.calibrating = False
        self.status = "点击数值输入，或用 − / + 微调；修改后保存应用。"
        self.status_color = MUTED
        self._buttons = []

    def _number(self, name):
        try:
            value = float(self.values[name])
        except ValueError as exc:
            raise ValueError("数值尚未填写完整") from exc
        if not math.isfinite(value):
            raise ValueError("请输入有限数字")
        return value

    def updates(self):
        values = {name: self._number(name) for name, *_ in FIELDS}
        for name in ("music_volume", "clap_volume"):
            values[name] /= 100
        return values

    def _message(self, text, color=MUTED):
        self.status, self.status_color = str(text), color

    def _edited(self, name):
        if name in ("input_offset_ms", "beat_offset_ms") and self.calibrator is not None:
            self.calibrator = None
            self._message("校准依据已改变，请重新采样。", PURPLE)

    def _step(self, index, direction):
        name, _, _, step = FIELDS[index]
        try:
            value = self._number(name) + step * direction
            limits = {"input_offset_ms": (-1000, 1000), "window_ms": (1, 1000),
                      "blocksize": (0, 8192), "music_volume": (0, 100), "clap_volume": (0, 100)}
            if name in limits:
                lower, upper = limits[name]
                value = max(lower, min(upper, value))
            self.values[name] = f"{round(value, 3):g}"
            self._edited(name)
            self.focus, self.select_all = index, True
        except ValueError as exc:
            self._message(exc, RED)

    def start_calibration(self):
        try:
            offset, compensation = self._number("beat_offset_ms"), self._number("input_offset_ms")
            if not -1000 <= compensation <= 1000:
                raise ValueError("输入延迟补偿应在 −1000 到 1000 ms 之间")
            if self.app.config.beat_start_seconds + offset / 1000 >= self.app.engine.duration:
                raise ValueError("首拍超出音乐时长，请先调整全曲偏移")
            calibrator = BeatCalibrator(self.app.config.bpm, self.app.config.beat_start_seconds,
                                        offset, compensation)
        except ValueError as exc:
            self._message(exc, RED)
            return
        self.calibrator = calibrator
        self.focus = None
        self.calibrating = True
        self.app.start()
        self.app.record("calibration_start", bpm=self.app.config.bpm,
                        beat_offset_ms=offset, input_offset_ms=compensation)
        self._message("先听前奏；从第一拍起，每拍按一次空格。", PURPLE)

    def tap(self, host_time):
        if not self.calibrating:
            return
        reading = self.app.engine.reading(host_time)
        if reading.generation != self.app.expected_generation or not reading.playing:
            return
        self.app.engine.trigger_clap()
        accepted = self.calibrator.add(reading.position)
        self.app.record("calibration_tap", host_time=host_time, music_time=reading.position,
                        accepted=accepted)
        estimate = self.calibrator.estimate()
        self._message(estimate.message, GREEN if estimate.ready else PURPLE)
        if estimate.count >= 16 and estimate.ready or estimate.count >= 32:
            self.finish_calibration()

    def finish_calibration(self):
        if not self.calibrating:
            return
        self.calibrating = False
        self.app.stop()
        estimate = self.calibrator.estimate()
        self.app.record("calibration_result", count=estimate.count, used=estimate.used,
                        rejected=estimate.rejected, beat_offset_ms=estimate.offset_ms,
                        spread_ms=estimate.spread_ms, ready=estimate.ready)
        self._message(estimate.message if estimate.used >= 8 else "有效拍数不足，请重新校准并至少稳定拍 8 拍。",
                      GREEN if estimate.ready else RED)

    def use_estimate(self):
        if self.calibrator is None or self.calibrating:
            return
        estimate = self.calibrator.estimate()
        if estimate.ready:
            self.values["beat_offset_ms"] = f"{estimate.offset_ms:g}"
            self._message(f"建议 {estimate.offset_ms:+.2f} ms 已加入左侧，保存应用后生效。", GREEN)

    def save(self):
        if self.calibrating:
            return
        try:
            values = self.updates()
        except ValueError as exc:
            self._message(exc, RED)
            return
        if self.app.apply_settings(values, self.original_text):
            self.window.close_settings()
        else:
            self._message(self.app.feedback[1], RED)

    def update(self):
        if self.calibrating and not self.app.desired_playing and not self.app.paused:
            self.finish_calibration()

    def key_press(self, symbol, modifiers, host_time):
        if symbol == arcade.key.ESCAPE:
            self.window.close_settings()
        elif symbol == arcade.key.SPACE:
            self.tap(host_time)
        elif symbol == arcade.key.TAB and not self.calibrating:
            direction = -1 if modifiers & arcade.key.MOD_SHIFT else 1
            self.focus = (self.focus + direction) % len(FIELDS) if self.focus is not None else 0
            self.select_all = True
        elif symbol == arcade.key.ENTER:
            if self.calibrating:
                self.finish_calibration()
            else:
                self.focus = None
        elif self.focus is not None and not self.calibrating:
            name = FIELDS[self.focus][0]
            if symbol in (arcade.key.BACKSPACE, arcade.key.DELETE):
                self.values[name] = "" if self.select_all or symbol == arcade.key.DELETE else self.values[name][:-1]
                self._edited(name)
                self.select_all = False
            elif symbol in (arcade.key.UP, arcade.key.DOWN):
                self._step(self.focus, 1 if symbol == arcade.key.UP else -1)
            elif symbol == arcade.key.A and modifiers & (arcade.key.MOD_CTRL | getattr(arcade.key, "MOD_COMMAND", 0)):
                self.select_all = True

    def text(self, value):
        if self.focus is None or self.calibrating:
            return
        value = "".join(character for character in value if character in "0123456789.-+")
        if not value:
            return
        name = FIELDS[self.focus][0]
        old = "" if self.select_all else self.values[name]
        self.values[name] = (old + value)[:14]
        self._edited(name)
        self.select_all = False

    def mouse_press(self, x, y):
        for rect, action, enabled in reversed(self._buttons):
            left, top, width, height = rect
            if left <= x <= left + width and top <= y <= top + height:
                if enabled:
                    action()
                return
        if not self.calibrating:
            for index in range(len(FIELDS)):
                if 350 <= x <= 494 and 174 + index * 78 <= y <= 218 + index * 78:
                    self.focus, self.select_all = index, True
                    return
        self.focus = None

    def _button(self, key, label, rect, action, primary=False, enabled=True):
        self.window._card(rect, GREEN if primary and enabled else (35, 44, 62), 9)
        self.window._text(("settings-button", key), label, rect[0] + 15, rect[1] + 12,
                          17, BG if primary and enabled else TEXT if enabled else MUTED)
        self._buttons.append((rect, action, enabled))

    def draw(self):
        w = self.window
        self._buttons.clear()
        w._text("settings-title", "设置与首拍校准", 40, 28, 32)
        w._text("settings-subtitle", "调整拍点、输入补偿与声音", 40, 75, 15, MUTED)
        self._button("return", "返回游戏", (1090, 30, 150, 44), w.close_settings)
        w._text("settings-return-hint", "未保存修改会放弃", 1090, 82, 12, MUTED)
        w._card((40, 116, 560, 614))
        w._text("settings-manual-title", "手动设置", 64, 137, 24)
        for index, (name, title, hint, step) in enumerate(FIELDS):
            y = 174 + index * 78
            focused = index == self.focus
            w._text(("settings-field-title", name), title, 64, y + 1, 19)
            w._text(("settings-field-hint", name), hint, 64, y + 29, 12, MUTED)
            w._card((350, y, 144, 44), (28, 36, 51), 7, PURPLE if focused else None)
            value = self.values[name]
            w._text(("settings-value", name), value or "输入数值", 364, y + 11, 20,
                    PURPLE if focused and self.select_all else TEXT, max_width=117)
            if focused and not self.select_all:
                w._line(482, y + 10, 482, y + 34, PURPLE)
            self._button((name, "minus"), "−", (302, y, 36, 44),
                         lambda i=index: self._step(i, -1), enabled=not self.calibrating)
            self._button((name, "plus"), "+", (506, y, 36, 44),
                         lambda i=index: self._step(i, 1), enabled=not self.calibrating)
        try:
            first = self.app.config.beat_start_seconds + self._number("beat_offset_ms") / 1000
            preview = f"首拍预览 {first:.3f}s  ·  {self.app.config.bpm:g} BPM"
        except ValueError:
            preview = "首拍预览：请填写有效数字"
        w._text("settings-first-preview", preview, 64, 668, 16, GREEN)
        w._text("settings-buffer-hint", "缓冲修改会重开音频；输入补偿不会消除实际发声延迟。", 64, 704, 12, MUTED)

        w._card((624, 116, 616, 614))
        w._text("calibration-title", "听音乐，跟拍校准", 648, 137, 24)
        for index, line in enumerate(("播放音乐后，从听到的第一拍起，", "每个四分音符拍一次空格，至少稳定拍 8 拍。",
                                      "稳定满 16 拍自动结束，Enter 可提前完成。")):
            w._text(("calibration-instruction", index), line, 648, 184 + index * 27, 16, MUTED)
        self._button("start", "播放 / 重新校准", (648, 274, 200, 43), self.start_calibration, primary=True)
        self._button("finish", "完成采样", (864, 274, 160, 43), self.finish_calibration, enabled=self.calibrating)
        estimate = self.calibrator.estimate() if self.calibrator is not None else None
        w._card((648, 338, 568, 168), (28, 36, 51), 12)
        heading = "正在听音乐采样…" if self.calibrating else "校准结果预览" if estimate else "等待开始校准"
        w._text("calibration-heading", heading, 668, 354, 22, PURPLE if self.calibrating else GREEN)
        numbers = f"采集 {estimate.count} 拍 · 稳定 {estimate.used} 拍 · 忽略 {estimate.rejected} 次" if estimate else "跟拍只用于估计拍点偏移，不参与游戏成绩"
        w._text("calibration-count", numbers, 668, 395, 15, MUTED)
        result = f"建议偏移 {estimate.offset_ms:+.2f} ms · 首拍 {estimate.first_beat_seconds:.3f}s" if estimate and estimate.count else "首拍位置与输入补偿将分开保存"
        w._text("calibration-result", result, 668, 432, 17, TEXT, max_width=528)
        spread = f"稳定拍的 90% 离散范围：±{estimate.spread_ms:.1f} ms" if estimate and estimate.count else "听到音乐后按空格；不需要跟随屏幕拍点"
        w._text("calibration-spread", spread, 668, 471, 14, MUTED)
        for index, line in enumerate(textwrap.wrap(self.status, width=32)[:2]):
            w._text(("calibration-status", index), line, 648, 527 + index * 24, 15, self.status_color, max_width=568)
        self._button("use", "使用建议偏移（待保存）", (648, 591, 280, 43), self.use_estimate,
                     enabled=estimate is not None and estimate.ready and not self.calibrating)
        for index, line in enumerate(("只微调当前首拍附近的相位；整拍或小节错位请手动调整。",
                                      "校准使用左侧输入补偿；结果包含个人跟拍习惯。",
                                      f"不自动识别硬件延迟，也不改变 {self.app.config.bpm:g} BPM 的固定速度。")):
            w._text(("calibration-scope", index), line, 648, 653 + index * 23, 13, MUTED, max_width=568)
        self._button("save", "保存并应用", (40, 747, 190, 45), self.save, primary=True, enabled=not self.calibrating)
        w._text("settings-footer", "Tab 切换数值 · ↑↓ 微调 · Esc 放弃修改返回 · 保存后音乐停止", 254, 760, 14, MUTED)
