"""白底 demo 设置：曲目、可编辑节奏与两种跟拍校准。"""

import math
from pathlib import Path
import re
import textwrap

import arcade

from .app import BG, PANEL, LINE, TEXT, MUTED, GREEN, PURPLE, RED
from .calibration import BeatCalibrator, TempoCalibrator
from .music_library import discover_music


# name, title, unit, step; 无步进的字段直接输入。
FIELDS = (
    ("bpm", "速度", "BPM", 1),
    ("beats_per_bar", "每小节拍数", "四分拍", 1),
    ("note_values", "节奏时值", "分母", None),
    ("beat_start_seconds", "初始拍位置", "秒", .1),
    ("beat_offset_ms", "拍点微调", "ms", 5),
    ("lead_in_bars", "预备小节数", "小节", 1),
    ("input_offset_ms", "输入补偿", "ms", 5),
    ("window_ms", "判定容差", "ms", 5),
    ("blocksize", "音频缓冲", "帧", 64),
    ("music_volume", "音乐音量", "%", 5),
    ("clap_volume", "拍手音量", "%", 5),
)
FIELD_MAP = {field[0]: field for field in FIELDS}
TAB_FIELDS = {"music": tuple(field[0] for field in FIELDS[:6]),
              "audio": tuple(field[0] for field in FIELDS[6:]), "calibration": ()}


