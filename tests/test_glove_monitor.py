import unittest

from tools.monitor_glove import parse_line, timing_report


class GloveMonitorTests(unittest.TestCase):
    def test_protocol_and_invalid_timestamps(self):
        self.assertEqual(parse_line("READY"), {"type": "ready"})
        self.assertEqual(parse_line("1,4294967295"), {"type": "contact", "device_ms": 4294967295})
        self.assertEqual(parse_line("0,25"), {"type": "release", "device_ms": 25})
        for line in ("1,-1", "1,4294967296", "1,２", "2,100", "0,10,20", "R", "1," + "9" * 5000):
            with self.subTest(line=line):
                self.assertEqual(parse_line(line)["type"], "invalid")

    def test_constant_unknown_delay_is_not_reported_as_absolute_latency(self):
        rows = [{"device_ms": d, "host_monotonic": 500 + d / 1000} for d in (100, 200, 300)]
        result = timing_report(rows)[0]
        self.assertEqual(result["relative_delay_range_ms"], 0)

    def test_batched_delivery_is_visible(self):
        rows = [{"device_ms": d, "host_monotonic": h} for d, h in
                ((100, 10.100), (200, 10.230), (220, 10.230), (300, 10.300))]
        result = timing_report(rows)[0]
        self.assertAlmostEqual(result["relative_delay_range_ms"], 30)
        self.assertAlmostEqual(result["largest_interval_difference_ms"], 30)

    def test_ready_and_unannounced_reset_split_clock_segments(self):
        rows = [{"device_ms": 500, "host_monotonic": 10},
                {"device_ms": 600, "host_monotonic": 10.1}, {"type": "ready"},
                {"device_ms": 0, "host_monotonic": 20},
                {"device_ms": 100, "host_monotonic": 20.1},
                {"device_ms": 0, "host_monotonic": 30},
                {"device_ms": 100, "host_monotonic": 30.1}]
        self.assertEqual([s["events"] for s in timing_report(rows)], [2, 2, 2])

    def test_uint32_rollover_preserves_intervals(self):
        rows = [{"device_ms": 0xFFFFFFF0, "host_monotonic": 10},
                {"device_ms": 16, "host_monotonic": 10.032}]
        self.assertEqual(timing_report(rows)[0]["relative_delay_range_ms"], 0)


if __name__ == "__main__":
    unittest.main()
