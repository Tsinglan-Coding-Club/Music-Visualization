"""Arcade 窗口：火锅主题 2×4 音符卡、判定动画与操作入口。

音频与判定由控制器负责；按键回调立即记录时间，不等待下一次画面更新。
画面由 HotpotStage 绘制；所有坐标沿用 1280 × 800 设计坐标。
"""

import math
from pathlib import Path
from time import perf_counter

import arcade

from .app import BG, PANEL, LINE, TEXT
from .settings import SettingsPage
from .hotpot_ui import HotpotStage, NIGHT


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
                "MUSIC HOTPOT · 节奏火锅",
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
            self.stage = HotpotStage(self)
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

    def _text(self, key, value, x, y, size=20, color=TEXT, max_width=None, anchor_x="left",
              font_name=None):
        """按稳定位置缓存标签，仅在内容、缩放或位置改变时更新。"""
        value = str(value)
        px, py = self._point(x, y)
        font_size = size * 0.75 * self._scale  # 设计字号为像素；Text 字号单位为点。
        label = self._texts.get(key)
        if label is None:
            label = arcade.Text(
                "", px, py, color,
                font_size=font_size,
                font_name=font_name or FONT_NAMES,
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

    def on_draw(self):
        if self._closed_once:
            return
        if self._settings is not None:
            self.background_color = BG
            self.clear()
            self._camera.use()
            self.stage.draw_settings_backdrop()
            self._settings.draw()
            return
        self.background_color = NIGHT
        self.clear()
        self._camera.use()
        self.stage.draw()

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
        if self._settings is None:
            self.stage.update(delta_time)
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
            "mistakes": self.controller.session.mistakes,
            "failed": self.controller.session.failed,
            "accuracy": self.controller.session.accuracy,
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
