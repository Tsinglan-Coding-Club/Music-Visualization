"""Arcade 窗口：五线谱节奏示意、单次拍掌落点与操作入口。

音频与判定由控制器负责；按键回调立即记录时间，不等待下一次画面更新。
所有绘制使用 Arcade 原生图元与复用的 Text，设计坐标为 1280 × 800。
"""

import math
from pathlib import Path
from time import perf_counter

import arcade

from .app import BG, PANEL, LINE, TEXT, MUTED, GREEN, PURPLE, RED
from .settings import SettingsPage


DESIGN_WIDTH = 1280
DESIGN_HEIGHT = 800
FONT_NAMES = ("PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", "Heiti SC", "Arial")


class RhythmWindow(arcade.Window):
    """承载 RhythmApp 控制器；run 返回本轮统计，close 可以重复调用。"""

    def __init__(self, controller, visible=True):
        self.controller = controller
        self._closed_once = False
        self._window_ready = False
        self._held_keys = set()
        self._texts = {}
        self._text_sources = {}
        self._buttons = []
        self._settings = None
        self._settings_button = (1128, 28, 104, 40)
        self._camera = None
        self._scale = 1.0
        self._offset_x = 0.0
        self._offset_y = 0.0
        self._run_started = False
        self._demo_seconds = None
        self._screenshot = None
        self._started_at = None
        self._next_flush = perf_counter() + 1
        try:
            super().__init__(
                controller.config.width,
                controller.config.height,
                "主音归来 · 节奏练习",
                resizable=True,
                visible=visible,
                update_rate=1 / 120,
                draw_rate=1 / 60,
                vsync=False,
                enable_polling=False,
            )
            self._window_ready = True
            self.background_color = BG
            self._camera = arcade.Camera2D(
                position=(0, 0),
                projection=arcade.LRBT(0, self.width, 0, self.height),
                window=self,
            )
            self._resize_layout(self.width, self.height)
            self._buttons = [
                ((64, 736, 120, 44), "播放", "start", True),
                ((196, 736, 120, 44), "暂停", "toggle_pause", False),
                ((328, 736, 120, 44), "重置", "reset_round", False),
            ]
        except Exception:
            self.close()
            raise

    def _resize_layout(self, width, height):
        width, height = max(width, 1), max(height, 1)
        self._scale = min(width / DESIGN_WIDTH, height / DESIGN_HEIGHT)
        self._offset_x = (width - DESIGN_WIDTH * self._scale) / 2
        self._offset_y = (height - DESIGN_HEIGHT * self._scale) / 2
        if self._camera is not None:
            self._camera.projection = arcade.LRBT(0, width, 0, height)
            # Arcade 默认 framebuffer 会自行乘 Retina 像素比例；这里用逻辑尺寸，
            # 否则视口被二次放大，标题和右侧内容会落在实际屏幕之外。
            self._camera.viewport = arcade.LBWH(0, 0, width, height)

    def on_resize(self, width, height):
        self._resize_layout(width, height)

    def _point(self, x, y):
        """设计左上坐标转为窗口左下坐标；留白不改变画面纵横比。"""
        return (
            self._offset_x + x * self._scale,
            self._offset_y + (DESIGN_HEIGHT - y) * self._scale,
        )

    def _line(self, x1, y1, x2, y2, color, width=1):
        arcade.draw_line(*self._point(x1, y1), *self._point(x2, y2), color, width * self._scale)

    def _circle(self, x, y, radius, color):
        arcade.draw_circle_filled(*self._point(x, y), radius * self._scale, color, num_segments=32)

    def _rounded_fill(self, rect, color, radius):
        x, y, width, height = rect
        radius = max(0, min(radius, width / 2, height / 2))
        left, bottom = self._point(x, y + height)
        scale = self._scale
        if radius == 0:
            arcade.draw_lbwh_rectangle_filled(left, bottom, width * scale, height * scale, color)
            return
        arcade.draw_lbwh_rectangle_filled(
            left + radius * scale, bottom, (width - 2 * radius) * scale, height * scale, color
        )
        arcade.draw_lbwh_rectangle_filled(
            left, bottom + radius * scale, width * scale, (height - 2 * radius) * scale, color
        )
        for cx in (x + radius, x + width - radius):
            for cy in (y + radius, y + height - radius):
                self._circle(cx, cy, radius, color)

    def _card(self, rect, color=PANEL, radius=0, border=LINE):
        if border is not None:
            self._rounded_fill(rect, border, radius)
            x, y, width, height = rect
            self._rounded_fill((x + 1, y + 1, width - 2, height - 2), color, max(radius - 1, 0))
        else:
            self._rounded_fill(rect, color, radius)

    def _text(self, key, value, x, y, size=20, color=TEXT, max_width=None, anchor_x="left"):
        """按稳定位置缓存标签，仅在内容、缩放或位置改变时更新。"""
        value = str(value)
        px, py = self._point(x, y)
        font_size = size * 0.75 * self._scale  # 设计字号为像素；Text 字号单位为点。
        label = self._texts.get(key)
        if label is None:
            label = arcade.Text(
                "", px, py, color,
                font_size=font_size,
                font_name=FONT_NAMES,
                anchor_x=anchor_x,
                anchor_y="top",
            )
            self._texts[key] = label
        label.x, label.y = px, py
        if label.font_size != font_size:
            label.font_size = font_size
        if label.color != color:
            label.color = color
        source = (value, font_size, max_width)
        if self._text_sources.get(key) != source:
            label.text = value
            if max_width is not None:
                truncated = value
                while truncated and label.content_width > max_width * self._scale:
                    truncated = truncated[:-1]
                    label.text = truncated + "…"
            self._text_sources[key] = source
        label.draw()

    def _staff(self, position, bar):
        """节奏教学图：五条谱线、固定音高与真实音符时值，不表示旋律检测。"""
        cfg = self.controller.config
        chart = self.controller.chart
        session = self.controller.session
        for staff_y in (233, 245, 257, 269, 281):
            self._line(210, staff_y, 1110, staff_y, (150, 150, 150), 1)
        self._line(210, 233, 210, 281, MUTED, 1.5)
        self._line(1110, 233, 1110, 281, MUTED, 1.5)
        self._text("signature-top", cfg.beats_per_bar, 172, 231, 24, TEXT, anchor_x="center")
        self._text("signature-bottom", "4", 172, 259, 24, TEXT, anchor_x="center")
        offsets = []
        elapsed = 0.0
        for value in cfg.note_values:
            offsets.append(elapsed)
            elapsed += 4 / value
        note_xs = [255 + offset / cfg.beats_per_bar * 820 for offset in offsets]
        entry_bar = chart.bar_at(session.entry_time) if session.entry_time is not None else None
        current_bar = entry_bar[0] if entry_bar else bar[0] if bar else 1
        stem_top, note_y = 219, 257
        for index, (denominator, offset, note_x) in enumerate(zip(cfg.note_values, offsets, note_xs)):
            target_index = (current_bar - 1) * len(cfg.note_values) + index
            target_time = chart.start + ((current_bar - 1) * cfg.beats_per_bar + offset) * chart.beat_seconds
            state = session.states.get(target_index)
            active = abs(position - target_time) <= cfg.window_ms / 1000
            color = GREEN if state == "hit" else RED if state == "miss" else PURPLE if active else TEXT
            if denominator >= 4:
                arcade.draw_ellipse_filled(*self._point(note_x, note_y), 16 * self._scale,
                                           11 * self._scale, color, tilt_angle=18, num_segments=32)
            else:
                arcade.draw_ellipse_outline(*self._point(note_x, note_y), 16 * self._scale,
                                            11 * self._scale, color, 2 * self._scale, tilt_angle=18,
                                            num_segments=32)
            stem_x = note_x + 7
            if denominator >= 2:
                self._line(stem_x, note_y, stem_x, stem_top, color, 2)
            if denominator >= 8:
                # 连续八分音符用共同符杠；孤立八分及更短时值用符尾。
                next_eighth = denominator == 8 and index + 1 < len(cfg.note_values) and cfg.note_values[index + 1] == 8
                previous_eighth = denominator == 8 and index > 0 and cfg.note_values[index - 1] == 8
                if next_eighth:
                    self._line(stem_x, stem_top + 2, note_xs[index + 1] + 7, stem_top + 2, color, 5)
                if not next_eighth and not previous_eighth:
                    flags = max(1, int(math.log2(denominator)) - 2)
                    for flag in range(flags):
                        flag_y = stem_top + flag * 7
                        points = [(stem_x, flag_y), (stem_x + 16, flag_y + 9),
                                  (stem_x + 18, flag_y + 21), (stem_x + 11, flag_y + 14),
                                  (stem_x, flag_y + 7)]
                        arcade.draw_polygon_filled([self._point(*point) for point in points], color)
            beat_label = str(int(offset) + 1) if offset.is_integer() else "&" if (offset * 2).is_integer() else f"{offset + 1:g}"
            self._text(("staff-beat", index), beat_label, note_x, 292, 14, MUTED, anchor_x="center")

    def on_draw(self):
        if self._closed_once:
            return
        self.clear()
        self._camera.use()
        if self._settings is not None:
            self._settings.draw()
            return
        app = self.controller
        cfg = app.config
        session = app.session
        reading = app.engine.reading()
        position = reading.position if reading.generation == app.expected_generation else 0.0
        bar = app.chart.bar_at(position)
        self._text("title", "主音归来 · 节奏 demo", 64, 25, 24)
        self._text("track", cfg.music_path.name, 64, 64, 15, MUTED, max_width=850)
        self._text("bpm", f"{cfg.bpm:g} BPM", 1096, 41, 16, MUTED, anchor_x="right")
        self._card(self._settings_button)
        self._text("settings-entry", "设置", 1162, 39, 17)

        title, detail, color = app.feedback
        errors = {"配置未加载", "设置未保存", "设置未应用", "音频设备无法停止",
                  "音频设备无法打开", "音频输出已中断"}
        preparing = session.entry_time is not None and position < session.entry_time
        if title in errors:
            self._text("error-title", title, 640, 426, 26, RED, anchor_x="center")
            self._text("error-detail", detail, 640, 470, 14, MUTED, max_width=1000, anchor_x="center")
        elif app.paused:
            self._text("entry-prompt", "已暂停", 640, 438, 28, MUTED, anchor_x="center")
        elif not app.desired_playing:
            prompt = "播放结束" if position >= app.engine.duration else "按 Enter 播放"
            self._text("entry-prompt", prompt, 640, 438, 26, MUTED, anchor_x="center")
        elif session.entry_time is None:
            self._text("entry-prompt", "第一拍拍手开始", 640, 438, 26, MUTED, anchor_x="center")
        elif preparing:
            beats_left = max(1, math.ceil((session.entry_time - position) / app.chart.beat_seconds))
            self._text("feedback", f"预备 · {beats_left}", 64, 110, 26, PURPLE)
        elif session.finished:
            self._text("entry-prompt", "本小节完成", 640, 438, 28, GREEN, anchor_x="center")
        else:
            feedback = "开始" if title in ("预备一小节", "预备中") else title
            self._text("feedback", feedback, 64, 110, 26, color)
        if session.entry_time is not None:
            self._text("score", f"命中 {session.hits}   漏拍 {session.misses}",
                       1216, 116, 18, MUTED, anchor_x="right")

        self._staff(position, bar)
        line_y, top_y = 650, 340
        for x in (480, 800):
            self._line(x, top_y, x, line_y, (225, 225, 225))
        for target in app.chart.visible_targets(position, cfg.lookahead_seconds, 0.25):
            if (session.entry_time is None or session.finished
                    or not session.entry_time <= target.time < session.round_end_time):
                continue
            y = line_y - (target.time - position) / cfg.lookahead_seconds * (line_y - top_y)
            if y < top_y or y > 675:
                continue
            state = session.states.get(target.index)
            color = GREEN if state == "hit" else RED if state == "miss" else PURPLE
            self._line(585, y, 695, y, color, 5)
        self._line(450, line_y, 830, line_y, GREEN, 3)
        self._line(64, 714, 1216, 714, LINE)
        for index, (rect, label, action, primary) in enumerate(self._game_buttons()):
            if action == "start":
                label = "重播" if app.desired_playing or app.paused else "播放"
            elif action == "toggle_pause":
                label = "继续" if app.paused else "暂停"
            self._card(rect)
            self._text(("button", index), label, rect[0] + 42, rect[1] + 12, 18, TEXT)
        if app.desired_playing:
            hint = "空格备用" if app.glove is not None else "空格 · 拍手"
            self._text("clap-key", hint, 1216, 751, 16, MUTED, anchor_x="right")

    def _game_buttons(self):
        app = self.controller
        return [button for button in self._buttons
                if not (button[2] == "toggle_pause" and not (app.desired_playing or app.paused))
                and not (button[2] == "reset_round" and app.session.entry_time is None)]

    def on_key_press(self, symbol, modifiers):
        host_time = perf_counter()
        if self._closed_once or symbol in self._held_keys:
            return
        self._held_keys.add(symbol)
        if self._settings is not None:
            self._settings.key_press(symbol, modifiers, host_time)
            return
        app = self.controller
        if symbol == arcade.key.F1:
            self.open_settings()
        elif symbol == arcade.key.SPACE:
            app.clap(host_time=host_time)
        elif symbol in (arcade.key.ENTER, arcade.key.NUM_ENTER):
            if app.paused:
                app.toggle_pause()
            elif not app.desired_playing:
                app.start()
        elif symbol == arcade.key.R:
            app.reset_round()
        elif symbol == arcade.key.P:
            app.toggle_pause()
        elif symbol == arcade.key.S:
            app.stop()
        elif symbol == arcade.key.BACKSPACE:
            app.start()
        elif symbol == arcade.key.F5:
            app.reload()
        elif symbol == arcade.key.ESCAPE:
            self.close()

    def open_settings(self):
        if self._settings is None and self.controller.reload():
            self.controller.auto_input = False
            self._settings = SettingsPage(self)

    def close_settings(self, saved=False):
        if self._settings is not None and not self._settings.leave(saved):
            return
        self._settings = None

    def on_text(self, text):
        if self._settings is not None:
            self._settings.text(text)

    def on_key_release(self, symbol, modifiers):
        self._held_keys.discard(symbol)

    def on_deactivate(self):
        self._held_keys.clear()

    def on_mouse_press(self, x, y, button, modifiers):
        if self._closed_once or button != arcade.MOUSE_BUTTON_LEFT:
            return
        design_x = (x - self._offset_x) / self._scale
        design_y = DESIGN_HEIGHT - (y - self._offset_y) / self._scale
        if self._settings is not None:
            self._settings.mouse_press(design_x, design_y)
            return
        left, top, width, height = self._settings_button
        if left <= design_x <= left + width and top <= design_y <= top + height:
            self.open_settings()
            return
        for (left, top, width, height), label, action, primary in self._game_buttons():
            if left <= design_x <= left + width and top <= design_y <= top + height:
                getattr(self.controller, action)()
                break

    def on_update(self, delta_time):
        if self._closed_once:
            return
        app = self.controller
        if self._settings is None and app.auto_input and app.desired_playing:
            app.inject_due_targets()
        calibration_inputs = app.update()
        if self._settings is not None:
            for event in calibration_inputs:
                self._settings.tap(event.host_time, event.music_time, play_sound=False,
                                   source=event.source, settled=True)
            self._settings.update()
        now = perf_counter()
        if now >= self._next_flush:
            app.log.flush()
            self._next_flush = now + 1
        if self._demo_seconds is not None and now - self._started_at >= self._demo_seconds:
            try:
                if self._screenshot is not None:
                    self.switch_to()
                    self.on_draw()
                    self._screenshot.parent.mkdir(parents=True, exist_ok=True)
                    arcade.get_image(width=self.width, height=self.height).save(self._screenshot)
            finally:
                self.close()

    def _stats(self):
        return {
            "hits": self.controller.session.hits,
            "misses": self.controller.session.misses,
            "underruns": self.controller.engine.underruns,
            "log": str(self.controller.log_path),
        }

    def run(self, demo_seconds=None, screenshot=None):
        """运行原生事件循环；自动验证到时可保存真实窗口 framebuffer PNG。"""
        if self._run_started:
            raise RuntimeError("同一窗口不能重复运行事件循环")
        if self._closed_once:
            return self._stats()
        if demo_seconds is not None and (not math.isfinite(demo_seconds) or demo_seconds <= 0):
            raise ValueError("demo_seconds 必须为有限正数")
        if screenshot is not None and demo_seconds is None:
            raise ValueError("自动截图需要指定 demo_seconds")
        self._run_started = True
        self._demo_seconds = demo_seconds
        self._screenshot = Path(screenshot) if screenshot is not None else None
        self._started_at = perf_counter()
        self._next_flush = self._started_at + 1
        try:
            if demo_seconds is not None:
                self.controller.auto_input = True
                self.controller.start()
            arcade.run()
            return self._stats()
        finally:
            self.close()

    def on_close(self):
        self.close()

    def close(self):
        """退出、外部关闭及异常路径共用一次资源清理。"""
        if self._closed_once:
            return
        self._closed_once = True
        self._held_keys.clear()
        try:
            self.controller.close()
        finally:
            if self._window_ready:
                super().close()
