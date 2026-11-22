"""火锅舞台的纯布局测试；不创建窗口或纹理。"""

from types import SimpleNamespace
import unittest

from rhythm_game.core import RhythmChart, RhythmSession
from rhythm_game.hotpot_ui import BeatCard, bar_cards, card_state, current_beat, prep_cards, stage_cards


def make_chart():
    return RhythmChart(90, 4, 4, 0, [4, 8, 8, 4, 4], 30, patterns=[[4, 8, 8, 4, 4]])


class HotpotLayoutTests(unittest.TestCase):
    def setUp(self):
        self.chart = make_chart()

    def test_bar_cards_group_notes_by_quarter_beat(self):
        cards = bar_cards(self.chart, 1, "preview")
        self.assertEqual(len(cards), 4)
        self.assertEqual([len(card.targets) for card in cards], [1, 2, 1, 1])
        self.assertEqual(cards[0].time, 4.0)
        self.assertEqual(cards[1].time, 4 + self.chart.beat_seconds)
        self.assertEqual(cards[0].kind, "preview")

    def test_card_state_aggregates_target_states(self):
        session = SimpleNamespace(states={}, preparation_states={})
        card = bar_cards(self.chart, 1, "formal")[1]
        self.assertIsNone(card_state(card, session))
        session.states = {card.targets[0].index: "hit"}
        self.assertEqual(card_state(card, session), "partial")
        session.states = {target.index: "hit" for target in card.targets}
        self.assertEqual(card_state(card, session), "hit")
        session.states = {card.targets[0].index: "miss"}
        self.assertEqual(card_state(card, session), "miss")

    def test_prep_and_formal_stages(self):
        session = RhythmSession(self.chart, 100, 0, game_bars=2)
        app = SimpleNamespace(session=session, chart=self.chart,
                              config=SimpleNamespace(game_bars=2))
        self.assertTrue(session.try_start(4.5))
        self.assertIsNotNone(session.entry_time)
        top, bottom, top_label, bottom_label = stage_cards(app, 6.7)
        self.assertEqual(len(top), 4)
        self.assertEqual(top[0].kind, "prep")
        self.assertEqual(bottom[0].kind, "formal")
        self.assertEqual((top_label, bottom_label), ("PREP", "FIRST BAR"))
        self.assertEqual(current_beat(top, app, 7.4), 1)
        top, bottom, top_label, _ = stage_cards(app, 10.0)
        self.assertEqual(top[0].kind, "formal")
        self.assertEqual(top[0].bar, 1)
        self.assertEqual(bottom[0].bar, 2)
        self.assertEqual(top_label, "BAR 1/2")
        self.assertEqual(current_beat(top, app, 10.0), 1)

    def test_auto_game_bars_label_uses_effective_total(self):
        session = RhythmSession(self.chart, 100, 0, game_bars=0)
        app = SimpleNamespace(session=session, chart=self.chart,
                              config=SimpleNamespace(game_bars=0))
        self.assertTrue(session.try_start(4.5))
        _, _, label, _ = stage_cards(app, session.entry_time + .1)
        self.assertGreater(session.round_game_bars, 1)
        self.assertEqual(label, f"BAR 1/{session.round_game_bars}")

    def test_rest_only_card_uses_hit_and_miss_state(self):
        chart = RhythmChart(90, 4, 4, 0, [4, "r4", 4, 4], 30,
                            patterns=[[4, "r4", 4, 4]])
        session = RhythmSession(chart, 100, game_bars=1)
        self.assertEqual(session.arm(4), 4)
        rest = session.formal_rests[0]
        card = BeatCard(rest.time, rest.bar, int(rest.beat_offset), (), "formal", (rest,))
        self.assertIsNone(card_state(card, session))
        session.rest_states[rest.index] = "hit"
        self.assertEqual(card_state(card, session), "hit")
        session.rest_states[rest.index] = "miss"
        self.assertEqual(card_state(card, session), "miss")
        cards = bar_cards(session.score_chart, 1, "formal")
        self.assertEqual(cards[1].rests, (rest,))

    def test_prep_cards_match_preparation_targets(self):
        session = RhythmSession(self.chart, 100, 0, game_bars=2)
        self.assertTrue(session.try_start(4.5))
        cards = prep_cards(session)
        self.assertEqual(len(cards), 4)
        self.assertEqual([card.targets[0] for card in cards],
                         list(session.preparation_targets))
        self.assertTrue(all(card.kind == "prep" for card in cards))

    def test_empty_beat_has_no_state(self):
        chart = RhythmChart(90, 4, 4, 0, [2, 2], 30, patterns=[[2, 2]])
        session = SimpleNamespace(states={}, preparation_states={})
        card = bar_cards(chart, 1, "formal")[1]
        self.assertEqual(card.targets, ())
        self.assertIsNone(card_state(card, session))


if __name__ == "__main__":
    unittest.main()
