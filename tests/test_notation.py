"""音符文本与精确时值的纯软件检查。"""

from dataclasses import FrozenInstanceError
from fractions import Fraction
import unittest

from rhythm_game.notation import RhythmNote, format_pattern, normalize_pattern, parse_note, parse_pattern


class NotationTests(unittest.TestCase):
    def test_regular_and_dotted_exact_durations(self):
        self.assertEqual(parse_note(16).beats, Fraction(1, 4))
        self.assertEqual(parse_note("8.").beats, Fraction(3, 4))
        self.assertEqual(parse_note("8..").beats, Fraction(7, 8))
        self.assertEqual(parse_note("8...").beats, Fraction(15, 16))
        self.assertEqual(parse_note("64").beats, Fraction(1, 16))
        self.assertEqual(parse_note(" 08. ").token, "8.")
        self.assertEqual(parse_note("16").token, 16)

    def test_immutable_validated_note(self):
        note = RhythmNote(8, 1)
        with self.assertRaises(FrozenInstanceError):
            note.dots = 2
        for denominator, dots in ((True, 0), (8.0, 0), (3, 0), (8, True), (8, -1), (8, 4)):
            with self.subTest(denominator=denominator, dots=dots), self.assertRaises(ValueError):
                RhythmNote(denominator, dots)

    def test_rejects_nonstandard_or_noninteger_tokens(self):
        for value in (True, False, 8.0, None, [], "", "3", "128", "8....", "8 .", "8d", "1/8", "８", "+8", "-8"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_note(value)

    def test_canonical_pattern_and_mixed_three_beat_bar(self):
        self.assertEqual(normalize_pattern(["8.", "16", 8, 16, 16, 16, 16, 8], 3),
                         ("8.", 16, 8, 16, 16, 16, 16, 8))
        self.assertEqual(normalize_pattern(["4..", 16], 2), ("4..", 16))
        self.assertEqual(normalize_pattern(["8...", 64], 1), ("8...", 64))
        self.assertEqual(normalize_pattern([16] * 16, 4), (16,) * 16)

    def test_bar_total_is_exact_not_rounded(self):
        for pattern, beats in (([8, 16], 1), ([4], 3), ([], 4), ([64] * 15, 1), ([4], True), ([4], 1.0)):
            with self.subTest(pattern=pattern, beats=beats), self.assertRaises(ValueError):
                normalize_pattern(pattern, beats)
        for value in (None, "4,4", 4):
            with self.assertRaises(ValueError):
                normalize_pattern(value, 4)

    def test_multi_bar_parser_preserves_independent_patterns(self):
        text = "8.,16 4，4 | 16, 16,8 4 4"
        patterns = parse_pattern(text)
        self.assertEqual(patterns, (("8.", 16, 4, 4), (16, 16, 8, 4, 4)))
        self.assertEqual(parse_pattern(format_pattern(patterns)), patterns)
        self.assertEqual(parse_pattern("8 16"), ((8, 16),))  # 由调用方按当前拍号检查。
        for value in ("", " | 4", "4 |", "4 || 4", "4|,|4", "8.x", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_pattern(value)


if __name__ == "__main__":
    unittest.main()
