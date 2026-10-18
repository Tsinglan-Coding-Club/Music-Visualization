"""五线谱布局和视口的软件检查，不依赖窗口、音频或真实手套。"""

from dataclasses import replace
from fractions import Fraction
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch

from rhythm_game.core import RhythmChart, RhythmSession
from rhythm_game.notation import parse_note
from rhythm_game.staff import Segment, build_staff_layout, clip_segment, draw_staff, layout_bar, note_status


def fake_chart(patterns=((4, 8, 8, 4),), beats=3, duration=60, start=4):
    targets = []
    beat_seconds = 0.5
    for bar in range(1, 40):
        offset = Fraction()
        for value in patterns[(bar - 1) % len(patterns)]:
            note = parse_note(value)
            time = start + ((bar - 1) * beats + float(offset)) * beat_seconds
            if time < duration:
                targets.append(SimpleNamespace(index=len(targets), bar=bar, denominator=note.denominator,
                                               dots=note.dots, beat_offset=offset, time=time))
            offset += note.beats
    return SimpleNamespace(patterns=patterns, note_values=patterns[0], beats_per_bar=beats,
                           start=start, beat_seconds=beat_seconds, bar_seconds=beats * beat_seconds,
                           duration_seconds=duration,
                           targets_for_bar=lambda bar: [target for target in targets if target.bar == bar])


def projected_session(entry=10, patterns=((8, 16, 16, 4, 4), ("8.", 16, 4, 4))):
    # 谱面刻意延长到音频末尾，检查布局还会受本轮小节范围约束。
    score = fake_chart(patterns=patterns, start=entry, duration=60)
    preparation = tuple(SimpleNamespace(index=i, bar=0, denominator=4, dots=0,
                                       beat_offset=Fraction(i), time=entry - 1.5 + i * 0.5)
                        for i in range(3))
    return SimpleNamespace(score_chart=score, entry_time=entry, round_end_time=entry + 3,
                           first_formal_bar=1, last_formal_bar=2, finished=False, failed=False,
                           preparation_start_time=entry - 1.5, preparation_targets=preparation,
                           preparation_states={}, states={}, window_ms=100)


