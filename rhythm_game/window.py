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
        self._settings_button = (862, 30, 150, 40)
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
                ((40, 749, 174, 43), "播放 / 重来", "start", True),
                ((226, 749, 132, 43), "暂停 / 继续", "toggle_pause", False),
                ((370, 749, 148, 43), "清空本轮", "reset_round", False),
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

    def _card(self, rect, color=PANEL, radius=16, border=None):
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
        self._text("staff-caption", "节奏示意 · 固定音高", 474, 204, 12, MUTED)
        for staff_y in (233, 245, 257, 269, 281):
            self._line(548, staff_y, 1192, staff_y, (83, 99, 125), 1)
        self._line(548, 233, 548, 281, MUTED, 1.5)
        self._line(1192, 233, 1192, 281, MUTED, 1.5)
        self._text("signature-top", cfg.beats_per_bar, 514, 231, 24, TEXT, anchor_x="center")
        self._text("signature-bottom", "4", 514, 259, 24, TEXT, anchor_x="center")
        self._text("staff-beat-label", "拍数", 474, 292, 11, MUTED)
        self._text("staff-order-label", "序号", 474, 313, 11, (106, 122, 148))
        offsets = []
        elapsed = 0.0
        for value in cfg.note_values:
            offsets.append(elapsed)
            elapsed += 4 / value
        note_xs = [585 + offset / cfg.beats_per_bar * 570 for offset in offsets]
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
            self._text(("staff-order", index), str(index + 1), note_x, 313, 11, (106, 122, 148), anchor_x="center")

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
        reading = app.engine.reading()
        position = reading.position if reading.generation == app.expected_generation else 0.0
        bar = app.chart.bar_at(position)
        self._text("title", "主音归来", 40, 28, 34)
        self._text("subtitle", "节奏练习  /  第 1 阶段", 218, 42, 17, MUTED)
        self._card(self._settings_button, (35, 44, 62), 9)
        self._text("settings-entry", "设置 / 校准", 878, 40, 17)
        self._card((1032, 30, 208, 40), (27, 51, 46), 20)
        self._text("input-mode", "自动软件验证" if app.auto_input else "键盘输入 · 手套未接入", 1050, 39, 16, GREEN)

        self._card((40, 106, 384, 624))
        self._text("rules-heading", "HOW TO PLAY", 64, 129, 14, PURPLE)
        self._text("rules-title", "跟随音乐，拍出节奏", 64, 158, 26)
        rules = [
            ("播放音乐", "Enter 开始；音乐从文件开头播放"),
            ("第一拍拍手启动", "任意小节第一拍按空格，先预备一小节" if cfg.lead_in_bars else "任意小节第一拍按空格，立即开始"),
            ("正式打一个小节", "跟随短线；到线再拍，不需长按"),
        ]
        for index, (title, detail) in enumerate(rules):
            y = 218 + index * 68
            self._card((64, y + 2, 32, 32), (42, 43, 67), 10)
            self._text(("rule-number", index), f"0{index + 1}", 70, y + 8, 13, PURPLE)
            self._text(("rule-title", index), title, 110, y, 19)
            self._text(("rule-detail", index), detail, 110, y + 29, 14, MUTED)
        title, detail, color = app.feedback
        if title in ("预备一小节", "预备中") and app.session.entry_time is not None and position >= app.session.entry_time:
            title, detail, color = "正式开始", "跟随短线，打完这一个小节", GREEN
        self._card((64, 442, 336, 137), (28, 36, 51), border=color)
        self._text("feedback-title", title, 84, 459, 32, color, max_width=296)
        self._text("feedback-detail", detail, 84, 514, 15, MUTED, max_width=296)
        self._text("hits-label", "命中", 67, 606, 15, MUTED)
        self._text("hits", f"{app.session.hits:03d}", 64, 631, 36, GREEN)
        self._text("misses-label", "漏拍", 220, 606, 15, MUTED)
        self._text("misses", f"{app.session.misses:03d}", 217, 631, 36, RED)
        self._text("window", f"试用容差 ±{cfg.window_ms:g} ms", 64, 697, 14, MUTED)

        self._card((448, 106, 792, 624))
        self._text("bpm", f"{cfg.bpm:g}", 474, 121, 40)
        self._text("bpm-label", "BPM", 582, 145, 14, MUTED)
        bar_label = f"第 {bar[0]:02d} 小节" if bar else "曲目已结束" if position >= app.engine.duration else "等待第一小节"
        self._text("bar", bar_label, 660, 128, 21)
        self._text("chart-start", f"首拍 {app.chart.start:.3f}s · 偏移 {cfg.beat_offset_ms:+g}ms", 660, 161, 14, MUTED)
        for index in range(cfg.beats_per_bar):
            x = 1055 + index * min(28, 145 / cfg.beats_per_bar)
            active = bar is not None and int(bar[1] - 1) == index
            self._circle(x, 146, 6, GREEN if active else LINE)
        self._line(474, 200, 1214, 200, LINE)
        self._staff(position, bar)
        caption = "正式小节 · 短线到达判定线时拍掌" if app.session.entry_time is not None else "等待第一拍拍手 · 拍手后出现本小节短线"
        self._text("lane-caption", caption, 474, 337, 13, MUTED)
        line_y, top_y = 644, 360
        for x in (700, 990):
            self._line(x, top_y, x, line_y, (32, 40, 57))
        slot = 0
        for target in app.chart.visible_targets(position, cfg.lookahead_seconds, 0.25):
            if (app.session.entry_time is None or app.session.finished
                    or not app.session.entry_time <= target.time < app.session.round_end_time):
                continue
            y = line_y - (target.time - position) / cfg.lookahead_seconds * (line_y - top_y)
            if y < top_y or y > 670:
                continue
            state = app.session.states.get(target.index)
            color = GREEN if state == "hit" else RED if state == "miss" else PURPLE
            # 所有目标使用同一条短线；线宽和厚度均不编码音符／接触时长。
            self._line(790, y, 900, y, color, 5)
            ordinal = target.index % len(cfg.note_values) + 1
            self._text(("falling-order", slot), f"{ordinal} · {target.time:.2f}s", 931, y - 9, 13, MUTED)
            slot += 1
        self._line(670, line_y, 1020, line_y, GREEN, 3)
        self._text("judgment-line", "判定线", 1070, line_y - 12, 16, GREEN)
        if app.session.entry_time is None:
            self._text("entry-prompt", "在小节第一拍拍手开始", 845, 445, 25, MUTED, anchor_x="center")
        elif position < app.session.entry_time:
            beats_left = max(1, math.ceil((app.session.entry_time - position) / app.chart.beat_seconds))
            self._text("entry-prompt", f"预备 {beats_left} 拍", 590, 407, 25, GREEN, anchor_x="center")
            self._text("entry-prompt-detail", "下一小节正式开始", 590, 447, 14, MUTED, anchor_x="center")
        elif app.session.finished:
            self._text("entry-prompt", "本小节完成", 845, 445, 25, GREEN, anchor_x="center")
        self._text("position", f"{position:06.2f} / {app.engine.duration:.2f}s", 474, 690, 15)
        self._text("output-health", f"输出延迟估计 {app.engine.latency_ms:.1f}ms  ·  欠载 {app.engine.underruns}", 800, 692, 13, MUTED)
        for index, (rect, label, action, primary) in enumerate(self._buttons):
            self._card(rect, GREEN if primary else (35, 44, 62), 9)
            self._text(("button", index), label, rect[0] + 18, rect[1] + 11, 18, BG if primary else TEXT)
        self._text("shortcuts", "P 暂停  ·  R 清空本轮  ·  S 停止  ·  F5 加载配置", 554, 762, 15, MUTED)

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

    def close_settings(self):
        if self._settings is not None and self._settings.calibrating:
            self._settings.calibrating = False
            self.controller.stop()
            self.controller.record("calibration_cancel")
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
        for (left, top, width, height), label, action, primary in self._buttons:
            if left <= design_x <= left + width and top <= design_y <= top + height:
                getattr(self.controller, action)()
                break

    def on_update(self, delta_time):
        if self._closed_once:
            return
        app = self.controller
        if self._settings is None and app.auto_input and app.desired_playing:
            app.inject_due_targets()
        app.update()
        if self._settings is not None:
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
