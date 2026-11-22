"""Hotpot 舞台：暖色卡座、火锅插图、音符卡与判定动画。

只负责画面，不改变音频、判定或输入。所有坐标沿用窗口的 1280×800 设计坐标，
通过 window 的缩放辅助函数绘制，因此窗口拉伸时布局保持一致。
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import random
import re
from time import perf_counter

import arcade

ASSET_DIR = Path(__file__).resolve().parent.parent / "assets" / "hotpot"
DISPLAY_FONTS = ("Avenir Next Heavy", "Arial Rounded MT Bold", "PingFang SC", "Arial")
TEXT_FONTS = ("PingFang SC", "Heiti SC", "Arial")

# 暖色火锅夜市场景
NIGHT = (24, 12, 18)
NIGHT_2 = (38, 20, 26)
NIGHT_3 = (56, 28, 32)
INK = (44, 26, 30)
CREAM = (255, 244, 224)
CREAM_2 = (255, 230, 196)
CREAM_3 = (244, 208, 166)
CORAL = (255, 104, 76)
BROTH = (210, 66, 46)
GOLD = (255, 196, 92)
JADE = (104, 214, 158)
MINT = (176, 242, 204)
PLUM = (132, 62, 94)
PINK = (255, 150, 150)
MUTED = (198, 164, 150)

FOOD_TEXTURES = ("tomatoes", "cabbage", "mushrooms", "meat", "dish")

CARD_W, CARD_H = 148, 148
CARD_GAP = 22
GRID_X, GRID_Y = 360, 190
GRID_W = CARD_W * 4 + CARD_GAP * 3
ROW_GAP = 30
BOTTOM_Y = GRID_Y + CARD_H + ROW_GAP
PANEL_RECT = (330, 160, 720, 370)

JUDGE_TEXT = {
    "命中": "HIT!",
    "预备拍命中": "PREP HIT!",
    "太早了": "EARLY",
    "太晚了": "LATE",
    "重复拍掌": "DUPLICATE",
    "跟拍预备": "PREP",
    "预备重复拍掌": "PREP AGAIN",
    "已启动": "LET'S COOK!",
    "等待启动拍手": "READY",
    "本轮完成": "CLEAR!",
    "本轮失败": "FAILED",
    "当前未播放": "PAUSED",
    "准备就绪": "READY TO COOK",
    "播放结束": "FINISHED",
    "已停止": "STOPPED",
    "开始": "GO!",
}

JUDGE_COLOR = {
    "命中": GOLD,
    "预备拍命中": JADE,
    "太早了": CORAL,
    "太晚了": CORAL,
    "重复拍掌": PINK,
    "跟拍预备": MUTED,
    "预备重复拍掌": PINK,
    "本轮完成": MINT,
    "本轮失败": CORAL,
}


def mix(a, b, t):
    t = max(0.0, min(1.0, t))
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def dim(color, amount):
    return mix(color, NIGHT_2, amount)


@dataclass(frozen=True)
class BeatCard:
    time: float
    bar: int
    beat: int
    targets: tuple
    kind: str  # preview / prep / formal
    rests: tuple = ()


@dataclass
class Particle:
    x: float
    y: float
    vx: float
    vy: float
    size: float
    color: tuple
    life: float
    age: float
    rot: float
    vr: float
    circle: bool


@dataclass
class Food:
    x: float
    y: float
    vx: float
    vy: float
    size: float
    texture: str
    angle: float
    spin: float
    age: float
    life: float


@dataclass
class Ring:
    x: float
    y: float
    color: tuple
    max_radius: float
    life: float
    age: float
    width: float



def max_bar(chart):
    return max((target.bar for target in chart.targets), default=0)


def bar_cards(chart, bar, kind):
    """把一个小节的音符和休止符按四分拍分组成最多四张卡；空拍也保留卡片。"""
    cards = []
    targets = [target for target in chart.targets if target.bar == bar]
    rests = [rest for rest in chart.rests if rest.bar == bar]
    for beat in range(chart.beats_per_bar):
        beat_targets = tuple(target for target in targets if int(target.beat_offset) == beat)
        beat_rests = tuple(rest for rest in rests if int(rest.beat_offset) == beat)
        time = chart.start + ((bar - 1) * chart.beats_per_bar + beat) * chart.beat_seconds
        cards.append(BeatCard(time, bar, beat, beat_targets, kind, beat_rests))
    return tuple(cards)


def prep_cards(session):
    return tuple(BeatCard(target.time, 1, index, (target,), "prep")
                 for index, target in enumerate(session.preparation_targets))


def stage_cards(app, position):
    """返回 (上排, 下排, 上排标签, 下排标签)。上排是当前段落，下排是下一小节。"""
    session = app.session
    chart = app.chart
    if session.entry_time is None or session.score_chart is None:
        top = bar_cards(chart, 1, "preview")
        bottom = bar_cards(chart, 2, "preview") if max_bar(chart) >= 2 else ()
        return top, bottom, "PREVIEW", "NEXT"
    if position < session.entry_time:
        top = prep_cards(session)
        bottom = bar_cards(session.score_chart, 1, "formal")
        return top, bottom, "PREP", "FIRST BAR"
    total = session.round_game_bars or app.config.game_bars
    if total <= 0:
        total = max_bar(session.score_chart) or 1
    bar = int((position - session.entry_time) // session.score_chart.bar_seconds) + 1
    bar = min(max(bar, 1), total)
    top = bar_cards(session.score_chart, bar, "formal")
    bottom = (bar_cards(session.score_chart, bar + 1, "formal")
              if bar < total else ())
    return top, bottom, f"BAR {bar}/{total}", "NEXT"


def current_beat(top, app, position):
    if not top:
        return -1
    session = app.session
    card = top[0]
    if card.kind == "prep":
        start = session.preparation_start_time
        if start is None or position < start:
            return -1
        chart = session.chart
    else:
        chart = session.score_chart or app.chart
        start = chart.start + (card.bar - 1) * chart.bar_seconds
        if position < start:
            return -1
    beat = int((position - start) // chart.beat_seconds)
    return min(max(beat, 0), len(top) - 1)


def card_state(card, session):
    if card.kind == "prep":
        states = [session.preparation_states.get(target.index) for target in card.targets]
    elif card.kind == "formal":
        states = [session.states.get(target.index) for target in card.targets]
        states += [session.rest_states.get(rest.index) for rest in card.rests]
    else:
        return None
    if not states:
        return None
    if all(state == "hit" for state in states):
        return "hit"
    if any(state == "miss" for state in states):
        return "miss"
    if any(state == "hit" for state in states):
        return "partial"
    return None


class HotpotStage:
    def __init__(self, window):
        self.w = window
        self.textures = {}
        for name in ("pot", "bowl", "dish", "cabbage", "mushrooms", "meat", "tomatoes", "chopsticks"):
            path = ASSET_DIR / f"{name}.png"
            if path.exists():
                try:
                    self.textures[name] = arcade.load_texture(str(path))
                except Exception:
                    pass
        self.glow = None
        glow_path = ASSET_DIR / "glow.png"
        if glow_path.exists():
            try:
                self.glow = arcade.load_texture(str(glow_path))
            except Exception:
                pass
        self.rng = random.Random(20261118)
        self._now = perf_counter()
        self._event_text = None
        self._event_color = CREAM
        self._event_detail = ""
        self._event_time = 0.0
        self._marker = None
        self._top = ()
        self._bottom = ()
        self._top_label = "PREVIEW"
        self._bottom_label = "NEXT"
        self._current = -1
        self._position = 0.0
        self._particles = []
        self._rings = []
        self._foods = []
        self._flash = {}
        self._seen_formal = set()
        self._seen_prep = set()
        self._last_mistakes = 0
        self._last_feedback = None
        self._combo = 0
        self._combo_pop = 0.0
        self._bar_key = None
        self._bar_changed_at = 0.0
        self._result_time = None
        self._embers = [
            [self.rng.uniform(40, 1240), self.rng.uniform(0, 800),
             self.rng.uniform(6, 18), self.rng.uniform(0, math.tau),
             self.rng.uniform(1.2, 3.0), self.rng.choice((GOLD, CORAL, CREAM_3))]
            for _ in range(26)
        ]

    # ---------- 状态与动画 ----------

    def _reset_visual(self, session):
        self._seen_formal = set()
        self._seen_prep = set()
        self._last_mistakes = session.mistakes
        self._last_feedback = None
        self._combo = 0
        self._combo_pop = 0.0
        self._particles = []
        self._rings = []
        self._foods = []
        self._flash = {}
        self._bar_key = None
        self._bar_changed_at = 0.0
        self._result_time = None
        self._event_text = None
        self._event_time = 0.0

    def update(self, delta_time):
        self._now = perf_counter()
        app = self.w.controller
        session = app.session
        reading = app.engine.reading()
        position = reading.position if reading.generation == app.expected_generation else 0.0
        self._position = position
        marker = (app.expected_generation, session.entry_time, session.round_end_time)
        if marker != self._marker:
            self._marker = marker
            self._reset_visual(session)

        self._top, self._bottom, self._top_label, self._bottom_label = stage_cards(app, position)
        self._current = current_beat(self._top, app, position)

        bar_key = (self._top[0].kind, self._top[0].bar) if self._top else None
        if bar_key != self._bar_key:
            self._bar_key = bar_key
            self._bar_changed_at = self._now

        for index, state in list(session.states.items()):
            if index in self._seen_formal:
                continue
            if state == "hit":
                self._seen_formal.add(index)
                self._on_hit(index, prep=False)
            elif state == "miss":
                self._seen_formal.add(index)
                self._on_miss(index, prep=False)
        for index, state in list(session.preparation_states.items()):
            if index in self._seen_prep:
                continue
            if state == "hit":
                self._seen_prep.add(index)
                self._on_hit(index, prep=True)
            elif state == "miss":
                self._seen_prep.add(index)
                self._on_miss(index, prep=True)

        if session.mistakes > self._last_mistakes:
            self._combo = 0
            self._last_mistakes = session.mistakes
        feedback_key = (app.feedback[0], app.feedback[1], app.feedback_time)
        if feedback_key != self._last_feedback:
            self._last_feedback = feedback_key
            title = app.feedback[0]
            if title in ("太早了", "太晚了", "重复拍掌", "跟拍预备", "预备重复拍掌"):
                self._feedback_effect(title)
            elif title in ("本轮完成", "本轮失败") and self._result_time is None:
                self._result_time = self._now
                self._spawn_result(title == "本轮完成")

        self._update_effects(min(max(delta_time, 0.0), 0.05))

    def _card_for(self, target_index, prep):
        cards = self._top if prep or self._top else self._bottom
        for card in cards:
            if any(target.index == target_index for target in card.targets):
                return card
        return self._top[0] if self._top else (self._bottom[0] if self._bottom else None)

    def _center_of(self, card):
        if card is None:
            return 640, 300
        x = GRID_X + card.beat * (CARD_W + CARD_GAP) + CARD_W / 2
        in_top = card in self._top
        y = (GRID_Y if in_top else BOTTOM_Y) + CARD_H / 2
        return x, y

    def _error_ms(self):
        match = re.search(r"([+-]?\d+(?:\.\d+)?)\s*ms", str(self.w.controller.feedback[1]))
        return float(match.group(1)) if match else None

    def _target_offset(self, card, index):
        if card is None or not card.targets:
            return 0
        count = len(card.targets)
        position = next((i for i, target in enumerate(card.targets) if target.index == index),
                        (count - 1) / 2)
        return (position - (count - 1) / 2) * 24

    def _on_hit(self, index, prep):
        card = self._card_for(index, prep)
        x, y = self._center_of(card)
        x += self._target_offset(card, index)
        error = None if prep else self._error_ms()
        if prep:
            self._rings.append(Ring(x, y, JADE, 70, .48, 0, 3))
            self._spawn_particles(x, y - 8, 16, (JADE, MINT, CREAM, GOLD), 160)
            label = "PREP HIT!"
            color = JADE
        else:
            self._combo += 1
            self._combo_pop = self._now
            perfect = error is not None and abs(error) <= 35
            label = "PERFECT!" if perfect else "HIT!"
            color = GOLD if perfect else MINT
            self._rings.append(Ring(x, y, color, 96, .58, 0, 4))
            self._rings.append(Ring(x, y, CORAL, 72, .42, 0, 2))
            self._spawn_particles(x, y - 10, 34, (GOLD, CORAL, JADE, CREAM, PINK, CREAM_2), 280)
        if card is not None:
            self._flash[(card.kind, card.bar, card.beat)] = self._now
        self._spawn_food(x, y, prep)
        self._event_text = label
        self._event_color = color
        self._event_detail = ("ON BEAT" if error is not None and abs(error) < .05
                              else f"{error:+.1f} ms" if error is not None else "")
        self._event_time = self._now

    def _spawn_food(self, x, y, prep):
        if len(self._foods) >= 48:
            self._foods.pop(0)
        size = self.rng.uniform(32, 42) if prep else self.rng.uniform(40, 54)
        self._foods.append(Food(
            x=x + self.rng.uniform(-14, 14),
            y=y - CARD_H / 2 + 4,
            vx=self.rng.uniform(-260, 260),
            vy=self.rng.uniform(-360, -260),
            size=size,
            texture=self.rng.choice(FOOD_TEXTURES),
            angle=self.rng.uniform(-30, 30),
            spin=self.rng.uniform(-210, 210),
            age=0.0,
            life=4.0,
        ))

    def _on_miss(self, index, prep):
        card = self._card_for(index, prep)
        x, y = self._center_of(card)
        x += self._target_offset(card, index)
        self._combo = 0
        self._rings.append(Ring(x, y, CORAL, 64, .42, 0, 3))
        self._spawn_particles(x, y - 6, 12, (CORAL, PLUM, MUTED), 130)
        if card is not None:
            self._flash[(card.kind, card.bar, card.beat)] = self._now
        self._event_text, self._event_color, self._event_detail = "MISS", CORAL, "漏拍"
        self._event_time = self._now

    def _feedback_effect(self, title):
        card = self._top[self._current] if 0 <= self._current < len(self._top) else (
            self._top[0] if self._top else None)
        x, y = self._center_of(card)
        label = JUDGE_TEXT.get(title, title)
        color = JUDGE_COLOR.get(title, MUTED)
        self._event_text, self._event_color = label, color
        self._event_detail = self.w.controller.feedback[1]
        self._event_time = self._now
        self._rings.append(Ring(x, y, color, 56, .32, 0, 2))

    def _spawn_particles(self, x, y, count, colors, speed):
        for _ in range(count):
            angle = self.rng.uniform(0, math.tau)
            velocity = self.rng.uniform(speed * .35, speed)
            self._particles.append(Particle(
                x, y,
                math.cos(angle) * velocity,
                math.sin(angle) * velocity - self.rng.uniform(40, 150),
                self.rng.uniform(3, 7),
                self.rng.choice(colors),
                self.rng.uniform(.55, 1.05),
                0.0,
                self.rng.uniform(0, 360),
                self.rng.uniform(-600, 600),
                self.rng.random() < .28,
            ))

    def _spawn_result(self, success):
        x, y = 640, 340
        colors = (GOLD, JADE, CORAL, CREAM, MINT, PINK) if success else (CORAL, PLUM, MUTED, CREAM_3)
        self._spawn_particles(x, y, 90 if success else 45, colors, 420)
        self._rings.append(Ring(x, y, GOLD if success else CORAL, 220, .9, 0, 5))

    def _update_effects(self, dt):
        for particle in self._particles:
            particle.x += particle.vx * dt
            particle.y += particle.vy * dt
            particle.vy += 620 * dt
            particle.rot += particle.vr * dt
            particle.age += dt
        self._particles = [particle for particle in self._particles if particle.age < particle.life]
        for food in self._foods:
            food.vy += 760 * dt
            food.x += food.vx * dt
            food.y += food.vy * dt
            food.angle += food.spin * dt
            food.age += dt
        self._foods = [food for food in self._foods
                       if food.age < food.life and food.y < 880 and -160 < food.x < 1440]
        for ring in self._rings:
            ring.age += dt
        self._rings = [ring for ring in self._rings if ring.age < ring.life]
        for ember in self._embers:
            ember[1] -= ember[2] * dt
            ember[3] += dt * .6
            if ember[1] < -10:
                ember[0] = self.rng.uniform(40, 1240)
                ember[1] = 810
                ember[2] = self.rng.uniform(6, 18)

    # ---------- 绘制辅助 ----------

    def _s(self, value):
        return value * self.w._scale

    def _rect(self, rect, color):
        x, y, width, height = rect
        left, bottom = self.w._point(x, y + height)
        arcade.draw_lbwh_rectangle_filled(left, bottom, width * self.w._scale,
                                          height * self.w._scale, color)

    def _ellipse(self, x, y, width, height, color, outline=False):
        sx, sy = self.w._point(x, y)
        if outline:
            arcade.draw_ellipse_outline(sx, sy, width * self.w._scale, height * self.w._scale,
                                        color, border_width=max(1.0, 2.0 * self.w._scale))
        else:
            arcade.draw_ellipse_filled(sx, sy, width * self.w._scale, height * self.w._scale, color)

    def _text(self, key, value, x, y, size, color, anchor_x="left", font_name=None,
              max_width=None):
        text = str(value)
        chinese = any(ord(char) > 127 for char in text)
        chosen = TEXT_FONTS if chinese else (font_name or DISPLAY_FONTS)
        # 同一标签在英文／中文之间切换时使用不同的 Text 缓存，避免用错字体。
        cache_key = (key, "cn" if chinese else "en")
        self.w._text(cache_key, value, x, y, size, color, anchor_x=anchor_x,
                     font_name=chosen, max_width=max_width)

    def _pill(self, rect, fill, border, radius=18):
        self.w._card(rect, fill, radius=radius, border=border)

    def _flame(self, x, y, size, color):
        self._ellipse(x, y, size * .8, size * 1.15, color)
        self._ellipse(x, y - size * .35, size * .55, size * .7, mix(color, CORAL, .35))
        self._circle_screen(x, y - size * .7, size * .18, GOLD)

    def _circle_screen(self, x, y, radius, color):
        self.w._circle(x, y, radius, color)

    def _line(self, x1, y1, x2, y2, color, width=1):
        self.w._line(x1, y1, x2, y2, color, width)

    # ---------- 背景与装饰 ----------


    def draw_settings_backdrop(self):
        """设置页保持浅色，但仍用火锅色系的暖背景。"""
        top, bottom = (252, 248, 241), (243, 230, 212)
        bands = 20
        left, base = self.w._point(0, 800)
        height = 800 * self.w._scale
        width = 1280 * self.w._scale
        for index in range(bands):
            color = mix(top, bottom, index / (bands - 1))
            arcade.draw_lbwh_rectangle_filled(
                left, base + height * index / bands, width, height / bands + 1, color)
        self.w._circle(1080, 120, 220, (255, 150, 80, 16))
        self.w._circle(1080, 120, 150, (255, 180, 100, 12))
        for index in range(4):
            phase = self._now * .45 + index * 1.4
            x = 980 + index * 42 + math.sin(phase) * 12
            y = 640 - (self._now * 12 + index * 70) % 220
            self._ellipse(x, y, 30, 12, (255, 190, 140, 14))
        for gy in range(6):
            for gx in range(9):
                self.w._circle(120 + gx * 130, 110 + gy * 110, 1.2, (180, 120, 80, 14))

    def _draw_background(self):
        top, bottom = (30, 14, 20), (16, 8, 13)
        bands = 26
        left, base = self.w._point(0, 800)
        height = 800 * self.w._scale
        width = 1280 * self.w._scale
        for index in range(bands):
            color = mix(top, bottom, index / (bands - 1))
            arcade.draw_lbwh_rectangle_filled(
                left, base + height * index / bands, width, height / bands + 1, color)
        if self.glow is not None:
            left, bottom = self.w._point(120, 780)
            rect = arcade.LBWH(left, bottom, 1040 * self.w._scale, 760 * self.w._scale)
            arcade.draw_texture_rect(self.glow, rect)
        # 细微的点阵纹理
        for gy in range(8):
            for gx in range(13):
                self.w._circle(90 + gx * 92, 80 + gy * 92, 1.2, (255, 196, 92, 12))
        # 飘动火星
        for x, y, speed, phase, size, color in self._embers:
            self.w._circle(x, y, size, (*color, 130))
            self.w._circle(x, y, size * .45, (*CREAM, 180))
        # 火锅蒸汽
        for index in range(6):
            phase = self._now * .55 + index * 1.15
            x = 560 + index * 26 + math.sin(phase) * 16
            y = 420 - (self._now * (18 + index * 2) + index * 55) % 300
            self._ellipse(x, y, 24 + index * 5, 10 + index * 2, (255, 235, 210, 18))
            self._ellipse(x + 8, y - 16, 30 + index * 4, 12, (255, 220, 190, 12))

    def _draw_sticker(self, name, x, y, width, height, angle=0, muted=False):
        shadow = (10, 5, 9)
        border = (170, 118, 64) if muted else GOLD
        plate = (226, 204, 174) if muted else CREAM
        self.w._rounded_fill((x + 5, y + 7, width, height), shadow, 20)
        self.w._rounded_fill((x - 8, y - 8, width + 16, height + 16), border, 24)
        self.w._rounded_fill((x - 5, y - 5, width + 10, height + 10), plate, 21)
        texture = self.textures.get(name)
        if texture is None:
            self._ellipse(x + width / 2, y + height / 2, width * .55, height * .55, CORAL)
            return
        left, bottom = self.w._point(x, y + height)
        rect = arcade.LBWH(left, bottom, width * self.w._scale, height * self.w._scale)
        arcade.draw_texture_rect(texture, rect, angle=angle, alpha=190 if muted else 255)

    def _draw_decorations(self):
        self._draw_sticker("tomatoes", 58, 88, 60, 60, -8, muted=True)
        self._draw_sticker("bowl", 816, 84, 56, 56, 6, muted=True)
        self._draw_sticker("cabbage", 1152, 90, 58, 58, 8, muted=True)
        self._draw_sticker("meat", 66, 548, 74, 74, -6)
        self._draw_sticker("chopsticks", 1144, 544, 74, 74, 6)
        self._draw_sticker("mushrooms", 226, 616, 64, 64, 4)
        self._draw_sticker("pot", 616, 596, 112, 96, 0)
        self._draw_sticker("dish", 890, 604, 106, 92, -4)

    # ---------- 卡片 ----------

    def _draw_note(self, target, px, py, color):
        denominator = target.denominator
        stem_x = px + 7
        stem_top = py - 36
        if denominator >= 2:
            self._line(stem_x, py - 2, stem_x, stem_top, color, 2.4)
        if denominator >= 2:
            self._ellipse(px, py, 20, 13, color, outline=denominator <= 2)
        else:
            self._ellipse(px, py, 22, 14, color, outline=True)
        for dot in range(target.dots):
            self.w._circle(px + 13 + dot * 5, py - 5, 2.2, color)

    def _draw_flag(self, stem_x, top_y, color):
        # 标准符尾：从符干顶端向右下勾，再回到符干方向。
        self._line(stem_x, top_y, stem_x + 11, top_y + 7, color, 2.4)
        self._line(stem_x + 11, top_y + 7, stem_x + 2, top_y + 14, color, 2.4)

    def _draw_rest(self, rest, px, py, color):
        denominator = rest.denominator
        if denominator == 1:
            # 全休止：从第四线下方悬挂。
            self.w._rounded_fill((px - 10, py - 20, 20, 5), color, 2)
        elif denominator == 2:
            # 二分休止：坐在第三线上。
            self.w._rounded_fill((px - 10, py - 12, 20, 5), color, 2)
        elif denominator >= 8:
            # 八分休止：斜符干，上端一个符头钩；十六分／三十二分依次加钩。
            self._line(px + 5, py - 37, px - 6, py - 13, color, 3.4)
            self._line(px + 5, py - 37, px + 9, py - 31, color, 3)
            hooks = min(3, max(1, denominator.bit_length() - 3))
            for index in range(hooks):
                self.w._circle(px - 4 - index * 2, py - 30 - index * 7, 3.3, color)
        else:
            # 四分休止：乐理中的三折钩形，顶端短横、底端回钩。
            points = [(px + 7, py - 4), (px - 4, py - 13), (px + 5, py - 20),
                      (px - 4, py - 28), (px + 1, py - 34), (px + 7, py - 30)]
            for (x1, y1), (x2, y2) in zip(points, points[1:]):
                self._line(x1, y1, x2, y2, color, 4)
        for dot in range(rest.dots):
            self.w._circle(px + 13 + dot * 5, py - 12, 2.2, color)

    def _draw_note_row(self, card, x, y, width, height, color):
        events = [(float(target.beat_offset), 0, target) for target in card.targets]
        events += [(float(rest.beat_offset), 1, rest) for rest in card.rests]
        events.sort(key=lambda item: (item[0], item[1]))
        if not events:
            # 空拍：画一个很淡的休止符，让 2×4 卡座保持完整
            self._ellipse(x + width / 2, y + height * .62, 18, 10, dim(color, .55), outline=True)
            self._line(x + width / 2 + 5, y + height * .62, x + width / 2 - 2,
                       y + height * .48, dim(color, .55), 2)
            return
        count = len(events)
        positions = [x + width * (index + 1) / (count + 1) for index in range(count)]
        base_y = y + height * .67
        stem_top = base_y - 36
        for index, (_, kind, event) in enumerate(events):
            if kind == 0:
                self._draw_note(event, positions[index], base_y, color)
            else:
                self._draw_rest(event, positions[index], base_y, color)
        # 符杠只连接音符；休止符会打断连杠。
        beam_counts = [
            min(4, max(0, event.denominator.bit_length() - 3))
            if kind == 0 and event.denominator >= 8 else 0
            for _, kind, event in events
        ]
        for level in range(1, max(beam_counts, default=0) + 1):
            run_start = None
            for index in range(count + 1):
                active = index < count and beam_counts[index] >= level
                if active and run_start is None:
                    run_start = index
                if (not active or index == count) and run_start is not None:
                    run_end = index - 1
                    beam_y = stem_top + (level - 1) * 7
                    if run_end > run_start:
                        self._line(positions[run_start] + 7, beam_y,
                                   positions[run_end] + 7, beam_y, color,
                                   4 if level == 1 else 3)
                    else:
                        self._draw_flag(positions[run_start] + 7, beam_y, color)
                    run_start = None

    def _sustain_target(self, card):
        """如果上一拍的长音符仍然覆盖这张卡，返回那个音符。"""
        if card.kind == "prep":
            return None
        app = self.w.controller
        chart = app.session.score_chart if card.kind == "formal" else app.chart
        if chart is None:
            return None
        for target in chart.targets:
            duration = float(target.duration_beats or 1) * chart.beat_seconds
            if target.time < card.time - 1e-9 and target.time + duration > card.time + 1e-9:
                return target
        return None

    def _draw_sustain_row(self, x, y, width, height, color):
        bar_y = y + height * .64
        self.w._rounded_fill((x + 24, bar_y - 3, width - 48, 6), dim(color, .18), 3)
        self._ellipse(x + 28, bar_y, 18, 12, color, outline=True)
        self._line(x + 40, bar_y, x + width - 26, bar_y, color, 3)

    def _draw_card(self, card, y, current, state, bottom=False, sustain=None, sustain_state=None):
        x = GRID_X + card.beat * (CARD_W + CARD_GAP)
        enter = min(1.0, (self._now - self._bar_changed_at) / .32)
        if enter < 1:
            x += (1 - enter) * 24
        bob = math.sin(self._now * 1.5 + card.beat * .8) * 2.2
        y += bob
        flash_age = self._now - self._flash.get((card.kind, card.bar, card.beat), -10)
        scale = 1.0
        if flash_age < .35:
            scale = 1.0 + .06 * (1 - flash_age / .35)
        # 音符本身始终用同一墨色；只用方块底色/描边表达“当前、正确、错误、延音”。
        note_color = INK
        has_targets = bool(card.targets)
        has_events = bool(card.targets or card.rests)
        sustain_hit = sustain is not None and sustain_state == "hit"
        sustain_miss = sustain is not None and sustain_state == "miss"
        if state == "hit":
            fill, border = (228, 248, 232), JADE
        elif state == "miss":
            fill, border = (250, 224, 218), CORAL
        elif state == "partial":
            fill, border = (255, 240, 214), GOLD
        elif sustain_hit and not has_targets:
            fill, border = (232, 249, 235), JADE
        elif sustain_miss and not has_targets:
            fill, border = (250, 224, 218), CORAL
        elif current:
            fill, border = (255, 238, 208), GOLD
        else:
            fill, border = CREAM, (236, 194, 148)
        highlight = (JADE if state == "hit" or (sustain_hit and not has_targets)
                     else CORAL if state == "miss" or (sustain_miss and not has_targets)
                     else GOLD)
        if bottom:
            fill = mix(fill, (126, 86, 62), .16)
            border = mix(border, (126, 86, 62), .2)
        elif card.kind == "preview":
            fill, border = dim(fill, .12), dim(border, .16)
        width, height = CARD_W * scale, CARD_H * scale
        left = x + (CARD_W - width) / 2
        top = y + (CARD_H - height) / 2
        if current and not bottom:
            pulse = .5 + .5 * math.sin(self._now * 6.2)
            self.w._rounded_fill((left - 12 - pulse * 4, top - 12 - pulse * 4,
                                  width + 24 + pulse * 8, height + 24 + pulse * 8),
                                 mix(NIGHT_3, highlight, .55), 30)
            self.w._rounded_fill((left - 7, top - 7, width + 14, height + 14), highlight, 28)
        self.w._rounded_fill((left + 5, top + 8, width, height), (10, 5, 9), 24)
        self.w._rounded_fill((left - 4, top - 4, width + 8, height + 8), border, 26)
        self.w._rounded_fill((left, top, width, height), fill, 22)
        self.w._rounded_fill((left + 8, top + 8, width - 16, 26), mix(fill, (255, 255, 255), .16), 10)
        self._text(("hp-beat", card.kind, card.bar, card.beat), str(card.beat + 1),
                   left + 12, top + 10, 10, dim(note_color, .45))
        if card.beat == 0:
            self._flame(left + width - 22, top + 22, 15, GOLD)
        if sustain is not None:
            self._draw_sustain_row(left, top, width, height * (.58 if has_targets else 1),
                                   highlight if (sustain_hit or sustain_miss) else dim(GOLD, .15))
        if has_events:
            self._draw_note_row(card, left, top, width, height, note_color)
        elif sustain is None:
            self._draw_note_row(card, left, top, width, height, note_color)
        else:
            self._text(("hp-hold", card.kind, card.bar, card.beat), "HOLD",
                       left + width / 2, top + height - 19, 9, highlight, anchor_x="center")
        if has_events and state == "hit":
            self.w._circle(left + width - 20, top + height - 20, 12, JADE)
            self._line(left + width - 26, top + height - 20, left + width - 21,
                       top + height - 15, CREAM, 2.5)
            self._line(left + width - 21, top + height - 15, left + width - 13,
                       top + height - 26, CREAM, 2.5)
        elif has_events and state == "miss":
            self.w._circle(left + width - 20, top + height - 20, 12, CORAL)
            self._line(left + width - 25, top + height - 25, left + width - 14,
                       top + height - 14, CREAM, 2.5)
            self._line(left + width - 25, top + height - 14, left + width - 14,
                       top + height - 25, CREAM, 2.5)

    def _draw_grid(self):
        self.w._card(PANEL_RECT, NIGHT_2, radius=30, border=(120, 72, 52))
        self.w._rounded_fill((340, 170, 700, 350), NIGHT_3, 24)
        self._line(352, 348, 1028, 348, (120, 72, 52), 1)
        self._text("hp-top-label", self._top_label, GRID_X + GRID_W, 164, 11, GOLD,
                   anchor_x="right")
        finished = self.w.controller.session.finished
        self._text("hp-bottom-label", "FINISH" if not self._bottom else "UP NEXT",
                   GRID_X + GRID_W, 342, 11, MUTED, anchor_x="right")
        session = self.w.controller.session
        for index, card in enumerate(self._top):
            sustain = self._sustain_target(card)
            sustain_state = session.states.get(sustain.index) if sustain is not None and card.kind == "formal" else None
            self._draw_card(card, GRID_Y, index == self._current and not finished,
                            card_state(card, session), sustain=sustain,
                            sustain_state=sustain_state)
        if self._bottom:
            for card in self._bottom:
                sustain = self._sustain_target(card)
                sustain_state = session.states.get(sustain.index) if sustain is not None and card.kind == "formal" else None
                self._draw_card(card, BOTTOM_Y, False, card_state(card, session),
                                bottom=True, sustain=sustain, sustain_state=sustain_state)
        else:
            self.w._card((GRID_X, BOTTOM_Y, GRID_W, CARD_H), NIGHT_2, radius=22,
                         border=(96, 60, 48))
            self._text("hp-finish", "LAST BAR · FINISH", GRID_X + GRID_W / 2, BOTTOM_Y + 52,
                       18, dim(GOLD, .1), anchor_x="center")
            self._flame(GRID_X + GRID_W / 2, BOTTOM_Y + 108, 22, dim(GOLD, .2))
        self._flame(640, 348, 16, GOLD)

    # ---------- HUD ----------

    def _draw_header(self):
        self._draw_sticker("pot", 54, 18, 50, 44, -3)
        self._text("hp-title", "MUSIC HOTPOT", 118, 14, 30, CREAM)
        self._text("hp-subtitle", "RHYTHM CAULDRON · 节奏火锅", 120, 52, 10, GOLD)
        track = self.w.controller.config.music_path.name
        self._text("hp-track", track, 120, 74, 11, MUTED, max_width=760)
        bpm = self.w.controller.config.bpm
        self._pill((944, 22, 146, 44), NIGHT_2, (128, 82, 56), radius=22)
        self.w._circle(968, 44, 5, GOLD)
        self._line(968, 50, 968, 33, GOLD, 2)
        self._text("hp-bpm", f"{bpm:g} BPM", 1038, 32, 16, GOLD, anchor_x="center")
        self._pill(self.w._settings_button, NIGHT_2, (128, 82, 56), radius=22)
        self._text("hp-settings", "设置", self.w._settings_button[0] + 52,
                   self.w._settings_button[1] + 12, 14, CREAM, anchor_x="center")

    def _draw_left_panel(self):
        self.w._card((56, 160, 250, 370), NIGHT_2, radius=26, border=(112, 68, 50))
        self.w._card((70, 174, 222, 58), CORAL, radius=20, border=(255, 214, 170))
        self._text("hp-player", "PLAYER A", 88, 184, 18, NIGHT)
        self._text("hp-player-sub", "HOTPOT CHEF", 89, 209, 9, (90, 30, 26))
        combo = self._combo
        pop = 1 + .22 * math.exp(-(self._now - self._combo_pop) * 10) if self._combo_pop else 1
        self._text("hp-combo", f"x{combo}", 78, 246, 48 * pop, GOLD if combo else MUTED)
        self._text("hp-combo-label", "COMBO", 82, 306, 10, MUTED)
        self._line(76, 332, 286, 332, (112, 68, 50), 1)
        session = self.w.controller.session
        rows = [
            ("HITS", session.hits, MINT),
            ("MISS", session.misses, CORAL if session.misses else MUTED),
            ("ACCURACY", f"{session.accuracy:.0%}" if session.accuracy is not None else "—", GOLD),
            ("ERRORS", session.mistakes, PINK if session.mistakes else MUTED),
        ]
        for index, (label, value, color) in enumerate(rows):
            y = 348 + index * 36
            self._text(("hp-stat-label", index), label, 80, y, 10, MUTED)
            self._text(("hp-stat-value", index), value, 286, y - 3, 18, color, anchor_x="right")
        self._text("hp-chef-hint", "A 键 · 拍掌", 80, 486, 10, MUTED)

    def _draw_right_panel(self):
        self.w._card((1050, 160, 174, 370), NIGHT_2, radius=26, border=(112, 68, 50))
        session = self.w.controller.session
        app = self.w.controller
        total = session.round_game_bars or app.config.game_bars
        if total <= 0:
            total = max_bar(session.score_chart) if session.score_chart is not None else 0
        if session.entry_time is not None and self._position >= session.entry_time and total:
            bar = min(total, max(1, int((self._position - session.entry_time) /
                                        app.chart.bar_seconds) + 1))
        else:
            bar = 0
        self._text("hp-round-label", "ROUND", 1066, 176, 10, MUTED)
        if session.entry_time is not None and self._position < session.entry_time:
            round_text = "PREP"
        elif bar and total:
            round_text = f"{bar}/{total}"
        elif app.config.game_bars == 0:
            round_text = "AUTO"
        else:
            round_text = "—"
        self._text("hp-round", round_text, 1210, 172, 16, GOLD, anchor_x="right")
        self._text("hp-progress-label", "PROGRESS", 1066, 206, 9, MUTED)
        self.w._rounded_fill((1066, 224, 142, 12), NIGHT, 6)
        if session.entry_time is not None and self._position < session.entry_time:
            if session.preparation_start_time is not None:
                if self._position >= session.preparation_start_time:
                    progress = (self._position - session.preparation_start_time) / max(
                        .001, session.entry_time - session.preparation_start_time)
                elif session.trigger_time is not None:
                    progress = (self._position - session.trigger_time) / max(
                        .001, session.preparation_start_time - session.trigger_time)
                else:
                    progress = 0.0
            else:
                progress = 0.0
        elif session.entry_time is not None and session.round_end_time is not None:
            progress = (self._position - session.entry_time) / max(
                .001, session.round_end_time - session.entry_time)
        else:
            progress = 0.0
        progress = max(0.0, min(1.0, progress))
        if progress > 0:
            self.w._rounded_fill((1066, 224, 142 * progress, 12), GOLD, 6)
            self.w._circle(1066 + 142 * progress, 230, 5, CORAL)
        self._text("hp-heat-label", "HEAT", 1066, 250, 10, MUTED)
        heat = min(5, (self._combo + 1) // 2)
        for index in range(5):
            color = CORAL if index < heat else NIGHT_3
            self.w._rounded_fill((1066 + index * 29, 270, 23, 12), color, 6)
        self._text("hp-input-label", "INPUT", 1066, 318, 10, MUTED)
        source = app.clap_input
        if source is None:
            status, color, detail = "KEYBOARD", MUTED, "空格备用"
        else:
            mapping = {"connected": ("LIVE", JADE), "waiting": ("WAIT", GOLD),
                       "starting": ("SYNC", GOLD), "error": ("ERROR", CORAL),
                       "stopped": ("OFF", MUTED)}
            status, color = mapping.get(source.status, ("—", MUTED))
            detail = "JOY-CON"
        self.w._circle(1074, 348, 5, color)
        self._text("hp-input-status", status, 1086, 338, 12, color)
        self._text("hp-input-detail", detail, 1066, 366, 10, MUTED)
        if app.clap_input is not None and app.clap_input.status != "connected":
            self._text("hp-input-hint", "运行发送器", 1066, 390, 9, MUTED)
        latency = app.engine.latency_ms
        if latency > 0:
            self._text("hp-output-label", "OUTPUT", 1066, 414, 9, MUTED)
            color = CORAL if latency > 30 else MUTED
            self._text("hp-latency", f"{latency:.0f} ms", 1066, 430, 12, color)
            if latency > 30:
                self._text("hp-latency-hint", "建议有线", 1066, 452, 9, CORAL)
        self._text("hp-space-hint", "SPACE 备用", 1210, 492, 9, MUTED, anchor_x="right")

    def _draw_feedback(self):
        app = self.w.controller
        if app.paused or app.session.finished:
            return
        title, detail, _ = app.feedback
        if app.paused:
            title, detail = "已暂停", "按 P 继续"
        elif not app.desired_playing and app.session.entry_time is None:
            title, detail = "准备就绪", "按 ENTER 开锅 · 拍手也可以启动"
        elif app.session.failed:
            title, detail = "本轮失败", "拍手再来"
        elif app.session.finished:
            title, detail = "本轮完成", "拍手再来"
        if (not app.paused and app.desired_playing and not app.session.finished
                and self._event_text is not None and self._now - self._event_time < .48):
            label = self._event_text
            color = self._event_color
            detail = self._event_detail
            age = max(0.0, self._now - self._event_time)
        else:
            label = JUDGE_TEXT.get(title, title)
            if title == "命中":
                error = self._error_ms()
                if error is not None:
                    label = "PERFECT!" if abs(error) <= 35 else "HIT!"
            color = JUDGE_COLOR.get(title, CREAM_2)
            age = max(0.0, self._now - app.feedback_time)
        pop = 1 + .16 * math.exp(-age * 12)
        shake = math.sin(age * 55) * 4 * math.exp(-age * 8) if color in (CORAL, PINK) else 0
        self._text("hp-feedback-shadow", label, 642 + shake, 110, 28 * pop, NIGHT)
        self._text("hp-feedback", label, 640 + shake, 108, 28 * pop, color)
        if detail:
            self._text("hp-feedback-detail", detail, 640, 143, 12, MUTED,
                       anchor_x="center", max_width=680)

    def _draw_footer(self):
        self.w._card((56, 720, 1168, 58), NIGHT_2, radius=24, border=(112, 68, 50))
        app = self.w.controller
        for rect, label, action, primary in self.w._game_buttons():
            if action == "start":
                label = "重播" if app.desired_playing or app.paused else "播放"
            elif action == "toggle_pause":
                label = "继续" if app.paused else "暂停"
            fill = CORAL if action == "start" else GOLD if action == "toggle_pause" else CREAM
            text_color = NIGHT
            self._pill(rect, fill, mix(fill, CREAM, .45), radius=18)
            cx, cy = rect[0] + 26, rect[1] + 22
            if action == "start":
                self._triangle(cx, cy, 8, text_color)
            elif action == "toggle_pause":
                self._rect((cx - 7, cy - 8, 5, 16), text_color)
                self._rect((cx + 2, cy - 8, 5, 16), text_color)
            else:
                self._reset_icon(cx, cy, 8, text_color)
            self._text(("hp-button", action), label, rect[0] + 46, rect[1] + 12, 15, text_color)
        available = {action for _, _, action, _ in self.w._game_buttons()}
        for rect, label, action, primary in self.w._buttons:
            if action in available:
                continue
            if action == "start":
                label = "播放"
            elif action == "toggle_pause":
                label = "暂停"
            elif action == "reset_round":
                label = "重置"
            self._pill(rect, (62, 46, 42), (98, 70, 56), radius=18)
            self._text(("hp-button-ghost", action), label, rect[0] + 46, rect[1] + 12, 15,
                       (150, 120, 108))
        hint = "SPACE 备用 · A 拍掌" if app.clap_input is not None else "SPACE · 拍手"
        self._text("hp-footer-hint", hint, 1210, 744, 11, MUTED, anchor_x="right")

    def _triangle(self, x, y, size, color):
        sx, sy = self.w._point(x, y)
        s = size * self.w._scale
        arcade.draw_triangle_filled(sx - s * .4, sy - s, sx - s * .4, sy + s, sx + s, sy, color)

    def _reset_icon(self, x, y, size, color):
        sx, sy = self.w._point(x, y)
        s = size * self.w._scale
        arcade.draw_arc_filled(sx, sy, s * 2, s * 2, color, 35, 320, num_segments=24)
        arcade.draw_triangle_filled(sx + s * .7, sy + s * .9, sx + s * .2, sy + s * .9,
                                    sx + s * .55, sy + s * .3, color)

    # ---------- 特效与覆盖层 ----------

    def _draw_effects(self):
        for ring in self._rings:
            progress = ring.age / ring.life
            radius = ring.max_radius * progress
            alpha = int(190 * (1 - progress))
            if alpha <= 0:
                continue
            sx, sy = self.w._point(ring.x, ring.y)
            arcade.draw_circle_outline(sx, sy, self._s(radius), (*ring.color, alpha),
                                       border_width=max(1, self._s(ring.width)))
        for particle in self._particles:
            progress = particle.age / particle.life
            alpha = int(255 * (1 - progress))
            if alpha <= 0:
                continue
            sx, sy = self.w._point(particle.x, particle.y)
            size = self._s(particle.size)
            if particle.circle:
                arcade.draw_circle_filled(sx, sy, size * .55, (*particle.color, alpha))
            else:
                rect = arcade.LBWH(sx - size / 2, sy - size / 2, size, size)
                arcade.draw_rect_filled(rect, (*particle.color, alpha), tilt_angle=particle.rot)
        for food in self._foods:
            pop = min(1.0, food.age / .14)
            size = food.size * (0.55 + 0.45 * pop)
            sx, sy = self.w._point(food.x, food.y)
            # 只画食材本身，不加盘子或圆形描边；左上角留一点极淡的落影增加体积感。
            arcade.draw_ellipse_filled(sx + 4 * self.w._scale, sy - 8 * self.w._scale,
                                       size * .72 * self.w._scale, size * .18 * self.w._scale,
                                       (10, 5, 9, 38))
            texture = self.textures.get(food.texture)
            if texture is not None:
                max_px = size * self.w._scale
                ratio = max_px / max(texture.width, texture.height)
                width_px = texture.width * ratio
                height_px = texture.height * ratio
                rect = arcade.LBWH(sx - width_px / 2, sy - height_px / 2,
                                   width_px, height_px)
                arcade.draw_texture_rect(texture, rect, angle=food.angle)
            else:
                arcade.draw_circle_filled(sx, sy, size * .35 * self.w._scale, CORAL)

    def _draw_overlays(self):
        app = self.w.controller
        session = app.session
        if app.paused:
            self._rect((0, 0, 1280, 800), (10, 5, 9, 170))
            self._pill((440, 320, 400, 130), NIGHT_2, GOLD, radius=26)
            self._text("hp-pause-title", "PAUSED", 640, 342, 34, GOLD, anchor_x="center")
            self._text("hp-pause-sub", "按 P 继续", 640, 394, 14, MUTED, anchor_x="center")
            return
        if not app.desired_playing and session.entry_time is None:
            self._pill((430, 536, 420, 50), NIGHT_2, CORAL, radius=22)
            self._text("hp-start-title", "按 ENTER 开锅", 640, 545, 18, CREAM, anchor_x="center")
            self._text("hp-start-sub", "空格 / A 也可以启动", 640, 569, 11, GOLD, anchor_x="center")
        if session.finished:
            success = not session.failed
            self._pill((390, 378, 500, 120), NIGHT_2, MINT if success else CORAL, radius=26)
            self._text("hp-finish-title", "CLEAR!" if success else "FAILED",
                       640, 392, 32, MINT if success else CORAL, anchor_x="center")
            detail = (f"命中 {session.hits} · 漏拍 {session.misses} · 错误 {session.mistakes}"
                      if success else f"本轮结束 · 命中 {session.hits} · 错误 {session.mistakes}")
            self._text("hp-finish-detail", detail, 640, 438, 13, CREAM_2, anchor_x="center")
            self._text("hp-finish-hint", "拍手再来", 640, 468, 11, MUTED, anchor_x="center")

    def draw(self):
        app = self.w.controller
        self._draw_background()
        self._draw_decorations()
        self._draw_grid()
        self._draw_header()
        self._draw_left_panel()
        self._draw_right_panel()
        self._draw_feedback()
        self._draw_footer()
        self._draw_effects()
        self._draw_overlays()
        _ = app