class SettingsPage:
    def __init__(self, window):
        self.window = window
        self.app = window.controller
        self.original_text = self.app.config_path.read_text(encoding="utf-8")
        self.values = {}
        for name, *_ in FIELDS:
            value = getattr(self.app.config, name)
            if name == "note_values":
                self.values[name] = ", ".join(str(n) for n in value)
            else:
                if name.endswith("volume"):
                    value *= 100
                self.values[name] = f"{value:g}"
        self.values["music_path"] = str(self.app.config.music_path)
        self.tab = "music"
        self.focus = None
        self.select_all = False
        self.calibrator = None
        self.calibration_kind = "tempo"
        self.calibrating = False
        self.previewed = False
        self.status = ""
        self.status_color = MUTED
        self._buttons = []
        self._field_rects = {}
        self.music_choices = ()
        self.choosing_music = False
        self.music_page = 0

    def _number(self, name):
        try:
            value = float(self.values[name])
        except ValueError as exc:
            raise ValueError(f"{FIELD_MAP[name][1]}尚未填写完整") from exc
        if not math.isfinite(value):
            raise ValueError("请输入有限数字")
        return value

    def updates(self):
        values = {name: self._number(name) for name, *_ in FIELDS if name != "note_values"}
        for name in ("beats_per_bar", "lead_in_bars", "blocksize"):
            if not values[name].is_integer():
                raise ValueError(f"{FIELD_MAP[name][1]}必须为整数")
            values[name] = int(values[name])
        raw = self.values["note_values"].strip()
        if not re.fullmatch(r"\d+(?:[\s,，]+\d+)*", raw):
            raise ValueError("节奏时值请输入用逗号或空格分隔的音符分母，例如 4, 8, 8, 4, 4")
        values["note_values"] = [int(n) for n in re.split(r"[\s,，]+", raw)]
        values["music_path"] = self.values["music_path"]
        for name in ("music_volume", "clap_volume"):
            values[name] /= 100
        return values

    def _message(self, text, color=MUTED):
        self.status, self.status_color = str(text), color

    def _edited(self, name):
        if name in ("music_path", "bpm", "beat_start_seconds", "input_offset_ms", "beat_offset_ms"):
            self.calibrator = None
        self._message("")

    def _step(self, name, direction):
        try:
            step = FIELD_MAP[name][3]
            value = self._number(name) + step * direction
            limits = {"bpm": (1, 600), "beats_per_bar": (1, 32), "beat_start_seconds": (0, math.inf),
                      "lead_in_bars": (0, 1), "input_offset_ms": (-1000, 1000), "window_ms": (1, 1000),
                      "blocksize": (0, 8192), "music_volume": (0, 100), "clap_volume": (0, 100)}
            if name in limits:
                lower, upper = limits[name]
                value = max(lower, min(upper, value))
            self.values[name] = f"{round(value, 3):g}"
            self._edited(name)
            self.focus, self.select_all = name, True
        except ValueError as exc:
            self._message(exc, RED)

    def switch_tab(self, tab):
        if self.calibrating:
            return
        self.tab, self.focus = tab, None
        self.choosing_music = False

    def choose_music(self):
        self.music_choices = discover_music(self.app.config_path, self.values["music_path"])
        self.choosing_music = True
        self.music_page = 0
        self.focus = None

    def select_music(self, path):
        self.values["music_path"] = str(path)
        self.choosing_music = False
        self._edited("music_path")
        self._message("已选择曲目；请检查或校准它的 BPM 和初始拍位置。")

    def select_calibration(self, kind):
        if not self.calibrating and kind != self.calibration_kind:
            self.calibration_kind, self.calibrator = kind, None
            self._message("")

    def start_calibration(self):
        try:
            values = self.updates()
            calibrator = (TempoCalibrator() if self.calibration_kind == "tempo" else
                          BeatCalibrator(values["bpm"], values["beat_start_seconds"],
                                         values["beat_offset_ms"], values["input_offset_ms"]))
            if not self.app.preview_settings(values, self.original_text, tempo_only=self.calibration_kind == "tempo"):
                self._message(self.app.feedback[1], RED)
                return
        except ValueError as exc:
            self._message(exc, RED)
            return
        self.previewed = True
        self.calibrator = calibrator
        self.focus = None
        self.calibrating = True
        self.app.start(input_mode="calibration")
        self.app.record("calibration_start", kind=self.calibration_kind, music_path=values["music_path"],
                        bpm=values["bpm"], beat_offset_ms=values["beat_offset_ms"],
                        input_offset_ms=values["input_offset_ms"])
        self._message("")

    def tap(self, host_time, music_time=None, play_sound=True, source="keyboard", settled=False):
        if not self.calibrating:
            return
        reading = self.app.engine.reading(host_time)
        if not settled and (reading.generation != self.app.expected_generation or not reading.playing):
            return
        if play_sound:
            self.app.engine.trigger_clap()
        position = reading.position if music_time is None else music_time
        if not settled and self.app.queue_calibration_input(host_time, position, source):
            return
        self._sample(host_time, position, source)

    def _sample(self, host_time, position, source, auto_finish=True):
        accepted = self.calibrator.add(position)
        self.app.record("calibration_tap", kind=self.calibration_kind, source=source,
                        host_time=host_time, music_time=position, accepted=accepted)
        estimate = self.calibrator.estimate()
        if auto_finish and (estimate.count >= 16 and estimate.ready or estimate.count >= 32):
            self.finish_calibration()

    def finish_calibration(self):
        if not self.calibrating:
            return
        for event in self.app.take_calibration_inputs():
            self._sample(event.host_time, event.music_time, event.source, auto_finish=False)
        self.calibrating = False
        self.app.stop()
        estimate = self.calibrator.estimate()
        self.app.record("calibration_result", kind=self.calibration_kind, **vars(estimate))
        self._message("请核对结果，再采用并保存。" if estimate.ready else estimate.message,
                      GREEN if estimate.ready else RED)

    def use_estimate(self):
        if self.calibrator is None or self.calibrating:
            return
        estimate = self.calibrator.estimate()
        if estimate.ready:
            if self.calibration_kind == "tempo":
                self.values["bpm"] = f"{estimate.average_bpm:.3f}".rstrip("0").rstrip(".")
                self._message("已采用 BPM；接下来可切换到初始拍校准。保存后生效。", GREEN)
            else:
                self.values["beat_offset_ms"] = f"{estimate.offset_ms:g}"
                self._message("已采用初始拍校准结果，保存后生效。", GREEN)

    def save(self):
        if self.calibrating:
            return
        try:
            values = self.updates()
        except ValueError as exc:
            self._message(exc, RED)
            return
        if self.app.apply_settings(values, self.original_text):
            self.window.close_settings(saved=True)
        else:
            self._message(self.app.feedback[1], RED)

    def leave(self, saved=False):
        if self.calibrating:
            self.calibrating = False
            self.app.stop()
            self.app.record("calibration_cancel", kind=self.calibration_kind)
        if self.previewed and not saved and not self.app.reload():
            self._message(self.app.feedback[1], RED)
            return False
        return True

    def update(self):
        if self.calibrating and not self.app.desired_playing and not self.app.paused:
            self.finish_calibration()

    def key_press(self, symbol, modifiers, host_time):
        if symbol == arcade.key.ESCAPE:
            if self.choosing_music:
                self.choosing_music = False
            else:
                self.window.close_settings()
        elif self.choosing_music:
            return
        elif symbol == arcade.key.SPACE and self.focus != "music_path":
            self.tap(host_time)
        elif symbol == arcade.key.TAB and not self.calibrating:
            names = (("music_path",) if self.tab == "music" else ()) + TAB_FIELDS[self.tab]
            if names:
                direction = -1 if modifiers & arcade.key.MOD_SHIFT else 1
                index = names.index(self.focus) if self.focus in names else -1
                self.focus = names[(index + direction) % len(names)]
                self.select_all = True
        elif symbol in (arcade.key.ENTER, arcade.key.NUM_ENTER):
            if self.calibrating:
                self.finish_calibration()
            else:
                self.focus = None
        elif self.focus is not None and not self.calibrating:
            name = self.focus
            if symbol in (arcade.key.BACKSPACE, arcade.key.DELETE):
                self.values[name] = "" if self.select_all or symbol == arcade.key.DELETE else self.values[name][:-1]
                self._edited(name)
                self.select_all = False
            elif symbol in (arcade.key.UP, arcade.key.DOWN) and name in FIELD_MAP and FIELD_MAP[name][3]:
                self._step(name, 1 if symbol == arcade.key.UP else -1)
            elif symbol == arcade.key.A and modifiers & (arcade.key.MOD_CTRL | getattr(arcade.key, "MOD_COMMAND", 0)):
                self.select_all = True

    def text(self, value):
        if self.focus is None or self.calibrating or self.choosing_music:
            return
        name = self.focus
        if name == "music_path":
            value = "".join(c for c in value if c.isprintable())
        else:
            allowed = "0123456789,， " if name == "note_values" else "0123456789.-+"
            value = "".join(c for c in value if c in allowed)
        if not value:
            return
        old = "" if self.select_all else self.values[name]
        self.values[name] = (old + value)[:4096 if name == "music_path" else 512 if name == "note_values" else 16]
        self._edited(name)
        self.select_all = False

    def mouse_press(self, x, y):
        for rect, action, enabled in reversed(self._buttons):
            left, top, width, height = rect
            if left <= x <= left + width and top <= y <= top + height:
                if enabled:
                    action()
                return
        if self.choosing_music:
            return
        if not self.calibrating:
            for name, (left, top, width, height) in self._field_rects.items():
                if left <= x <= left + width and top <= y <= top + height:
                    self.focus, self.select_all = name, True
                    return
        self.focus = None

    def _button(self, key, label, rect, action, primary=False, enabled=True):
        self.window._card(rect, PANEL if enabled else BG, border=TEXT if primary and enabled else LINE)
        self.window._text(("settings-button", key), label, rect[0] + 12, rect[1] + 11,
                          17, TEXT if enabled else MUTED, max_width=rect[2] - 20)
        self._buttons.append((rect, action, enabled))

    def _field(self, name, x, y, width=500):
        w = self.window
        title, unit, step = FIELD_MAP[name][1:]
        w._text(("field-title", name), title, x, y, 18)
        rect = (x, y + 29, width - 112, 42)
        self._field_rects[name] = rect
        w._card(rect, BG, border=PURPLE if self.focus == name else LINE)
        w._text(("field-value", name), self.values[name] or "—", x + 10, y + 39, 19,
                PURPLE if self.focus == name and self.select_all else TEXT, max_width=rect[2] - 20)
        if step:
            self._button((name, "minus"), "−", (x + width - 102, y + 29, 36, 42),
                         lambda: self._step(name, -1), enabled=not self.calibrating)
            self._button((name, "plus"), "+", (x + width - 58, y + 29, 36, 42),
                         lambda: self._step(name, 1), enabled=not self.calibrating)
        w._text(("field-unit", name), unit, x + width - 12, y + 42, 13, MUTED)

    def _draw_music(self):
        w = self.window
        w._text("music-label", "音乐文件", 48, 153, 18)
        rect = (48, 184, 1012, 42)
        self._field_rects["music_path"] = rect
        w._card(rect, BG, border=PURPLE if self.focus == "music_path" else LINE)
        w._text("music-path", self.values["music_path"] or "输入文件路径", 60, 195, 17,
                PURPLE if self.focus == "music_path" and self.select_all else TEXT, max_width=986)
        self._button("music-select", "选择音乐", (1080, 184, 152, 42), self.choose_music)
        w._text("music-hint", "列表读取 music/ 目录；也可直接输入完整路径。音乐按原速播放，BPM 用于拍点与判定。",
                48, 240, 15, MUTED)
        for index, name in enumerate(TAB_FIELDS["music"]):
            self._field(name, 48 + (index % 2) * 606, 295 + (index // 2) * 112, 532)
        w._text("rhythm-help", "时值示例：4, 8, 8, 4, 4；4 为四分音符，8 为八分音符。总时值须填满一小节。",
                48, 650, 15, MUTED)

    def _draw_audio(self):
        w = self.window
        for index, name in enumerate(TAB_FIELDS["audio"]):
            self._field(name, 48 + (index % 2) * 606, 170 + (index // 2) * 112, 532)
        w._text("audio-help", "输入补偿：正值从拍掌时间扣除；判定容差：目标前后的允许误差；缓冲 0 表示自动。",
                48, 540, 15, MUTED)
        glove = self.app.glove
        if glove is not None:
            statuses = {"ready": "手套已就绪", "connecting": "手套连接中", "waiting_release": "请松开手套",
                        "disconnected": "手套未连接", "error": "手套连接失败", "delayed": "手套信号延迟"}
            w._text("settings-glove", statuses.get(glove.status, "手套未连接"), 48, 600, 17, MUTED)
            self._button("glove-reconnect", "重连", (240, 588, 86, 42), self.app.reconnect_glove)

    def _draw_calibration(self):
        w = self.window
        for index, (kind, title) in enumerate((("tempo", "速度校准"), ("phase", "初始拍校准"))):
            self._button(kind, title, (48 + index * 190, 162, 172, 42),
                         lambda k=kind: self.select_calibration(k), primary=self.calibration_kind == kind,
                         enabled=not self.calibrating)
        w._text("calibration-track", Path(self.values["music_path"]).name, 48, 228, 18, max_width=1184)
        summary = f'设定 {self.values["bpm"]} BPM · 初始拍 {self.values["beat_start_seconds"]} 秒 · 微调 {self.values["beat_offset_ms"]} ms'
        w._text("calibration-settings", summary, 48, 263, 17, MUTED, max_width=1184)
        instructions = ("听音乐，连续按每个四分音符拍手；至少 8 拍，稳定 16 拍自动结束。" if self.calibration_kind == "tempo"
                        else "先确定 BPM 和大致初始拍位置，再听音乐逐拍拍手；至少 8 拍。")
        w._text("calibration-instruction", instructions + " 空格可备用。", 48, 309, 18, max_width=1184)
        caveat = ("请自行核对半速／倍速；漏拍、多拍和个人跟拍偏差会影响结果。" if self.calibration_kind == "tempo"
                  else "此项微调当前起点附近 ±半拍；整拍／整小节偏差需先手动改初始拍位置。")
        w._text("calibration-caveat", caveat, 48, 344, 15, MUTED)
        self._button("start", "重新采样" if self.calibrator else "开始采样", (48, 391, 152, 42),
                     self.start_calibration, enabled=not self.calibrating)
        self._button("finish", "完成", (216, 391, 104, 42), self.finish_calibration, enabled=self.calibrating)
        self._button("use", "采用结果", (336, 391, 152, 42), self.use_estimate,
                     enabled=self.calibrator is not None and self.calibrator.estimate().ready and not self.calibrating)
        estimate = self.calibrator.estimate() if self.calibrator else None
        w._line(48, 464, 1232, 464, LINE)
        w._text("calibration-heading", "采样中" if self.calibrating else "采样结果", 48, 488, 21)
        if estimate:
            w._text("calibration-count", f"采集 {estimate.count} 拍 · 有效 {estimate.used} · 排除 {estimate.rejected}",
                    48, 530, 17, MUTED)
            bpm = f"{estimate.average_bpm:.2f}" if estimate.average_bpm is not None else "—"
            difference = (f" · 与设定差 {estimate.average_bpm - self._number('bpm'):+.2f}"
                          if estimate.average_bpm is not None else "")
            w._text("calibration-bpm", f"跟拍速度 {bpm} BPM" + difference, 48, 568, 24, max_width=1184)
            if self.calibration_kind == "phase":
                result = f"建议微调 {estimate.offset_ms:+.2f} ms · 首拍 {estimate.first_beat_seconds:.3f} 秒"
                w._text("calibration-result", result, 48, 612, 18)
        else:
            w._text("calibration-empty", "等待采样", 48, 540, 18, MUTED)

    def _draw_music_chooser(self):
        w = self.window
        self._buttons.clear()
        w._card((32, 135, 1216, 557), BG, border=TEXT)
        w._text("chooser-title", "选择音乐", 52, 153, 23)
        self._button("chooser-close", "关闭", (1126, 148, 100, 40), lambda: setattr(self, "choosing_music", False))
        start = self.music_page * 7
        for index, path in enumerate(self.music_choices[start:start + 7]):
            try:
                label = str(path.relative_to(self.app.config_path.resolve().parent))
            except ValueError:
                label = str(path)
            self._button(("song", index), label, (52, 207 + index * 55, 1174, 44), lambda p=path: self.select_music(p))
        if not self.music_choices:
            w._text("chooser-empty", "music/ 内没有可选音频。放入文件后重开列表，或直接输入音乐路径。", 52, 240, 18, MUTED)
        self._button("previous", "上一页", (52, 622, 104, 42),
                     lambda: setattr(self, "music_page", self.music_page - 1), enabled=self.music_page > 0)
        self._button("next", "下一页", (172, 622, 104, 42),
                     lambda: setattr(self, "music_page", self.music_page + 1), enabled=start + 7 < len(self.music_choices))
        w._text("chooser-page", f"{self.music_page + 1} / {max(1, (len(self.music_choices) + 6) // 7)}", 300, 635, 16, MUTED)

    def draw(self):
        w = self.window
        self._buttons.clear()
        self._field_rects.clear()
        w._text("settings-title", "Demo 设置", 48, 26, 26)
        self._button("return", "取消并返回", (1072, 24, 160, 42), w.close_settings, enabled=not self.calibrating)
        for index, (tab, title) in enumerate((("music", "音乐与节奏"), ("calibration", "校准"), ("audio", "声音与输入"))):
            self._button(("tab", tab), title, (48 + index * 180, 88, 164, 42),
                         lambda t=tab: self.switch_tab(t), primary=self.tab == tab, enabled=not self.calibrating)
        {"music": self._draw_music, "calibration": self._draw_calibration, "audio": self._draw_audio}[self.tab]()
        for index, line in enumerate(textwrap.wrap(self.status, width=68)[:2]):
            w._text(("settings-status", index), line, 48, 686 + index * 22, 15, self.status_color, max_width=1184)
        self._button("save", "保存并返回", (48, 744, 160, 42), self.save, primary=True, enabled=not self.calibrating)
        if self.choosing_music:
            self._draw_music_chooser()
