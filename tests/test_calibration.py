import math
import unittest

from rhythm_game.calibration import BeatCalibrator, TempoCalibrator


class CalibrationTests(unittest.TestCase):
    def test_known_shift_and_input_delay_are_separate(self):
        calibration = BeatCalibrator(90, 4, 0, 50)
        for index in range(16):
            calibration.add(4 + index * 2 / 3 + 0.110 + 0.050 + (index % 3 - 1) * 0.005)
        result = calibration.estimate()
        self.assertTrue(result.ready)
        self.assertAlmostEqual(result.offset_ms, 110, delta=3)
        self.assertAlmostEqual(result.first_beat_seconds, 4.110, delta=0.003)
        self.assertEqual(result.used, 16)

    def test_negative_offset_and_skipped_beats(self):
        calibration = BeatCalibrator(90, 4, -50, 25)
        for index in (0, 1, 3, 4, 5, 8, 9, 10):
            calibration.add(4 + index * 2 / 3 - 0.080 + 0.025)
        self.assertTrue(calibration.estimate().ready)
        self.assertAlmostEqual(calibration.estimate().offset_ms, -80)

    def test_outlier_does_not_move_robust_result(self):
        calibration = BeatCalibrator(90, 4, 0, 0)
        for index in range(12):
            calibration.add(4 + index * 2 / 3 + (0.23 if index in (3, 8) else 0.040))
        result = calibration.estimate()
        self.assertTrue(result.ready)
        self.assertAlmostEqual(result.offset_ms, 40)
        self.assertEqual(result.used, 10)
        self.assertEqual(result.rejected, 2)

    def test_intro_duplicate_and_backward_events_are_ignored(self):
        calibration = BeatCalibrator(90, 4, 0, 0)
        self.assertFalse(calibration.add(2))
        self.assertTrue(calibration.add(4.05))
        self.assertFalse(calibration.add(4.10))
        self.assertTrue(calibration.add(4.70))
        self.assertFalse(calibration.add(4.60))
        self.assertEqual(calibration.estimate().count, 2)
        self.assertEqual(calibration.estimate().rejected, 3)
        self.assertFalse(calibration.estimate().ready)

    def test_minimum_samples_and_unstable_phase(self):
        calibration = BeatCalibrator(90, 4, 0, 0)
        self.assertFalse(calibration.estimate().ready)
        for index in range(7):
            calibration.add(4 + index * 2 / 3)
        self.assertFalse(calibration.estimate().ready)
        unstable = BeatCalibrator(90, 4, 0, 0)
        for index in range(20):
            unstable.add(4 + index * 2 / 3 + (0, 0.12, -0.13, 0.23, -0.24)[index % 5])
        self.assertFalse(unstable.estimate().ready)

    def test_half_beat_boundary_does_not_average_to_zero(self):
        calibration = BeatCalibrator(90, 4, 0, 0)
        for index in range(20):
            calibration.add(4 + index * 2 / 3 + 0.332 + (0.004 if index % 2 else -0.004))
        result = calibration.estimate()
        self.assertGreater(abs(result.offset_ms), 310)
        self.assertFalse(result.ready)
        self.assertIn("半拍", result.message)

    def test_capture_is_bounded(self):
        calibration = BeatCalibrator(90, 4, 0, 0)
        for index in range(100):
            calibration.add(4 + index * 2 / 3 + 0.05)
        self.assertEqual(calibration.estimate().count, 64)
        self.assertTrue(calibration.estimate().ready)

    def test_invalid_numbers(self):
        for bpm in (0, math.nan, True):
            with self.subTest(bpm=bpm), self.assertRaises(ValueError):
                BeatCalibrator(bpm, 4, 0, 0)
        calibration = BeatCalibrator(90, 4, 0, 0)
        for time in (math.nan, math.inf, True):
            with self.subTest(time=time), self.assertRaises(ValueError):
                calibration.add(time)

    def test_average_bpm_is_independent_of_configured_grid(self):
        for actual_bpm in (60, 105, 180):
            with self.subTest(actual_bpm=actual_bpm):
                calibration = BeatCalibrator(90, 4, 0, 35)
                for index in range(20):
                    calibration.add(4.115 + index * 60 / actual_bpm)
                result = calibration.estimate()
                self.assertAlmostEqual(result.average_bpm, actual_bpm)
                self.assertEqual(result.tempo_intervals, 19)

    def test_average_bpm_uses_average_interval_and_needs_two_taps(self):
        calibration = BeatCalibrator(90, 4, 0, 0)
        self.assertIsNone(calibration.estimate().average_bpm)
        calibration.add(4)
        self.assertIsNone(calibration.estimate().average_bpm)
        for time in (4.5, 5.2, 6.0):
            calibration.add(time)
        self.assertAlmostEqual(calibration.estimate().average_bpm, 90)
        self.assertEqual(calibration.estimate().tempo_intervals, 3)

    def test_tempo_ignores_rapid_duplicates_and_backward_events(self):
        calibration = BeatCalibrator(90, 4, 0, 0)
        for time in (4, 4.03, 4 + 2 / 3, 4.5, 4 + 4 / 3):
            calibration.add(time)
        self.assertAlmostEqual(calibration.estimate().average_bpm, 90)
        self.assertEqual(calibration.estimate().tempo_intervals, 2)

    def test_tempo_capture_is_bounded_independently(self):
        calibration = BeatCalibrator(90, 4, 0, 0)
        for index in range(100):
            calibration.add(4 + index * 60 / 105)
        result = calibration.estimate()
        self.assertEqual(result.tempo_intervals, 63)
        self.assertAlmostEqual(result.average_bpm, 105)


