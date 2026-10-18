"""游戏内设置与听音乐跟拍的首拍校准页。"""

import math
import textwrap

import arcade

from .app import BG, PANEL, TEXT, MUTED, GREEN, PURPLE, RED
from .calibration import BeatCalibrator


FIELDS = (
    ("beat_offset_ms", "拍点偏移", "正值后移，负值提前", 5),
    ("input_offset_ms", "输入补偿", "正值从输入时间扣除", 5),
    ("window_ms", "判定容差", "目标前后允许的误差", 5),
    ("blocksize", "音频缓冲", "0 表示自动选择", 64),
    ("music_volume", "音乐音量", "", 5),
    ("clap_volume", "拍手音量", "", 5),
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
        self.status = ""
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
        self.app.start(input_mode="calibration")
        self.app.record("calibration_start", bpm=self.app.config.bpm,
                        beat_offset_ms=offset, input_offset_ms=compensation)
        self._message("")

    def tap(self, host_time, music_time=None, play_sound=True, source="keyboard", settled=False):
        if not self.calibrating:
            return
        reading = self.app.engine.reading(host_time)
        if reading.generation != self.app.expected_generation or not reading.playing:
            return
        if play_sound:
            self.app.engine.trigger_clap()
        position = reading.position if music_time is None else music_time
        if not settled and self.app.queue_calibration_input(host_time, position, source):
            return
        accepted = self.calibrator.add(position)
        self.app.record("calibration_tap", source=source, host_time=host_time, music_time=position,
                        accepted=accepted)
        estimate = self.calibrator.estimate()
        self._message("")
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
                        spread_ms=estimate.spread_ms, ready=estimate.ready,
                        average_bpm=estimate.average_bpm, tempo_intervals=estimate.tempo_intervals)
        self._message("" if estimate.ready else estimate.message if estimate.used >= 8
                      else f"有效拍数不足（{estimate.used} / 8）", GREEN if estimate.ready else RED)

    def use_estimate(self):
        if self.calibrator is None or self.calibrating:
            return
        estimate = self.calibrator.estimate()
        if estimate.ready:
            self.values["beat_offset_ms"] = f"{estimate.offset_ms:g}"
            self._message("已采用，保存后生效", GREEN)

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
        w._text("settings-title", "设置", 40, 28, 32)
        self._button("return", "返回", (1136, 30, 104, 44), w.close_settings)
        w._card((40, 116, 560, 614))
        w._text("settings-manual-title", "参数", 64, 137, 24)
        for index, (name, title, hint, step) in enumerate(FIELDS):
            y = 174 + index * 78
            focused = index == self.focus
            w._text(("settings-field-title", name), title, 64, y + 12, 19)
            w._card((350, y, 144, 44), (28, 36, 51), 7, PURPLE if focused else None)
            value = self.values[name]
            w._text(("settings-value", name), value or "—", 364, y + 11, 20,
                    PURPLE if focused and self.select_all else TEXT, max_width=117)
            unit = "%" if name.endswith("volume") else "帧" if name == "blocksize" else "ms"
            w._text(("settings-unit", name), unit, 552, y + 14, 13, MUTED)
            if focused and not self.select_all:
                w._line(482, y + 10, 482, y + 34, PURPLE)
            self._button((name, "minus"), "−", (302, y, 36, 44),
                         lambda i=index: self._step(i, -1), enabled=not self.calibrating)
            self._button((name, "plus"), "+", (506, y, 36, 44),
                         lambda i=index: self._step(i, 1), enabled=not self.calibrating)
        try:
            first = self.app.config.beat_start_seconds + self._number("beat_offset_ms") / 1000
            preview = f"首拍 {first:.3f}s"
        except ValueError:
            preview = "首拍 —"
        w._text("settings-first-preview", preview, 64, 668, 16, GREEN)
        glove = self.app.glove
        if glove is not None:
            statuses = {"ready": "手套已就绪", "connecting": "手套连接中", "waiting_release": "请松开手套",
                        "disconnected": "手套未连接", "error": "手套连接失败", "delayed": "手套信号延迟"}
            w._text("settings-glove", statuses.get(glove.status, "手套未连接"), 64, 634, 14,
                    GREEN if glove.status == "ready" else MUTED)
            self._button("glove-reconnect", "重连", (474, 628, 86, 34), self.app.reconnect_glove)
        if self.focus is not None:
            hint = FIELDS[self.focus][2]
            if hint:
                w._text("settings-field-help", hint, 64, 704, 13, MUTED)
        elif glove is not None and glove.status in ("disconnected", "error", "delayed"):
            w._text("settings-glove-help", "重新插拔 USB 或点击重连", 64, 704, 13, MUTED)

        w._card((624, 116, 616, 614))
        w._text("calibration-title", "首拍校准", 648, 137, 24)
        instruction = "听音乐，每拍拍手一次（空格备用，至少 8 拍）" if glove is not None else "听音乐，每拍按一次空格（至少 8 拍）"
        w._text("calibration-instruction", instruction, 648, 184, 16, MUTED)
        self._button("start", "重新校准" if self.calibrator else "开始校准",
                     (648, 232, 168, 43), self.start_calibration, primary=True)
        self._button("finish", "完成", (832, 232, 104, 43), self.finish_calibration, enabled=self.calibrating)
        estimate = self.calibrator.estimate() if self.calibrator is not None else None
        w._card((648, 308, 568, 224), (28, 36, 51), 12)
        heading = "采样中" if self.calibrating else "校准结果" if estimate else "等待校准"
        w._text("calibration-heading", heading, 668, 330, 22, PURPLE if self.calibrating else GREEN)
        if estimate:
            w._text("calibration-count", f"有效 {estimate.used} 拍", 668, 371, 15, MUTED)
        configured_bpm = self.app.config.bpm
        if estimate is not None and estimate.average_bpm is not None:
            bpm_text = f"平均 {estimate.average_bpm:.1f} BPM · 设定 {configured_bpm:g} · 差 {estimate.average_bpm - configured_bpm:+.1f}"
        else:
            bpm_text = f"平均 — BPM · 设定 {configured_bpm:g}"
        w._text("calibration-bpm", bpm_text, 668, 410, 20, GREEN, max_width=528)
        result = (f"偏移 {estimate.offset_ms:+.2f} ms · 首拍 {estimate.first_beat_seconds:.3f}s"
                  if estimate and estimate.ready else "首拍 —")
        w._text("calibration-result", result, 668, 465, 17, TEXT, max_width=528)
        self._button("use", "使用结果", (648, 560, 168, 43), self.use_estimate,
                     enabled=estimate is not None and estimate.ready and not self.calibrating)
        for index, line in enumerate(textwrap.wrap(self.status, width=32)[:2]):
            w._text(("calibration-status", index), line, 648, 636 + index * 23, 14,
                    self.status_color, max_width=568)
        self._button("save", "保存", (40, 747, 120, 44), self.save, primary=True, enabled=not self.calibrating)
