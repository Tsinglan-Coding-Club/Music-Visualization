"""确定时间输入的软件测试；不代表手套或音频硬件延迟测试。"""

from dataclasses import FrozenInstanceError
from fractions import Fraction
import math
import unittest

from rhythm_game.core import Judgment, RhythmChart, RhythmSession, Target


def chart(**overrides):
    values = dict(
        bpm=90,
        beats_per_bar=4,
        beat_start_seconds=4,
        beat_offset_ms=0,
        note_values=[4, 8, 8, 4, 4],
        duration_seconds=20,
    )
    values.update(overrides)
    return RhythmChart(**values)


class ChartTests(unittest.TestCase):
    def test_mixed_note_onsets_and_bar_numbers_at_90_bpm(self):
        score = chart()
        self.assertAlmostEqual(score.beat_seconds, 2 / 3)
        self.assertAlmostEqual(score.bar_seconds, 8 / 3)
        expected_beats = [0, 1, 1.5, 2, 3, 4, 5, 5.5]
        for target, beat in zip(score.targets, expected_beats):
            self.assertAlmostEqual(target.time, 4 + beat * 2 / 3)
        self.assertEqual([t.denominator for t in score.targets[:5]], [4, 8, 8, 4, 4])
        self.assertEqual([t.bar for t in score.targets[:6]], [1, 1, 1, 1, 1, 2])
        self.assertEqual([t.index for t in score.targets], list(range(len(score.targets))))

    def test_sixteenths_and_dotted_notes_keep_exact_bar_offsets(self):
        score = chart(bpm=60, note_values=["4..", 16, "8.", 16, 4])
        expected = [Fraction(0), Fraction(7, 4), Fraction(2), Fraction(11, 4), Fraction(3)]
        self.assertEqual([t.beat_offset for t in score.targets_for_bar(1)], expected)
        self.assertEqual([t.time for t in score.targets_for_bar(1)], [4 + float(v) for v in expected])
        self.assertEqual([t.dots for t in score.targets_for_bar(1)], [2, 0, 1, 0, 0])
        self.assertEqual(score.targets[0].duration_beats, Fraction(7, 4))
        self.assertEqual(score.patterns, (("4..", 16, "8.", 16, 4),))

    def test_variable_patterns_have_independent_bar_ranges_and_downbeats(self):
        score = chart(bpm=60, patterns=[[4] * 4, [16] * 16], duration_seconds=16.1)
        self.assertEqual(score.patterns, ((4, 4, 4, 4), (16,) * 16))
        self.assertEqual(score.note_values, (4, 4, 4, 4))
        self.assertEqual(score._downbeat_times, (4, 8, 12, 16))
        self.assertEqual([len(score.targets_for_bar(bar)) for bar in [1, 2, 3, 4]], [4, 16, 4, 1])
        self.assertEqual(score.targets_for_bar(3)[0].index, 20)
        self.assertEqual(score.targets_for_bar(99), [])
        for patterns in [[], [[4]], [[4] * 4, [8]], [["8...", 4]]]:
            with self.subTest(patterns=patterns), self.assertRaises(ValueError):
                chart(patterns=patterns)

    def test_global_positive_and_negative_offset_moves_all_targets(self):
        base = chart(duration_seconds=19)
        for offset_ms in [-150, 150]:
            shifted = chart(beat_offset_ms=offset_ms, duration_seconds=19)
            self.assertEqual(len(base.targets), len(shifted.targets))
            for original, target in zip(base.targets, shifted.targets):
                self.assertAlmostEqual(target.time - original.time, offset_ms / 1000)
                self.assertEqual(target.bar, original.bar)

    def test_direct_generation_has_no_accumulated_float_drift(self):
        score = chart(duration_seconds=3600)
        last = score.targets[-1]
        onset_beats = [0, 1, 1.5, 2, 3]
        expected = 4 + ((last.bar - 1) * 4 + onset_beats[last.index % 5]) * 60 / 90
        self.assertAlmostEqual(last.time, expected, places=11)

    def test_no_target_at_or_beyond_eof(self):
        score = chart(bpm=60, duration_seconds=8)
        self.assertTrue(all(t.time < 8 for t in score.targets))
        self.assertEqual(score.targets[-1].time, 7)
        self.assertIsNone(score.next_bar(8))
        self.assertEqual(len(chart(duration_seconds=4.01).targets), 1)

    def test_next_bar_uses_absolute_music_position(self):
        score = chart()
        self.assertEqual(score.next_bar(0), 4)
        self.assertEqual(score.next_bar(4), 4)
        self.assertAlmostEqual(score.next_bar(4.001), 4 + 8 / 3)
        boundary = score.start + 3 * score.bar_seconds
        self.assertAlmostEqual(score.next_bar(boundary), boundary)
        self.assertIsNone(score.next_bar(20))

    def test_bar_and_fractional_beat_display(self):
        score = chart()
        self.assertIsNone(score.bar_at(3.9))
        self.assertEqual(score.bar_at(4), (1, 1))
        self.assertEqual(score.bar_at(5), (1, 2.5))
        self.assertEqual(score.bar_at(4 + score.bar_seconds), (2, 1))
        self.assertIsNone(score.bar_at(20))

    def test_visible_targets_includes_both_interval_boundaries(self):
        score = chart(bpm=60)
        visible = score.visible_targets(5, lookahead=1, lookbehind=1)
        self.assertEqual([t.time for t in visible], [4, 5, 5.5, 6])
        self.assertEqual(score.visible_targets(30, 1), [])
        self.assertEqual(score.visible_targets(4, 0, 0), [score.targets[0]])

    def test_records_are_immutable(self):
        target = Target(0, 4, 4, 1)
        result = Judgment("hit", target, 0)
        with self.assertRaises(FrozenInstanceError):
            target.time = 5
        with self.assertRaises(FrozenInstanceError):
            result.status = "miss"

    def test_invalid_configuration(self):
        invalid = [
            dict(bpm=0), dict(bpm=-1), dict(bpm=True), dict(bpm="90"),
            dict(beats_per_bar=0), dict(beats_per_bar=4.5), dict(beats_per_bar=True),
            dict(beat_start_seconds=-1), dict(beat_start_seconds=0, beat_offset_ms=-1),
            dict(beat_start_seconds=20), dict(duration_seconds=0), dict(duration_seconds=-1),
            dict(note_values=[]), dict(note_values=None), dict(note_values=[4, 8, 8]),
            dict(note_values=[0, 4]), dict(note_values=[-4, 4]),
            dict(note_values=[4.0, 4, 4, 4]), dict(note_values=[True, 4, 4, 4]),
        ]
        for field in ["bpm", "beat_start_seconds", "beat_offset_ms", "duration_seconds"]:
            invalid.extend({field: value} for value in [math.inf, -math.inf, math.nan])
        for overrides in invalid:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                chart(**overrides)

    def test_invalid_queries(self):
        score = chart()
        for value in [math.inf, -math.inf, math.nan]:
            with self.assertRaises(ValueError):
                score.next_bar(value)
            with self.assertRaises(ValueError):
                score.bar_at(value)
            with self.assertRaises(ValueError):
                score.visible_targets(value, 1)
            with self.assertRaises(ValueError):
                score.visible_targets(4, value)
            with self.assertRaises(ValueError):
                score.visible_targets(4, 1, value)
        with self.assertRaises(ValueError):
            score.visible_targets(4, -1)
        with self.assertRaises(ValueError):
            score.visible_targets(4, 1, -1)