class TempoCalibrationTests(unittest.TestCase):
    def test_tempo_is_independent_of_configured_grid_and_start(self):
        for bpm in (45, 90, 105, 180):
            with self.subTest(bpm=bpm):
                calibration = TempoCalibrator()
                # 无需经过设定的 4 秒首拍，也无需传入设定速度。
                for index in range(16):
                    self.assertTrue(calibration.add(0.215 + index * 60 / bpm))
                result = calibration.estimate()
                self.assertTrue(result.ready)
                self.assertEqual(result.count, 16)
                self.assertEqual(result.used, 16)
                self.assertEqual(result.tempo_intervals, 15)
                self.assertAlmostEqual(result.average_bpm, bpm)

    def test_stable_jitter_has_small_interval_spread(self):
        calibration = TempoCalibrator()
        for index in range(16):
            calibration.add(10 + index * 0.5 + (index % 3 - 1) * 0.005)
        result = calibration.estimate()
        self.assertTrue(result.ready)
        self.assertAlmostEqual(result.average_bpm, 120, delta=1.3)
        self.assertLessEqual(result.spread_ms, 16)
        self.assertEqual(result.rejected, 0)

    def test_occasional_missing_and_extra_taps_are_interval_outliers(self):
        calibration = TempoCalibrator()
        times = [index * 0.5 for index in range(20) if index != 4]
        times.append(6.7)
        for time in sorted(times):
            calibration.add(time)
        result = calibration.estimate()
        self.assertTrue(result.ready)
        self.assertAlmostEqual(result.average_bpm, 120)
        self.assertEqual(result.count, 20)
        self.assertEqual(result.tempo_intervals, 16)
        self.assertEqual(result.used, 17)
        self.assertEqual(result.rejected, 3)

    def test_no_half_or_double_tempo_guess(self):
        for interval, expected_bpm in ((1, 60), (0.25, 240)):
            with self.subTest(interval=interval):
                calibration = TempoCalibrator()
                for index in range(16):
                    calibration.add(index * interval)
                result = calibration.estimate()
                self.assertTrue(result.ready)
                self.assertAlmostEqual(result.average_bpm, expected_bpm)
                self.assertIn("半速或倍速", result.message)

    def test_eight_taps_are_required_and_two_show_a_provisional_bpm(self):
        calibration = TempoCalibrator()
        self.assertIsNone(calibration.estimate().average_bpm)
        calibration.add(0)
        self.assertIsNone(calibration.estimate().average_bpm)
        for index in range(1, 7):
            calibration.add(index * 0.5)
            self.assertAlmostEqual(calibration.estimate().average_bpm, 120)
            self.assertFalse(calibration.estimate().ready)
        calibration.add(3.5)
        self.assertTrue(calibration.estimate().ready)

    def test_mixed_tempi_do_not_produce_a_ready_result(self):
        for intervals in ([0.3, 0.9] * 8, [0.5] * 8 + [0.8] * 7):
            with self.subTest(intervals=intervals):
                calibration = TempoCalibrator()
                calibration.add(0)
                time = 0
                for interval in intervals:
                    time += interval
                    calibration.add(time)
                self.assertFalse(calibration.estimate().ready)

    def test_excessive_jitter_is_not_ready_even_without_outliers(self):
        calibration = TempoCalibrator()
        time = 0
        calibration.add(time)
        for interval in (0.45, 0.55) * 8:
            time += interval
            calibration.add(time)
        self.assertFalse(calibration.estimate().ready)
        self.assertGreater(calibration.estimate().spread_ms, 45)

    def test_negative_backward_and_rapid_duplicate_inputs_do_not_corrupt_tempo(self):
        calibration = TempoCalibrator()
        self.assertFalse(calibration.add(-1))
        for index in range(8):
            time = index * 0.5
            self.assertTrue(calibration.add(time))
            self.assertFalse(calibration.add(time))
            self.assertFalse(calibration.add(time - 0.1))
            self.assertFalse(calibration.add(time + 0.03))
        result = calibration.estimate()
        self.assertTrue(result.ready)
        self.assertEqual(result.count, 8)
        self.assertEqual(result.rejected, 25)
        self.assertAlmostEqual(result.average_bpm, 120)

    def test_invalid_numbers_raise_without_changing_capture(self):
        calibration = TempoCalibrator()
        for time in (math.nan, math.inf, -math.inf, True, None, "1"):
            with self.subTest(time=time), self.assertRaises(ValueError):
                calibration.add(time)
        self.assertEqual(calibration.estimate().count, 0)

    def test_entire_configured_bpm_range_is_supported(self):
        for bpm in (1, 2, 600):
            with self.subTest(bpm=bpm):
                calibration = TempoCalibrator()
                for index in range(16):
                    self.assertTrue(calibration.add(4.215 + index * 60 / bpm))
                result = calibration.estimate()
                self.assertTrue(result.ready)
                self.assertAlmostEqual(result.average_bpm, bpm)
                self.assertGreaterEqual(result.average_bpm, 1)
                self.assertLessEqual(result.average_bpm, 600)

    def test_intervals_slower_than_supported_range_are_not_applied(self):
        calibration = TempoCalibrator()
        for index in range(16):
            calibration.add(index * 61)
        result = calibration.estimate()
        self.assertFalse(result.ready)
        self.assertIsNone(result.average_bpm)
        self.assertEqual(result.tempo_intervals, 0)
        self.assertEqual(result.rejected, 15)

    def test_capture_is_bounded_and_follows_recent_tempo(self):
        calibration = TempoCalibrator()
        for index in range(16):
            calibration.add(index * 0.5)
        for index in range(100):
            calibration.add(10 + index * 0.6)
        result = calibration.estimate()
        self.assertEqual(result.count, 64)
        self.assertEqual(result.tempo_intervals, 63)
        self.assertTrue(result.ready)
        self.assertAlmostEqual(result.average_bpm, 100)
