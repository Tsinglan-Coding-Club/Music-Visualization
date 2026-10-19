import unittest

from rhythm_game.glove import ContactDebouncer


class ContactDebouncerTests(unittest.TestCase):
    def ready_filter(self):
        result = ContactDebouncer(30)
        result.reset(released=True)
        return result

    def test_short_contact_bounce_does_not_create_clap(self):
        d = self.ready_filter()
        for stamp, state in ((0, True), (.025, False), (.050, True), (.075, False)):
            self.assertEqual(d.feed(state, stamp), [])
        self.assertEqual(d.advance(.200), [])

    def test_preserves_edge_timestamp_after_confirmation(self):
        d = self.ready_filter()
        self.assertEqual(d.feed(True, 10, 4000), [])
        self.assertEqual(d.advance(10.029), [])
        event, = d.advance(10.030)
        self.assertTrue(event.contacting)
        self.assertEqual(event.onset_time, 10)
        self.assertEqual(event.device_ms, 4000)
        self.assertEqual(event.confirmed_time, 10.030)
        self.assertEqual(d.advance(10.100), [])

    def test_hold_and_firmware_timeout_pulse_do_not_retrigger(self):
        d = self.ready_filter()
        d.feed(True, 0)
        self.assertEqual(len(d.advance(.030)), 1)
        self.assertEqual(d.feed(False, 5.001), [])
        self.assertEqual(d.feed(True, 5.026), [])
        self.assertEqual(d.advance(6), [])
        self.assertEqual(d.feed(True, 7), [])
        self.assertEqual(d.advance(10), [])

    def test_stable_release_rearms_then_next_contact_emits_once(self):
        d = self.ready_filter()
        d.feed(True, 0)
        d.advance(.030)
        d.feed(False, .100)
        release, = d.advance(.130)
        self.assertFalse(release.contacting)
        d.feed(True, .200)
        clap, = d.advance(.230)
        self.assertTrue(clap.contacting)

    def test_120_bpm_sixteenth_notes_with_40ms_contacts_survive(self):
        d = self.ready_filter()
        transitions = []
        for index in range(16):
            start = index * .125
            transitions += d.feed(True, start)
            transitions += d.advance(start + .030)
            transitions += d.feed(False, start + .040)
            transitions += d.advance(start + .070)
        self.assertEqual(sum(t.contacting for t in transitions), 16)
        self.assertEqual(sum(not t.contacting for t in transitions), 16)

    def test_same_state_packets_do_not_extend_candidate(self):
        d = self.ready_filter()
        d.feed(True, 0)
        d.feed(True, .020)
        self.assertEqual(len(d.advance(.030)), 1)

    def test_start_or_invalid_data_requires_stable_release(self):
        d = ContactDebouncer()
        d.feed(True, 0)
        self.assertEqual(d.advance(.030), [])
        self.assertEqual(d.advance(1), [])
        d.feed(False, 2)
        d.advance(2.030)
        d.feed(True, 3)
        self.assertEqual(len(d.advance(3.030)), 1)
        d.reset()
        d.feed(True, 4)
        self.assertEqual(d.advance(4.030), [])

    def test_invalid_time_and_parameters(self):
        for value in (-1, float('nan'), float('inf'), True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ContactDebouncer(value)
        d = self.ready_filter()
        d.advance(1)
        with self.assertRaises(ValueError):
            d.feed(True, .9)
        with self.assertRaises(ValueError):
            d.advance(float('nan'))


if __name__ == '__main__':
    unittest.main()