class SessionTests(unittest.TestCase):
    def session(self, **overrides):
        values = dict(window_ms=125, input_offset_ms=0)
        values.update(overrides)
        return RhythmSession(chart(), **values)

    def test_unarmed_and_before_entry_window_wait(self):
        game = self.session()
        self.assertEqual(game.judge(4), Judgment("waiting", None, None))
        game.advance(100)
        self.assertEqual(game.states, {})
        game.arm(3)
        self.assertEqual(game.judge(3.8).status, "waiting")
        self.assertEqual(game.judge(3.9).status, "hit")
        self.assertEqual(game.hits, 1)

    def test_arm_at_target_allows_same_timestamp_hit(self):
        game = self.session()
        self.assertEqual(game.arm(4), 4)
        self.assertEqual(game.judge(4).status, "hit")

    def test_two_different_bar_entries_exclude_earlier_targets(self):
        game = self.session()
        for request, expected_bar in [(4.1, 2), (9.4, 4)]:
            entry = game.arm(request)
            target = game.judge(entry).target
            self.assertEqual(target.bar, expected_bar)
            self.assertEqual(game.hits, 1)
            game.advance(entry + 0.2)
            self.assertEqual(game.misses, 0)
            self.assertTrue(all(game.chart.targets[i].bar >= expected_bar for i in game.states))

    def test_repeated_early_and_late_input_does_not_shift_targets(self):
        for error in [-0.075, 0.075]:
            game = self.session()
            game.arm(3)
            original = list(game.chart.targets)
            round_targets = [target for target in original if target.bar == 1]
            for target in round_targets:
                result = game.judge(target.time + error)
                self.assertEqual(result.status, "hit")
                self.assertEqual(result.target.index, target.index)
                self.assertAlmostEqual(result.error_ms, error * 1000)
                game.advance(target.time + error)
            self.assertEqual(game.chart.targets, original)
            self.assertEqual(game.hits, len(round_targets))
            self.assertEqual(game.misses, 0)
            self.assertFalse(game.finished)
            game.advance(game.round_end_time)
            self.assertTrue(game.finished)

    def test_nearest_target_after_skipping_multiple_notes(self):
        game = self.session()
        game.arm(3)
        target = game.chart.targets[3]
        result = game.judge(target.time - 0.04)
        self.assertEqual(result.target, target)
        self.assertEqual(result.status, "hit")
        game.advance(target.time)
        self.assertEqual(game.misses, 3)

    def test_midpoint_tie_goes_to_earlier_and_duplicate_does_not_fall_through(self):
        game = self.session(window_ms=600)
        game.arm(3)
        first, second = game.chart.targets[:2]
        self.assertEqual(game.judge(first.time).status, "hit")
        result = game.judge((first.time + second.time) / 2)
        self.assertEqual(result.status, "duplicate")
        self.assertEqual(result.target, first)
        self.assertNotIn(second.index, game.states)
        self.assertEqual(game.hits, 1)

    def test_symmetric_window_includes_both_endpoints(self):
        for sign in [-1, 1]:
            game = self.session()
            game.arm(3)
            result = game.judge(4 + sign * 0.125)
            self.assertEqual(result.status, "hit")
            self.assertAlmostEqual(result.error_ms, sign * 125)

    def test_out_of_window_input_does_not_consume_target(self):
        game = self.session()
        game.arm(3)
        second = game.chart.targets[1]
        result = game.judge(second.time - 0.13)
        self.assertEqual(result.status, "early")
        self.assertNotIn(second.index, game.states)
        self.assertEqual(game.judge(second.time).status, "hit")
        late = game.judge(second.time + 0.13)
        self.assertEqual(late.status, "late")
        self.assertEqual(game.hits, 1)

    def test_no_input_miss_only_after_inclusive_deadline(self):
        game = self.session()
        game.arm(3)
        game.advance(4.125)
        self.assertEqual(game.misses, 0)
        game.advance(4.125001)
        self.assertEqual(game.states, {0: "miss"})
        game.advance(4.2)
        self.assertEqual(game.misses, 1)
        self.assertEqual(game.judge(4.2).status, "late")
        self.assertEqual(game.hits, 0)

    def test_positive_correction_subtracts_offset_and_defers_miss(self):
        game = self.session(input_offset_ms=80)
        game.arm(3)
        game.advance(4.15)
        self.assertEqual(game.misses, 0)
        result = game.judge(4.08)
        self.assertEqual(result.status, "hit")
        self.assertAlmostEqual(result.error_ms, 0)
        game.advance(4.3)
        self.assertEqual(game.misses, 0)

    def test_corrected_last_boundary_survives_advance_allowance(self):
        game = self.session(input_offset_ms=80)
        game.arm(3)
        game.advance(4.205)
        self.assertEqual(game.misses, 0)
        self.assertEqual(game.judge(4.205).status, "hit")
        game = self.session(input_offset_ms=80)
        game.arm(3)
        game.advance(4.205001)
        self.assertEqual(game.misses, 1)
        self.assertEqual(game.judge(4.205001).status, "late")

    def test_negative_input_offset_adds_to_time(self):
        game = self.session(input_offset_ms=-50)
        game.arm(3)
        result = game.judge(3.95)
        self.assertEqual(result.status, "hit")
        self.assertAlmostEqual(result.error_ms, 0)

    def test_finalized_miss_cannot_be_resurrected_by_old_event(self):
        game = self.session(input_offset_ms=80)
        game.arm(3)
        game.advance(4.3)
        self.assertEqual(game.judge(4.08).status, "late")
        self.assertEqual(game.states[0], "miss")

    def test_monotonic_input_timestamps_and_pre_arm_events(self):
        game = self.session()
        game.arm(3.95)
        self.assertEqual(game.judge(3.9).status, "waiting")
        self.assertEqual(game.judge(4).status, "hit")
        self.assertEqual(game.judge(4).status, "duplicate")
        with self.assertRaises(ValueError):
            game.judge(3.99)

    def test_reset_and_rearm_clear_all_previous_results(self):
        game = self.session()
        game.arm(3)
        game.judge(4)
        game.advance(6)
        self.assertGreater(game.misses, 0)
        game.reset()
        self.assertIsNone(game.entry_time)
        self.assertEqual(game.states, {})
        self.assertEqual((game.hits, game.misses), (0, 0))
        self.assertFalse(game.finished)
        game.arm(3)
        self.assertEqual(game.judge(4).status, "hit")
        self.assertIsNone(game.arm(20))
        self.assertEqual(game.states, {})

    def test_advance_after_eof_finishes_only_one_bar(self):
        game = self.session()
        entry = game.arm(4.1)
        expected = sum(entry <= t.time < game.round_end_time for t in game.chart.targets)
        game.advance(100)
        self.assertTrue(game.finished)
        self.assertEqual(game.misses, expected)
        self.assertEqual(game.hits, 0)

    def test_any_clap_maps_first_middle_and_last_quarter_in_4_and_3(self):
        for meter in [4, 3]:
            score = chart(bpm=60, beats_per_bar=meter, note_values=[4] * meter,
                          duration_seconds=60)
            for offset, expected in [(0, 1), (0.37, 1), (meter - 1 - 0.000001, 1),
                                     (meter - 1, 2), (meter - 0.000001, 2), (meter, 2)]:
                with self.subTest(meter=meter, offset=offset):
                    game = RhythmSession(score, 125)
                    self.assertTrue(game.try_start(4 + offset))
                    self.assertEqual(game.attributed_bar, expected)
                    self.assertEqual(game.preparation_start_time, 4 + expected * meter)
                    self.assertEqual(game.entry_time, 4 + (expected + 1) * meter)
                    self.assertEqual(game.judge(4 + offset).status, "waiting")
                    self.assertEqual((game.hits, game.mistakes), (0, 0))

    def test_intro_early_window_and_input_compensation(self):
        for offset in [-50, 80]:
            game = self.session(input_offset_ms=offset)
            self.assertFalse(game.try_start(4 - 0.125001 + offset / 1000))
            self.assertTrue(game.try_start(4 - 0.125 + offset / 1000))
            self.assertEqual(game.attributed_bar, 1)
            self.assertAlmostEqual(game.trigger_time, 3.875)
            self.assertEqual(game.hits, 0)

    def test_full_preparation_is_mandatory_for_legacy_lead_in_values(self):
        for legacy in [0, 1, 2]:
            game = self.session(lead_in_bars=legacy)
            self.assertTrue(game.try_start(4.4))
            self.assertEqual(game.phase_at(4), "lead_in")
            self.assertAlmostEqual(game.preparation_start_time, 4 + 8 / 3)
            self.assertAlmostEqual(game.entry_time, 4 + 16 / 3)
            self.assertEqual(game.phase_at(game.preparation_start_time), "preparing")
            self.assertEqual(game.phase_at(game.entry_time), "playing")

    def test_preparation_each_quarter_is_practice_even_with_dense_formal_chart(self):
        score = chart(note_values=[16] * 16, duration_seconds=40)
        game = RhythmSession(score, 125, input_offset_ms=80, max_mistakes=1)
        self.assertTrue(game.try_start(4.08))
        self.assertEqual(len(game.preparation_targets), 4)
        for target in game.preparation_targets:
            self.assertEqual(target.denominator, 4)
            self.assertEqual(game.judge(target.time + 0.08).status, "preparation_hit")
            self.assertEqual(game.judge(target.time + 0.09).status, "preparation_duplicate")
        self.assertEqual(game.prep_hits, 4)
        self.assertEqual((game.hits, game.misses, game.mistakes), (0, 0, 0))
        self.assertFalse(game.failed)
        self.assertEqual(game.judge(game.entry_time + 0.08).status, "hit")

    def test_missed_preparation_never_blocks_formal_game(self):
        game = self.session(max_mistakes=1)
        game.try_start(4)
        game.advance(game.entry_time)
        self.assertEqual(game.prep_misses, 4)
        self.assertEqual(game.mistakes, 0)
        self.assertFalse(game.failed)
        self.assertEqual(game.judge(game.entry_time).status, "hit")

    def test_formal_first_target_early_window_takes_priority_over_preparation(self):
        game = self.session(input_offset_ms=80)
        game.try_start(4.08)
        result = game.judge(game.entry_time - 0.125 + 0.08)
        self.assertEqual(result.status, "hit")
        self.assertEqual(result.target.bar, 3)
        self.assertAlmostEqual(result.error_ms, -125)
        self.assertEqual(game.hits, 1)
        self.assertEqual(game.prep_hits, 0)

    def test_multiple_changing_bars_are_scored_and_complete_at_end(self):
        score = chart(bpm=60, patterns=[[4, 4, 4, 4], [8] * 8, [16] * 16],
                      duration_seconds=40)
        game = RhythmSession(score, 60, game_bars=3)
        self.assertTrue(game.try_start(4.25))
        expected = [target for target in score.targets if 3 <= target.bar <= 5]
        self.assertEqual([len(score.targets_for_bar(bar)) for bar in [3, 4, 5]], [16, 4, 8])
        for target in expected:
            self.assertEqual(game.judge(target.time).status, "hit")
        self.assertEqual(game.hits, 28)
        self.assertFalse(game.finished)
        game.advance(game.round_end_time)
        self.assertTrue(game.finished)
        self.assertEqual(game.phase_at(game.round_end_time), "completed")
        saved = dict(game.states)
        self.assertEqual(game.judge(game.round_end_time).status, "completed")
        self.assertEqual(game.states, saved)

    def test_formal_target_slice_uses_bar_bounds_despite_float_round_end(self):
        score = chart(bpm=127, patterns=[[4] * 4, [16] * 16], duration_seconds=60)
        game = RhythmSession(score, 100, game_bars=4)
        self.assertEqual(game.formal_targets, ())
        self.assertIsNone(game.first_formal_bar)
        self.assertIsNone(game.last_formal_bar)
        game.try_start(4)
        self.assertEqual((game.first_formal_bar, game.last_formal_bar), (3, 6))
        self.assertEqual(len(game.formal_targets), 40)
        self.assertEqual(game.formal_targets, tuple(target for bar in range(3, 7)
                                                 for target in score.targets_for_bar(bar)))
        # Floating end arithmetic is slightly later than bar 7's exact onset.
        self.assertLess(score.targets_for_bar(7)[0].time, game.round_end_time)
        self.assertNotIn(score.targets_for_bar(7)[0], game.formal_targets)
        game.reset()
        self.assertEqual(game.formal_targets, ())
        self.assertIsNone(game.first_formal_bar)
        self.assertIsNone(game.last_formal_bar)

    def test_formal_failure_freezes_on_first_threshold_miss_and_can_retry(self):
        game = RhythmSession(chart(duration_seconds=40), 125, game_bars=3, max_mistakes=2)
        game.try_start(4)
        old_end = game.round_end_time
        game.judge(game.entry_time)
        game.advance(game.entry_time + 1.5)
        self.assertTrue(game.failed)
        self.assertTrue(game.finished)
        self.assertEqual((game.hits, game.misses, game.mistakes), (1, 2, 2))
        self.assertEqual(game.failure_reason, "miss")
        self.assertEqual(game.phase_at(game.entry_time), "failed")
        saved = dict(game.states)
        game.advance(100)
        self.assertEqual(game.states, saved)
        retry = game.failure_time + 0.1
        self.assertLess(retry, old_end)
        self.assertEqual(game.judge(retry).status, "failed")
        self.assertTrue(game.try_start(retry))
        self.assertFalse(game.failed)
        self.assertIsNone(game.failure_reason)
        self.assertIsNone(game.failure_time)
        self.assertEqual((game.hits, game.misses, game.mistakes), (0, 0, 0))

    def test_duplicate_and_early_extra_claps_count_errors(self):
        game = self.session(max_mistakes=2)
        game.arm(3)
        game.judge(4)
        self.assertEqual(game.judge(4.01).status, "duplicate")
        self.assertEqual(game.mistakes, 1)
        self.assertEqual(game.judge(4.4).status, "early")
        self.assertEqual(game.mistakes, 2)
        self.assertTrue(game.failed)
        self.assertEqual(game.failure_reason, "early")
        self.assertEqual(game.hits, 1)
        self.assertEqual(game.judge(4.6666666667).status, "failed")
        self.assertEqual(game.hits, 1)

    def test_late_supplement_of_missed_target_never_double_counts(self):
        game = self.session(max_mistakes=3)
        game.arm(3)
        game.advance(4.2)
        self.assertEqual((game.misses, game.mistakes), (1, 1))
        for time in [4.21, 4.22]:
            self.assertEqual(game.judge(time).status, "late")
        self.assertEqual((game.misses, game.mistakes), (1, 1))
        other = self.session(max_mistakes=3)
        other.arm(3)
        self.assertEqual(other.judge(4.2).status, "late")
        other.advance(4.21)
        self.assertEqual((other.misses, other.mistakes), (1, 1))

    def test_late_extra_after_hit_counts_error(self):
        game = self.session(max_mistakes=1)
        game.arm(3)
        game.judge(4)
        self.assertEqual(game.judge(4.2).status, "late")
        self.assertTrue(game.failed)
        self.assertEqual(game.failure_reason, "late")
        self.assertEqual((game.hits, game.misses, game.mistakes), (1, 0, 1))

    def test_dense_miss_settlement_stops_exactly_at_threshold(self):
        score = chart(bpm=600, note_values=[16] * 16)
        game = RhythmSession(score, 10, max_mistakes=3)
        game.arm(3)
        game.advance(100)
        self.assertEqual((game.misses, game.mistakes), (3, 3))
        self.assertEqual(set(game.states), {0, 1, 2})
        self.assertAlmostEqual(game.failure_time, 4.06)
        # A future input must settle earlier misses before it can score.
        game.arm(3)
        self.assertEqual(game.judge(4.2).status, "failed")
        self.assertEqual(game.hits, 0)

    def test_unlimited_errors_and_round_end_wait_for_positive_offset(self):
        game = self.session(input_offset_ms=80)
        game.try_start(4.08)
        game.advance(game.round_end_time)
        self.assertFalse(game.failed)
        self.assertFalse(game.finished)
        game.advance(game.round_end_time + 0.08)
        self.assertTrue(game.finished)
        self.assertEqual(game.misses, 5)
        self.assertEqual(game.mistakes, 5)

    def test_eof_rejects_incomplete_real_round_and_preserves_results(self):
        # Trigger bar + preparation bar + two formal bars exactly fit.
        score = chart(bpm=60, duration_seconds=20)
        game = RhythmSession(score, 125, game_bars=2)
        self.assertTrue(game.try_start(4))
        self.assertEqual(game.round_end_time, 20)
        game.judge(game.entry_time)
        game.advance(20)
        old = (game.entry_time, game.trigger_time, dict(game.states), game.hits)
        self.assertFalse(game.try_start(20))
        self.assertEqual(game.start_rejection, "insufficient_music")
        self.assertEqual((game.entry_time, game.trigger_time, game.states, game.hits), old)
        self.assertFalse(RhythmSession(chart(bpm=60, duration_seconds=19.999), 125,
                                       game_bars=2).try_start(4))

    def test_arm_allows_truncated_eof_but_waits_for_last_window(self):
        game = RhythmSession(chart(duration_seconds=4.05), 125, 80)
        game.arm(3)
        game.advance(4.13)
        self.assertFalse(game.finished)
        self.assertEqual(game.judge(4.205).status, "hit")
        game.advance(4.205)
        self.assertTrue(game.finished)

    def test_active_rejection_does_not_clear_round_and_reset_clears_everything(self):
        game = self.session(max_mistakes=1)
        game.try_start(4)
        entry = game.entry_time
        self.assertFalse(game.try_start(4.4))
        self.assertEqual(game.start_rejection, "active")
        self.assertEqual(game.entry_time, entry)
        game.advance(entry + 0.2)
        self.assertTrue(game.failed)
        game.reset()
        self.assertEqual(game.phase_at(100), "waiting")
        self.assertEqual(game.preparation_targets, ())
        self.assertEqual((game.hits, game.misses, game.mistakes, game.prep_hits, game.prep_misses),
                         (0, 0, 0, 0, 0))
        self.assertIsNone(game.preparation_start_time)
        self.assertIsNone(game.start_rejection)
        self.assertFalse(game.finished)

    def test_invalid_session_parameters_and_times(self):
        for field in ["window_ms", "input_offset_ms"]:
            for value in [math.nan, math.inf, -math.inf, True, "10"]:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.session(**{field: value})
        with self.assertRaises(ValueError):
            self.session(window_ms=-1)
        for field in ["lead_in_bars", "max_mistakes", "game_bars"]:
            for value in [-1, 1.5, True, "1", math.inf, math.nan]:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.session(**{field: value})
        with self.assertRaises(ValueError):
            self.session(game_bars=0)
        game = self.session()
        for value in [math.nan, math.inf, -math.inf]:
            for method in [game.arm, game.try_start, game.judge, game.advance, game.phase_at]:
                with self.assertRaises(ValueError):
                    method(value)


if __name__ == "__main__":
    unittest.main()