class StaffBeamTests(unittest.TestCase):
    def test_sixteenths_form_four_beat_groups_with_two_beams(self):
        plan = layout_bar([16] * 16, 4)
        primary = [beam for beam in plan.beams if beam.level == 1]
        secondary = [beam for beam in plan.beams if beam.level == 2]
        self.assertEqual([beam.note_indices for beam in primary],
                         [(0, 1, 2, 3), (4, 5, 6, 7), (8, 9, 10, 11), (12, 13, 14, 15)])
        self.assertEqual([beam.note_indices for beam in secondary], [b.note_indices for b in primary])
        self.assertTrue(all(not note.flags for note in plan.notes))
        self.assertEqual([note.x for note in plan.notes[:4]], [0, 30, 60, 90])

    def test_eighth_then_two_sixteenths_and_reverse(self):
        forward = layout_bar([8, 16, 16], 1)
        reverse = layout_bar([16, 16, 8], 1)
        self.assertEqual([(b.level, b.note_indices, b.hook) for b in forward.beams],
                         [(1, (0, 1, 2), False), (2, (1, 2), False)])
        self.assertEqual([(b.level, b.note_indices, b.hook) for b in reverse.beams],
                         [(1, (0, 1, 2), False), (2, (0, 1), False)])
        self.assertTrue(all(not note.flags for note in (*forward.notes, *reverse.notes)))

    def test_dotted_eighth_and_sixteenth_have_correct_secondary_hook(self):
        for pattern, index, direction in ((("8.", 16), 1, -1), ((16, "8."), 0, 1)):
            with self.subTest(pattern=pattern):
                plan = layout_bar(pattern, 1)
                self.assertEqual(plan.beams[0].note_indices, (0, 1))
                hook = plan.beams[1]
                self.assertEqual((hook.level, hook.note_indices, hook.hook), (2, (index,), True))
                self.assertGreater((hook.segment.x2 - hook.segment.x1) * direction, 0)
                self.assertEqual(len(plan.notes[1 - index].dot_positions), 1)

    def test_two_isolated_secondary_beams_do_not_connect_through_eighth(self):
        plan = layout_bar([16, 8, 16], 1)
        self.assertEqual([(b.note_indices, b.hook) for b in plan.beams if b.level == 2],
                         [((0,), True), ((2,), True)])
        self.assertLess(plan.beams[1].segment.x2, plan.notes[1].x)
        self.assertGreater(plan.beams[2].segment.x2, plan.notes[1].x)

    def test_half_whole_and_multiple_dots(self):
        whole = layout_bar([1], 4).notes[0]
        self.assertFalse(whole.filled)
        self.assertIsNone(whole.stem)
        half = layout_bar([2], 2).notes[0]
        self.assertFalse(half.filled)
        self.assertIsNotNone(half.stem)
        for pattern, dots in ((("4..", 16), 2), (("8...", 64), 3)):
            note = layout_bar(pattern, 2 if dots == 2 else 1).notes[0]
            self.assertEqual(len(note.dot_positions), dots)
            self.assertTrue(all(y == 251 for _, y in note.dot_positions))

    def test_unbeamed_short_notes_get_every_flag_and_never_duplicate_beams(self):
        plan = layout_bar(["8..", 32], 1)
        self.assertFalse(any(n.flags for n in plan.notes))
        self.assertEqual([beam.level for beam in plan.beams], [1, 2, 3])
        # 第一个音符越过四分拍边界；两端均为独立音符。
        crossing = layout_bar(["4..", 8, 16, 8, 4], 4)
        short = crossing.notes[1]
        self.assertEqual(len(short.flags), 1)
        self.assertFalse(any(short.index in beam.note_indices for beam in crossing.beams))

    def test_thirty_second_and_sixty_fourth_beam_levels(self):
        for denominator, levels in ((32, 3), (64, 4)):
            plan = layout_bar([denominator] * (denominator // 4), 1)
            self.assertEqual([beam.level for beam in plan.beams], list(range(1, levels + 1)))
            self.assertTrue(all(not note.flags for note in plan.notes))


class StaffTimelineTests(unittest.TestCase):
    def test_explicitly_hidden_layout_has_no_rhythm_or_bar_marks(self):
        chart = fake_chart(patterns=(("8.", 16, 8, 16, 16, 4),))
        plan = build_staff_layout(chart, chart.start, show_notes=False)
        self.assertEqual((plan.notes, plan.beams, plan.bars), ((), (), ()))
        self.assertEqual((plan.left, plan.right, plan.playhead_x), (210, 1150, 430))

    def test_three_four_multiple_bars_and_clock_based_scroll(self):
        chart = fake_chart()
        first = build_staff_layout(chart, chart.start)
        later = build_staff_layout(chart, chart.start + chart.beat_seconds / 2)
        self.assertGreaterEqual(len(first.bars), 3)
        self.assertEqual(first.notes[0].x, first.playhead_x)
        self.assertAlmostEqual(later.notes[0].x, first.notes[0].x - 60)
        self.assertAlmostEqual(first.bars[1].x - first.bars[0].x, 360)
        self.assertEqual(first.playhead_x, later.playhead_x)
        self.assertFalse(any(beam.note_indices[-1] == 4 for beam in first.beams))

    def test_preparation_does_not_freeze_formal_bar(self):
        chart = fake_chart(patterns=((4, 4, 4), (8, 16, 16, 4, 4)))
        session = SimpleNamespace(entry_time=7, round_end_time=8.5)
        plan = build_staff_layout(chart, 6, session)
        formal = [bar for bar in plan.bars if bar.formal]
        self.assertEqual([bar.number for bar in formal], [3])
        self.assertAlmostEqual(formal[0].x, plan.playhead_x + 240 - 18)
        self.assertEqual([note.denominator for note in plan.notes if note.bar == 2], [8, 16, 16, 4, 4])

    def test_preparation_replaces_only_its_bar_with_quarters_and_has_separate_states(self):
        chart = fake_chart(patterns=((8, 16, 16, 4, 4),))
        preparation = tuple(SimpleNamespace(index=i, bar=2, denominator=4, dots=0,
                                           beat_offset=Fraction(i), time=5.5 + i * 0.5)
                            for i in range(3))
        session = SimpleNamespace(entry_time=7, round_end_time=8.5, preparation_start_time=5.5,
                                  preparation_targets=preparation, preparation_states={0: "miss"},
                                  states={0: "hit"}, window_ms=100)
        plan = build_staff_layout(chart, 5.5, session)
        prep_notes = [n for n in plan.notes if n.preparation]
        self.assertEqual([(n.denominator, n.beat_offset, n.dots) for n in prep_notes],
                         [(4, Fraction(i), 0) for i in range(3)])
        self.assertTrue(next(bar for bar in plan.bars if bar.number == 2).preparation)
        formal_notes = [n for n in plan.notes if n.bar == 3]
        self.assertEqual([n.denominator for n in formal_notes], [8, 16, 16, 4, 4])
        self.assertTrue(all(not n.preparation for n in formal_notes))
        # 即使两组目标号都是 0，两种状态仍可分别绘成红色和绿色。
        self.assertEqual(note_status(prep_notes[0], 5.5, chart, session), "miss")
        self.assertEqual(note_status(replace(prep_notes[0], preparation=False), 5.5, chart, session), "hit")
        self.assertTrue(all(max(beam.segment.x1, beam.segment.x2) < prep_notes[0].x
                            or min(beam.segment.x1, beam.segment.x2) > prep_notes[-1].x
                            for beam in plan.beams))

    def test_formal_bar_integer_range_avoids_floating_end_boundary(self):
        chart = fake_chart(patterns=((4,),), beats=1)
        session = SimpleNamespace(entry_time=4.5, round_end_time=5.000000000000001,
                                  first_formal_bar=2, last_formal_bar=2,
                                  states={}, window_ms=100)
        plan = build_staff_layout(chart, 5, session)
        self.assertEqual([bar.number for bar in plan.bars if bar.formal], [2])
        following = next(note for note in plan.notes if note.bar == 3)
        self.assertIsNone(note_status(following, 5, chart, session))

    def test_bars_are_never_joined_and_eof_has_no_fake_notes(self):
        chart = fake_chart(patterns=((8, 8),), beats=1, duration=4.75)
        plan = build_staff_layout(chart, 4)
        self.assertEqual([n.index for n in plan.notes], [0, 1, 2])
        self.assertEqual([beam.note_indices for beam in plan.beams], [(0, 1)])
        self.assertEqual(len(plan.notes[-1].flags), 1)

    def test_sixty_fourths_use_readable_proportional_spacing(self):
        chart = fake_chart(patterns=((64,) * 16,), beats=1)
        plan = build_staff_layout(chart, 4)
        self.assertGreaterEqual(plan.notes[1].x - plan.notes[0].x, 20)

    def test_clip_horizontal_reversed_vertical_and_outside_segments(self):
        self.assertEqual(clip_segment(Segment(0, 5, 100, 5), 20, 80), Segment(20, 5, 80, 5))
        self.assertEqual(clip_segment(Segment(100, 5, 0, 5), 20, 80), Segment(80, 5, 20, 5))
        self.assertEqual(clip_segment(Segment(50, 0, 50, 10), 20, 80), Segment(50, 0, 50, 10))
        self.assertIsNone(clip_segment(Segment(0, 0, 10, 10), 20, 80))
        self.assertIsNone(clip_segment(Segment(5, 0, 5, 10), 20, 80))


class StaffProjectedScoreTests(unittest.TestCase):
    def test_actual_rounds_at_different_music_bars_have_same_local_layout(self):
        patterns = ((8, 16, 16, 4, 4), ("8.", 16, 4, 4))
        music = RhythmChart(120, 3, 4, 0, patterns[0], 60, patterns=patterns)
        layouts = []
        for trigger in (4.1, 10.1):
            session = RhythmSession(music, 100, game_bars=2)
            self.assertTrue(session.try_start(trigger))
            plan = build_staff_layout(music, session.entry_time, session)
            layouts.append(plan)
            self.assertEqual([bar.number for bar in plan.bars if bar.formal], [1, 2])
            self.assertTrue(any(note.preparation and note.bar == 0 for note in plan.notes))
            first = next(note for note in plan.notes if not note.preparation)
            self.assertEqual((first.index, first.bar, first.denominator, first.x), (0, 1, 8, 430))
            self.assertEqual(len([n for n in plan.notes if not n.preparation]), len(session.formal_targets))
        self.assertEqual([(n.bar, n.denominator, n.dots, n.x) for n in layouts[0].notes],
                         [(n.bar, n.denominator, n.dots, n.x) for n in layouts[1].notes])

    def test_each_music_position_restarts_same_score_at_bar_one(self):
        music = fake_chart(patterns=((4, 4, 4), (16,) * 12))
        plans = []
        for entry in (10, 22):
            session = projected_session(entry)
            plan = build_staff_layout(music, entry - 1, session)
            plans.append(plan)
            self.assertEqual([bar.number for bar in plan.bars if bar.formal], [1, 2])
            first_bar = [note for note in plan.notes if note.bar == 1]
            self.assertEqual([note.denominator for note in first_bar], [8, 16, 16, 4, 4])
            self.assertEqual(first_bar[0].index, 0)
            self.assertEqual(first_bar[0].x, plan.playhead_x + 240)
            second_bar = [note for note in plan.notes if note.bar == 2]
            self.assertEqual([note.denominator for note in second_bar], [8, 16, 4, 4])
            self.assertEqual(second_bar[0].dots, 1)
            self.assertTrue(all(note.bar <= 2 for note in plan.notes))
        shape = lambda plan: [(n.bar, n.denominator, n.dots, n.beat_offset, n.x) for n in plan.notes]
        self.assertEqual(shape(plans[0]), shape(plans[1]))

    def test_preparation_bar_zero_uses_quarters_before_score_bar_one(self):
        session = projected_session()
        plan = build_staff_layout(fake_chart(), 9, session)
        prep = [note for note in plan.notes if note.preparation]
        self.assertEqual([(n.bar, n.index, n.denominator) for n in prep], [(0, i, 4) for i in range(3)])
        self.assertTrue(next(bar for bar in plan.bars if bar.number == 0).preparation)
        self.assertEqual(prep[0].x, plan.playhead_x - 120)
        self.assertEqual(next(n for n in plan.notes if n.bar == 1).x - prep[0].x, 360)

    def test_projected_notes_scroll_by_actual_audio_delta(self):
        session = projected_session()
        music = fake_chart()
        first = build_staff_layout(music, 9, session)
        later = build_staff_layout(music, 9.25, session)
        by_id = {(note.preparation, note.index): note for note in later.notes}
        for note in first.notes:
            shifted = by_id[(note.preparation, note.index)]
            self.assertAlmostEqual(note.x - shifted.x, 60)
        self.assertEqual(first.playhead_x, later.playhead_x)

    def test_active_highlight_uses_score_clock_and_local_target_id(self):
        session = projected_session()
        music = fake_chart()
        plan = build_staff_layout(music, 10, session)
        note = next(n for n in plan.notes if not n.preparation and n.index == 0)
        self.assertEqual(note_status(note, 10, music, session), "active")
        session.states[0] = "hit"
        session.preparation_states[0] = "miss"
        self.assertEqual(note_status(note, 10, music, session), "hit")
        preparation = next(n for n in plan.notes if n.preparation and n.index == 0)
        self.assertEqual(note_status(preparation, 10, music, session), "miss")


class StaffVisibilityTests(unittest.TestCase):
    palette = SimpleNamespace(TEXT=(35, 35, 35), MUTED=(105, 105, 105),
                              GREEN=(35, 110, 65), PURPLE=(55, 85, 150), RED=(180, 45, 45))

    def test_waiting_before_first_clap_and_after_attempt_has_only_lines_and_signature(self):
        chart = fake_chart()
        for entry, finished, failed in ((None, False, False), (7, True, False), (7, True, True)):
            with self.subTest(entry=entry, finished=finished, failed=failed):
                window = SimpleNamespace(_line=Mock(), _text=Mock(), _circle=Mock(), _point=Mock(), _scale=1)
                session = SimpleNamespace(entry_time=entry, finished=finished, failed=failed)
                arcade = SimpleNamespace(draw_ellipse_filled=Mock(), draw_ellipse_outline=Mock(),
                                         draw_polygon_filled=Mock())
                with patch.dict(sys.modules, {"arcade": arcade, "rhythm_game.app": self.palette}):
                    draw_staff(window, 5, chart, session)
                self.assertEqual(window._line.call_count, 5)
                self.assertEqual([call.args[0] for call in window._text.call_args_list],
                                 ["signature-top", "signature-bottom"])
                window._circle.assert_not_called()
                window._point.assert_not_called()
                arcade.draw_ellipse_filled.assert_not_called()
                arcade.draw_ellipse_outline.assert_not_called()
                arcade.draw_polygon_filled.assert_not_called()

    def test_preparation_label_has_no_zero_or_absolute_music_bar_number(self):
        window = SimpleNamespace(_line=Mock(), _text=Mock(), _circle=Mock(),
                                 _point=lambda x, y: (x, y), _scale=1)
        arcade = SimpleNamespace(draw_ellipse_filled=Mock(), draw_ellipse_outline=Mock(),
                                 draw_polygon_filled=Mock())
        with patch.dict(sys.modules, {"arcade": arcade, "rhythm_game.app": self.palette}):
            draw_staff(window, 9, fake_chart(), projected_session())
        labels = [call.args[1] for call in window._text.call_args_list
                  if isinstance(call.args[0], tuple) and call.args[0][0] == "staff-bar"]
        self.assertIn("预备", labels)
        self.assertIn("1", labels)
        self.assertNotIn("0", labels)
        self.assertNotIn("5", labels)

    def test_trigger_displays_notes_in_lead_time_and_preparation(self):
        chart = fake_chart()
        preparation = tuple(SimpleNamespace(index=i, bar=2, denominator=4, dots=0,
                                           beat_offset=Fraction(i), time=5.5 + i * 0.5)
                            for i in range(3))
        session = SimpleNamespace(entry_time=7, round_end_time=8.5, finished=False, failed=False,
                                  preparation_start_time=5.5, preparation_targets=preparation,
                                  preparation_states={}, states={}, window_ms=100)
        for position in (4.5, 5.5):
            with self.subTest(position=position):
                window = SimpleNamespace(_line=Mock(), _text=Mock(), _circle=Mock(),
                                         _point=lambda x, y: (x, y), _scale=1)
                arcade = SimpleNamespace(draw_ellipse_filled=Mock(), draw_ellipse_outline=Mock(),
                                         draw_polygon_filled=Mock())
                with patch.dict(sys.modules, {"arcade": arcade, "rhythm_game.app": self.palette}):
                    draw_staff(window, position, chart, session)
                self.assertGreater(arcade.draw_ellipse_filled.call_count, 0)
                self.assertTrue(any(isinstance(call.args[0], tuple) and call.args[0][0] == "staff-beat"
                                    for call in window._text.call_args_list))
                self.assertGreater(window._line.call_count, 5)


if __name__ == "__main__":
    unittest.main()
