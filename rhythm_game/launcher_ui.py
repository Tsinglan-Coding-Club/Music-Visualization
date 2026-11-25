"""传送带发射台舞台：火锅在左，食材从右侧传送带滑向发射线，拍手把食材弹进锅心。

只负责画面呈现，不改变音频、判定或输入；判定事件沿用 HotpotStage 的控制器接口。
判定隐喻：落点距锅心的远近＝时间误差大小，早＝欠火候落在右侧，晚＝飞过锅心偏左。
所有坐标沿用 1280×800 设计坐标（y 向下）。
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import arcade

from .hotpot_ui import (
    BROTH, CORAL, CREAM, CREAM_2, CREAM_3, GOLD, JADE, JUDGE_COLOR, JUDGE_TEXT,
    MINT, MUTED, NIGHT_2, NIGHT_3, PINK, PLUM, HotpotStage, Ring, dim, max_bar, mix,
)

# 布局（设计坐标，y 向下）
POT_CX, POT_CY = 300, 460
POT_R = 196
BROTH_RX, BROTH_RY = 150, 102
BROTH_CY = POT_CY - 6
PERFECT_RX, PERFECT_RY = 34, 23
LAUNCH_X = 540
BELT_RECT = (540, 360, 716, 152)
BELT_ITEM_Y = 444
FOOD_KINDS = ("tomato", "meat", "mushroom", "cabbage", "tofu")
PERFECT_MS = 35.0


@dataclass
class Flying:
    kind: str
    size: float
    x0: float
    y0: float
    x1: float
    y1: float
    arc: float
    duration: float
    age: float
    color: tuple
    perfect: bool


@dataclass
class Landed:
    kind: str
    size: float
    x: float
    y: float
    color: tuple
    perfect: bool
    age: float
    life: float


@dataclass
class Dropped:
    kind: str
    size: float
    x: float
    y: float
    vy: float
    age: float
    life: float


@dataclass
class Splat:
    x: float
    y: float
    color: tuple
    age: float
    life: float
    seed: float


class LauncherStage(HotpotStage):
    """传送带＋发射台呈现；判定、配置、反馈文本全部复用 HotpotStage 的基类逻辑。"""

    def __init__(self, window):
        super().__init__(window)
        self._flying: list[Flying] = []
        self._landed: list[Landed] = []
        self._dropped: list[Dropped] = []
        self._splats: list[Splat] = []
        self._flick_at = -10.0
        self._speed = 150.0
        self._ghosts: list[list] = []      # 开播／启动瞬间淡出的旧传送带内容
        self._belt_snapshot: list[tuple] = ()
        self._prev_mode = None

    # ---------- 状态与动画 ----------

    def _reset_visual(self, session):
        super()._reset_visual(session)
        self._flying = []
        self._landed = []
        self._dropped = []
        self._splats = []
        self._flick_at = -10.0

    def update(self, delta_time):
        super().update(delta_time)
        dt = min(max(delta_time, 0.0), 0.05)
        app = self.w.controller
        lookahead = max(0.5, float(app.config.lookahead_seconds))
        # 带速比“刚好容纳 lookahead”快约 1/3，降低同屏密度
        self._speed = (BELT_RECT[0] + BELT_RECT[2] - 26 - LAUNCH_X) / max(1.2, lookahead - 0.8)
        # 待机预览盘 → 正式时间线切换时，旧内容作为残影淡出，避免硬切
        mode = (app.desired_playing, app.session.entry_time is not None)
        if self._prev_mode is not None and mode != self._prev_mode and self._belt_snapshot:
            self._ghosts = [[x, y, kind, size, 0.0]
                            for x, y, kind, size in self._belt_snapshot]
        self._prev_mode = mode
        for ghost in self._ghosts:
            ghost[4] += dt
        self._ghosts = [ghost for ghost in self._ghosts if ghost[4] < 0.6]
        for item in self._flying:
            item.age += dt
        landed = [item for item in self._flying if item.age >= item.duration]
        self._flying = [item for item in self._flying if item.age < item.duration]
        for item in landed:
            self._land(item)
        for drop in self._dropped:
            drop_dt = dt
            drop_age = drop.age + drop_dt
            drop.vy += 1900 * drop_dt
            drop.y += drop.vy * drop_dt
            drop.age = drop_age
        self._dropped = [drop for drop in self._dropped if drop.age < drop.life]
        for item in self._landed:
            item.age += dt
        self._landed = [item for item in self._landed if item.age < item.life]
        for splat in self._splats:
            splat.age += dt
        self._splats = [splat for splat in self._splats if splat.age < splat.life]

    # ---------- 判定事件 ----------

    def _window_ms(self):
        return max(1.0, float(self.w.controller.config.window_ms))

    def _belt_x(self, time, position):
        return LAUNCH_X + (time - position) * self._speed

    def _find_target(self, app, index, prep):
        session = app.session
        if prep:
            pool = session.preparation_targets
        elif session.score_chart is not None:
            pool = session.score_chart.targets
        else:
            pool = app.chart.targets
        for target in pool:
            if target.index == index:
                return target
        return None

    def _food_for(self, target):
        kind = FOOD_KINDS[target.index % len(FOOD_KINDS)]
        if target.duration_beats is not None:
            beats = float(target.duration_beats)
        else:
            beats = 4.0 / max(1, target.denominator) * (2 - 0.5 ** target.dots)
        size = 34 * min(1.8, max(0.62, math.sqrt(max(0.2, beats))))
        return kind, size

    def _landing(self, error, prep):
        """误差→落点：中心为 PERFECT，早落右侧（欠），晚落左侧（过）。"""
        if error is None:
            radius = self.rng.uniform(4, 26)
            angle = self.rng.uniform(0, math.tau)
        else:
            frac = min(1.0, abs(error) / self._window_ms())
            radius = 6 + frac * (BROTH_RX - 34)
            base = math.pi if error > 0 else 0.0
            angle = base + self.rng.uniform(-0.5, 0.5)
        return (POT_CX + radius * math.cos(angle),
                BROTH_CY + radius * (BROTH_RY / BROTH_RX) * math.sin(angle))

    def _launch(self, target, error, prep, color, perfect):
        x0 = self._belt_x(target.time, self._position) if target is not None else LAUNCH_X
        x0 = min(max(x0, LAUNCH_X - 60), LAUNCH_X + 40)
        kind, size = self._food_for(target) if target is not None else ("tomato", 34)
        x1, y1 = self._landing(error, prep)
        distance = math.hypot(x1 - x0, y1 - BELT_ITEM_Y)
        self._flick_at = self._now
        # 发射瞬间：冲环＋向后喷的蒸汽粒子，让“弹出去”有重量感
        self._rings.append(Ring(x0, BELT_ITEM_Y, color, 46, .3, 0, 2.5))
        self._spawn_particles(x0, BELT_ITEM_Y, 16 if perfect else 10,
                              (GOLD, CREAM_2, color), 170)
        self._flying.append(Flying(
            kind=kind, size=size, x0=x0, y0=BELT_ITEM_Y, x1=x1, y1=y1,
            arc=150 + distance * 0.12, duration=0.4 + distance / 1500,
            age=0.0, color=color, perfect=perfect,
        ))

    def _on_hit(self, index, prep):
        target = self._find_target(self.w.controller, index, prep)
        error = None if prep else self._error_ms()
        if prep:
            label, color, perfect = "PREP HIT!", JADE, False
        else:
            self._combo += 1
            self._combo_pop = self._now
            perfect = error is not None and abs(error) <= PERFECT_MS
            label = "PERFECT!" if perfect else "HIT!"
            color = GOLD if perfect else MINT
        self._launch(target, error, prep, color, perfect)
        self._event_text = label
        self._event_color = color
        self._event_detail = ("ON BEAT" if error is not None and abs(error) < .05
                              else f"{error:+.1f} ms" if error is not None else "")
        self._event_time = self._now

    def _on_miss(self, index, prep):
        target = self._find_target(self.w.controller, index, prep)
        self._combo = 0
        kind, size = self._food_for(target) if target is not None else ("meat", 34)
        x = self._belt_x(target.time, self._position) if target is not None else LAUNCH_X - 30
        x = min(max(x, LAUNCH_X - 70), LAUNCH_X + 30)
        self._dropped.append(Dropped(kind, size, x, BELT_ITEM_Y, 60.0, 0.0, 0.9))
        self._rings.append(Ring(x, BELT_ITEM_Y, CORAL, 52, .4, 0, 3))
        self._spawn_particles(x, BELT_ITEM_Y + 6, 8, (CORAL, PLUM, MUTED), 110)
        self._event_text, self._event_color, self._event_detail = "MISS", CORAL, "漏拍 · 掉下去了"
        self._event_time = self._now

    def _feedback_effect(self, title):
        """抢拍／晚拍／重复／休止敲击：食材砸在锅沿上。早＝右沿，晚＝左沿。"""
        label = JUDGE_TEXT.get(title, title)
        color = JUDGE_COLOR.get(title, MUTED)
        self._event_text, self._event_color = label, color
        self._event_detail = self.w.controller.feedback[1]
        self._event_time = self._now
        self._flick_at = self._now
        if title in ("太早了", "跟拍预备"):
            angle = self.rng.uniform(-0.6, 0.6)
        elif title == "太晚了":
            angle = math.pi + self.rng.uniform(-0.6, 0.6)
        else:
            angle = self.rng.uniform(0, math.tau)
        x = POT_CX + (POT_R * 0.92 + 10) * math.cos(angle)
        y = POT_CY + (POT_R * 0.62 + 10) * math.sin(angle)
        self._splats.append(Splat(x, y, CORAL, 0.0, 1.6, self.rng.uniform(0, 10)))
        self._spawn_particles(x, y, 10, (CORAL, PLUM, CREAM_3), 140)

    def _land(self, item):
        self._landed.append(Landed(item.kind, item.size, item.x1, item.y1,
                                   item.color, item.perfect, 0.0, 3.4))
        if len(self._landed) > 24:
            self._landed.pop(0)
        self._rings.append(Ring(item.x1, item.y1, item.color,
                                92 if item.perfect else 58, .5, 0, 4 if item.perfect else 2.5))
        colors = (GOLD, CREAM, CORAL, MINT) if item.perfect else (CREAM_2, item.color, CREAM)
        self._spawn_particles(item.x1, item.y1, 26 if item.perfect else 12,
                              colors, 210 if item.perfect else 130)

    def _spawn_result(self, success):
        colors = (GOLD, JADE, CORAL, CREAM, MINT, PINK) if success else (CORAL, PLUM, MUTED, CREAM_3)
        self._spawn_particles(POT_CX, BROTH_CY - 30, 110 if success else 50, colors, 430)
        self._rings.append(Ring(POT_CX, BROTH_CY, GOLD if success else CORAL, 240, .9, 0, 5))

    # ---------- 时间线 ----------

    def _timeline(self, app, position):
        """当前应出现在传送带上的 (target, kind)、休止符与拍点所用的 chart。"""
        session = app.session
        if session.entry_time is None:
            chart = app.chart
            return ([(target, "preview") for target in chart.targets], list(chart.rests), chart)
        if position < session.entry_time:
            events = [(target, "prep") for target in session.preparation_targets]
            rests = []
            if session.score_chart is not None:
                end = session.round_end_time
                events += [(t, "formal") for t in session.score_chart.targets
                           if end is None or t.time < end]
                rests += [r for r in session.score_chart.rests if end is None or r.time < end]
            return events, rests, session.chart
        chart = session.score_chart or app.chart
        end = session.round_end_time
        events = [(t, "formal") for t in chart.targets if end is None or t.time < end]
        rests = [r for r in chart.rests if end is None or r.time < end]
        return events, rests, chart

    def _belt_position(self, app, position, chart):
        """未开播时把首个目标预摆在传送带中段，便于预览谱面。"""
        if app.session.entry_time is None and not app.desired_playing:
            return chart.start - 1.6
        return position

    # ---------- 食材造型 ----------

    def _draw_food(self, kind, x, y, size, alpha=255):
        s = size
        if kind == "tomato":
            self._ellipse(x, y, s, s * .92, (222, 66, 48, alpha))
            self._ellipse(x - s * .18, y - s * .2, s * .32, s * .2, (255, 130, 108, alpha))
            self._ellipse(x, y - s * .46, s * .3, s * .14, (74, 148, 84, alpha))
        elif kind == "meat":
            self.w._rounded_fill((x - s * .55, y - s * .32, s * 1.1, s * .64),
                                 (238, 120, 118, alpha), s * .28)
            self._line(x - s * .3, y - s * .06, x + s * .3, y - s * .13, (250, 190, 180, alpha), 3)
            self._line(x - s * .28, y + s * .15, x + s * .26, y + s * .08, (250, 190, 180, alpha), 2)
        elif kind == "mushroom":
            self.w._rounded_fill((x - s * .16, y - s * .05, s * .32, s * .5),
                                 (246, 232, 205, alpha), s * .12)
            self._ellipse(x, y - s * .08, s * 1.05, s * .62, (168, 106, 66, alpha))
            self._ellipse(x - s * .2, y - s * .2, s * .3, s * .16, (208, 150, 104, alpha))
        elif kind == "cabbage":
            self._ellipse(x, y, s, s * .92, (96, 172, 96, alpha))
            self._ellipse(x, y, s * .62, s * .55, (150, 205, 130, alpha))
            self._line(x - s * .3, y, x + s * .3, y, (110, 170, 95, alpha), 2)
        else:  # tofu
            self.w._rounded_fill((x - s * .45, y - s * .38, s * .9, s * .76),
                                 (250, 240, 214, alpha), s * .16)
            self._ellipse(x + s * .12, y - s * .1, s * .2, s * .14, (232, 120, 90, alpha))

    # ---------- 场景绘制 ----------

    def _draw_background(self):
        """与基类一致的夜色与火星，但蒸汽留给锅自己画，避免浮在传送带上方。"""
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
            glow_left, glow_bottom = self.w._point(120, 780)
            rect = arcade.LBWH(glow_left, glow_bottom,
                               1040 * self.w._scale, 760 * self.w._scale)
            arcade.draw_texture_rect(self.glow, rect)
        for gy in range(8):
            for gx in range(13):
                self.w._circle(90 + gx * 92, 80 + gy * 92, 1.2, (255, 196, 92, 12))
        for ex, ey, _speed, _phase, size, color in self._embers:
            self.w._circle(ex, ey, size, (*color, 130))
            self.w._circle(ex, ey, size * .45, (*CREAM, 180))

    def _draw_pot(self):
        cx, cy = POT_CX, POT_CY
        flicker = 3 * math.sin(self._now * 9)
        # 灶台与火
        self.w._rounded_fill((cx - 150, cy + POT_R * .7, 300, 48), NIGHT_3, 18)
        self._flame(cx - 62, cy + POT_R * .7 + 24, 24 + flicker, CORAL)
        self._flame(cx, cy + POT_R * .7 + 30, 32 + flicker, GOLD)
        self._flame(cx + 62, cy + POT_R * .7 + 24, 24 - flicker, CORAL)
        # 锅体
        self._ellipse(cx, cy + 16, POT_R * 2.06, POT_R * 1.52, (52, 26, 26))
        self._ellipse(cx, cy, POT_R * 2, POT_R * 1.4, (96, 50, 42))
        self._ellipse(cx, cy - 8, POT_R * 1.86, POT_R * 1.24, (58, 30, 30))
        # 汤与靶心
        self._ellipse(cx, BROTH_CY, BROTH_RX * 2, BROTH_RY * 2, BROTH)
        self._ellipse(cx - 22, BROTH_CY - 16, BROTH_RX * 1.1, BROTH_RY * .68, (232, 96, 66))
        pulse = .5 + .5 * math.sin(self._now * 4)
        self._ellipse(cx, BROTH_CY, BROTH_RX * 1.34, BROTH_RY * 1.34, (*CREAM, 60), outline=True)
        self._ellipse(cx, BROTH_CY, PERFECT_RX * 2 * (1 + .07 * pulse),
                      PERFECT_RY * 2 * (1 + .07 * pulse), (*GOLD, 210), outline=True)
        # 锅里已落入的食材
        for item in self._landed:
            fade = 1 - (item.age / item.life) ** 2
            bob = math.sin(self._now * 2.2 + item.x * .05) * 2
            self._draw_food(item.kind, item.x, item.y + bob, item.size * .92,
                            alpha=int(255 * fade))
        # 锅沿上的砸痕
        for splat in self._splats:
            fade = 1 - splat.age / splat.life
            color = (*splat.color, int(200 * fade))
            for index in range(4):
                ang = splat.seed + index * 1.7
                r = 6 + (index % 3) * 4
                self._ellipse(splat.x + math.cos(ang) * 8, splat.y + math.sin(ang) * 5,
                              r, r * .7, color)
        # 蒸汽
        for index in range(4):
            phase = self._now * .6 + index * 1.5
            sx = cx - 50 + index * 34 + math.sin(phase) * 12
            sy = BROTH_CY - 70 - (self._now * 26 + index * 48) % 150
            fade = 1 - ((self._now * 26 + index * 48) % 150) / 150
            self._ellipse(sx, sy, 22 + index * 4, 10, (255, 235, 210, int(26 * fade)))

    def _draw_launcher(self):
        # 底座在传送带左端下方
        self.w._rounded_fill((LAUNCH_X - 64, 500, 44, 24), NIGHT_3, 10)
        self.w._rounded_fill((LAUNCH_X - 72, 520, 62, 10), (120, 72, 52), 5)
        # 双筷发射臂，从底座斜指向发射线，拍手瞬间上挑
        flick = math.exp(-max(0.0, self._now - self._flick_at) * 9)
        angle = -0.97 - 0.45 * flick
        px, py = LAUNCH_X - 46, 502
        tx, ty = px + 78 * math.cos(angle), py + 78 * math.sin(angle)
        self._line(px, py, tx, ty, CREAM_3, 5)
        self._line(px + 8, py + 3, tx + 8, ty + 3, CREAM_3, 5)
        self._ellipse(tx + 9, ty + 3, 28, 15, GOLD)

    def _draw_belt(self, app, position):
        x, y, width, height = BELT_RECT
        session = app.session
        events, rests, chart = self._timeline(app, position)
        belt_pos = self._belt_position(app, position, chart)
        # 外框与带面
        self.w._card(BELT_RECT, NIGHT_2, radius=26, border=(120, 72, 52))
        band = (x + 16, y + 50, width - 32, 66)
        self.w._rounded_fill(band, (64, 37, 33), 16)
        # 随音乐滚动的带面条纹
        stripe = 46
        sx = x + 14 - (belt_pos * self._speed) % stripe
        while sx < x + width - 14:
            self._line(sx, band[1] + 5, sx, band[1] + band[3] - 5, (86, 50, 44), 2)
            sx += stripe
        # 拍点刻度：小节线亮、拍线淡
        beat_s = chart.beat_seconds
        n0 = max(0, int((belt_pos - 0.5 - chart.start) / beat_s))
        n1 = int((belt_pos + (1320 - LAUNCH_X) / self._speed - chart.start) / beat_s) + 2
        for n in range(n0, n1):
            bx = self._belt_x(chart.start + n * beat_s, belt_pos)
            if bx < LAUNCH_X - 16 or bx > 1310:
                continue
            is_bar = n % chart.beats_per_bar == 0
            color = (*GOLD, 170) if is_bar else (150, 100, 84, 110)
            self._line(bx, band[1] + 2, bx, band[1] + band[3] - 2, color, 3 if is_bar else 1.5)
        # 休止符：淡圈提示“这里别拍”
        for rest in rests:
            if session.rest_states.get(rest.index) is not None:
                continue
            rx = self._belt_x(rest.time, belt_pos)
            if LAUNCH_X - 30 <= rx <= 1310:
                self._ellipse(rx, BELT_ITEM_Y, 20, 13, (*MUTED, 90), outline=True)
        # 旧盘残影：开播／启动瞬间向左滑出并淡出
        for gx, gy, gkind, gsize, gage in self._ghosts:
            fade = 1 - gage / 0.6
            self._draw_food(gkind, gx - 60 * gage, gy, gsize, alpha=int(200 * fade))
        # 食材
        snapshot = []
        for target, kind_name in events:
            states = session.preparation_states if kind_name == "prep" else session.states
            if states.get(target.index) is not None:
                continue
            bx = self._belt_x(target.time, belt_pos)
            if bx < LAUNCH_X - 80 or bx > 1320:
                continue
            kind, size = self._food_for(target)
            alpha = 255 if kind_name != "preview" else 170
            beats = float(target.duration_beats or 0)
            if beats > 1.05:  # 长音拖尾
                tail = (beats - 1) * beat_s * self._speed
                self.w._rounded_fill((bx, BELT_ITEM_Y + size * .3, tail, 8), (*GOLD, 110), 4)
            self._ellipse(bx, BELT_ITEM_Y + size * .52, size * .8, size * .2, (10, 5, 9, 70))
            if abs(bx - LAUNCH_X) < 30 and kind_name != "preview":
                ring_pulse = .5 + .5 * math.sin(self._now * 7)
                self._ellipse(bx, BELT_ITEM_Y, size * 1.5 + ring_pulse * 6,
                              size * 1.3 + ring_pulse * 5, (*GOLD, 170), outline=True)
            self._draw_food(kind, bx, BELT_ITEM_Y, size, alpha=alpha)
            snapshot.append((bx, BELT_ITEM_Y, kind, size))
            if kind_name == "prep":
                self._ellipse(bx, BELT_ITEM_Y + size * .62, size * .5, 5, (*JADE, 160))
        self._belt_snapshot = tuple(snapshot)
        # 发射线
        line_pulse = .5 + .5 * math.sin(self._now * 5)
        self.w._rounded_fill((LAUNCH_X - 4, y + 32, 8, height - 44),
                             (*GOLD, 150 + int(70 * line_pulse)), 4)
        self._text("ln-launch", "发射线", LAUNCH_X + 12, y + 16, 9, GOLD)
        # 段落标签
        if session.entry_time is not None and position < session.entry_time:
            self._text("ln-prep", "预备跟拍 PREP", x + width - 18, y + 16, 11, JADE,
                       anchor_x="right")
        elif session.entry_time is None:
            self._text("ln-preview", "谱面预览 PREVIEW", x + width - 18, y + 16, 11, MUTED,
                       anchor_x="right")

    def _draw_flying(self):
        for item in self._flying:
            p = min(1.0, item.age / item.duration)

            def arc_pos(q):
                return (item.x0 + (item.x1 - item.x0) * q,
                        item.y0 + (item.y1 - item.y0) * q - item.arc * 4 * q * (1 - q))

            x, y = arc_pos(p)
            # 多层残影沿弧线拖尾，越靠后越淡越小
            for k in range(4, 0, -1):
                q = p - k * 0.055
                if q <= 0.02:
                    continue
                gx, gy = arc_pos(q)
                fade = (1 - k / 5) * (0.55 if item.perfect else 0.4)
                trail_color = GOLD if item.perfect else item.color
                self._ellipse(gx, gy, item.size * .55 * (1 - k * .12),
                              item.size * .44 * (1 - k * .12), (*trail_color, int(160 * fade)))
            # 速度线：沿飞行方向拖一条短痕
            if 0.05 < p < 0.95:
                qx, qy = arc_pos(max(0.0, p - 0.06))
                self._line(qx, qy, x, y, (*CREAM, 90), 2)
            # 起跳时横向压扁拉伸，下落前恢复正常
            stretch = 1 + .3 * max(0.0, 1 - p * 3)
            wobble = 1 + .1 * math.sin(p * math.pi * 3)
            self._draw_food(item.kind, x, y, item.size * wobble * stretch)

    # ---------- HUD ----------

    def _draw_stats_card(self):
        self.w._card((48, 126, 250, 186), NIGHT_2, radius=22, border=(112, 68, 50))
        session = self.w.controller.session
        app = self.w.controller
        self._text("ln-player", "PLAYER A", 68, 140, 13, CORAL)
        combo = self._combo
        pop = 1 + .22 * math.exp(-(self._now - self._combo_pop) * 10) if self._combo_pop else 1
        self._text("ln-combo", f"x{combo}", 66, 160, 36 * pop, GOLD if combo else MUTED)
        self._text("ln-combo-label", "COMBO", 68, 206, 9, MUTED)
        acc = f"{session.accuracy:.0%}" if session.accuracy is not None else "—"
        self._text("ln-acc", acc, 280, 162, 22, GOLD, anchor_x="right")
        self._text("ln-acc-label", "ACCURACY", 280, 192, 9, MUTED, anchor_x="right")
        self._text("ln-hme", f"HIT {session.hits}  MISS {session.misses}  ERR {session.mistakes}",
                   68, 228, 11, MUTED)
        heat = min(5, (combo + 1) // 2)
        for index in range(5):
            color = CORAL if index < heat else NIGHT_3
            self.w._rounded_fill((68 + index * 27, 252, 21, 10), color, 5)
        # 进度条
        total = session.round_game_bars or app.config.game_bars
        if total <= 0:
            total = max_bar(session.score_chart) if session.score_chart is not None else 0
        if session.entry_time is not None and session.round_end_time is not None:
            progress = (self._position - session.entry_time) / max(
                .001, session.round_end_time - session.entry_time)
        else:
            progress = 0.0
        progress = max(0.0, min(1.0, progress))
        self.w._rounded_fill((68, 278, 210, 8), (20, 10, 14), 4)
        if progress > 0:
            self.w._rounded_fill((68, 278, 210 * progress, 8), GOLD, 4)
        round_text = f"{total} 小节" if total else "—"
        self._text("ln-round", round_text, 280, 290, 9, MUTED, anchor_x="right")

    def _draw_status_strip(self):
        """原右侧面板的输入／输出状态，压缩成传送带下方一行。"""
        app = self.w.controller
        source = app.clap_input
        if source is None:
            status, color = "KEYBOARD · 空格备用", MUTED
        else:
            mapping = {"connected": ("JOY-CON LIVE", JADE), "waiting": ("JOY-CON WAIT", GOLD),
                       "starting": ("JOY-CON SYNC", GOLD), "error": ("JOY-CON ERROR", CORAL),
                       "stopped": ("JOY-CON OFF", MUTED)}
            status, color = mapping.get(source.status, ("—", MUTED))
        self.w._circle(572, 532, 5, color)
        self._text("ln-input", status, 584, 524, 11, color)
        latency = app.engine.latency_ms
        if latency > 0:
            latency_color = CORAL if latency > 30 else MUTED
            hint = " · 建议有线" if latency > 30 else ""
            self._text("ln-latency", f"OUTPUT {latency:.0f} ms{hint}", 1244, 524, 11,
                       latency_color, anchor_x="right")

    def _draw_overlays(self):
        app = self.w.controller
        session = app.session
        if app.paused:
            self._rect((0, 0, 1280, 800), (10, 5, 9, 170))
            self._pill((POT_CX - 200, 300, 400, 130), NIGHT_2, GOLD, radius=26)
            self._text("ln-pause-title", "PAUSED", POT_CX, 322, 34, GOLD, anchor_x="center")
            self._text("ln-pause-sub", "按 P 继续", POT_CX, 374, 14, MUTED, anchor_x="center")
            return
        if not app.desired_playing and session.entry_time is None:
            self._pill((POT_CX - 190, 640, 380, 50), NIGHT_2, CORAL, radius=22)
            self._text("ln-start-title", "按 ENTER 开锅", POT_CX, 649, 17, CREAM,
                       anchor_x="center")
            self._text("ln-start-sub", "空格 / A 也可以启动", POT_CX, 671, 10, GOLD,
                       anchor_x="center")
        if session.finished:
            success = not session.failed
            self._pill((POT_CX - 230, 300, 460, 116), NIGHT_2, MINT if success else CORAL,
                       radius=26)
            self._text("ln-finish-title", "CLEAR!" if success else "FAILED",
                       POT_CX, 314, 30, MINT if success else CORAL, anchor_x="center")
            detail = (f"命中 {session.hits} · 漏拍 {session.misses} · 错误 {session.mistakes}"
                      if success else f"本轮结束 · 命中 {session.hits} · 错误 {session.mistakes}")
            self._text("ln-finish-detail", detail, POT_CX, 356, 13, CREAM_2, anchor_x="center")
            self._text("ln-finish-hint", "拍手再来", POT_CX, 384, 11, MUTED, anchor_x="center")

    def draw(self):
        self._draw_background()
        self._draw_header()
        self._draw_belt(self.w.controller, self._position)
        self._draw_pot()
        self._draw_launcher()
        self._draw_flying()
        self._draw_stats_card()
        self._draw_status_strip()
        self._draw_feedback()
        self._draw_footer()
        self._draw_effects()
        self._draw_overlays()
