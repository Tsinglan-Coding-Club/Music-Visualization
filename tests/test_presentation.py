import unittest

from rhythm_game.core import RhythmChart, RhythmSession
from rhythm_game.presentation import preparation_indicator


class PreparationPresentationTests(unittest.TestCase):
    def make_session(self, beats=4):
        chart = RhythmChart(120, beats, 0, 0, [4] * beats, 100)
        session = RhythmSession(chart, 100, game_bars=2, max_mistakes=3)
        self.assertTrue(session.try_start(.2))
        return session

    def test_clock_drives_four_three_two_one_without_any_input(self):
        session = self.make_session()
        start = session.preparation_start_time
        for index in range(4):
            with self.subTest(index=index):
                indicator = preparation_indicator(session, start + index * .5)
                self.assertEqual(indicator.remaining, 4 - index)
                self.assertEqual(indicator.current, index)
                self.assertEqual(indicator.lights[index], "current")
                self.assertEqual(sum(light.startswith("current") for light in indicator.lights), 1)
                self.assertEqual(indicator.lights[:index], ("past",) * index)
                self.assertIsNone(session.accuracy)
        self.assertIsNone(preparation_indicator(session, session.entry_time))

    def test_boundaries_do_not_skip_or_start_count_early(self):
        session = self.make_session()
        start = session.preparation_start_time
        self.assertIsNone(preparation_indicator(session, start - .001))
        self.assertEqual(preparation_indicator(session, start + .499999).remaining, 4)
        self.assertEqual(preparation_indicator(session, start + .5).remaining, 3)
        self.assertEqual(preparation_indicator(session, session.entry_time - .001).remaining, 1)

    def test_triple_meter_and_hits_keep_clock_as_current_beat(self):
        session = self.make_session(3)
        start = session.preparation_start_time
        session.judge(start)
        first = preparation_indicator(session, start)
        self.assertEqual(first.remaining, 3)
        self.assertEqual(first.lights, ("current_hit", "pending", "pending"))
        next_beat = preparation_indicator(session, start + .5)
        self.assertEqual(next_beat.remaining, 2)
        self.assertEqual(next_beat.lights, ("hit", "current", "pending"))
        self.assertEqual(session.hits, 0)
        self.assertIsNone(session.accuracy)

    def test_no_indicator_outside_preparation_or_after_failure(self):
        session = self.make_session()
        self.assertIsNone(preparation_indicator(session, 0))
        session.advance(session.entry_time + 1.2)
        self.assertTrue(session.failed)
        self.assertIsNone(preparation_indicator(session, session.preparation_start_time))


if __name__ == "__main__":
    unittest.main()
