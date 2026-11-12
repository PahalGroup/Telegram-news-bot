import unittest
from datetime import datetime

from scripts.actions_shift import IST, shift_seconds


def at(h, m=0):
    return datetime(2026, 11, 4, h, m, tzinfo=IST)


class ActionsShiftWindow(unittest.TestCase):
    def test_shifts_chain_across_the_day(self):
        self.assertEqual(shift_seconds(at(7)), 50 * 60)                # 50-min chunk, then chain
        self.assertEqual(shift_seconds(at(13)), 50 * 60)
        self.assertEqual(shift_seconds(at(19)), 50 * 60)

    def test_late_start_still_ends_at_the_handoff(self):
        self.assertEqual(shift_seconds(at(7, 40)), 50 * 60)

    def test_quiet_hours(self):
        for h, m in ((23, 30), (23, 45), (0, 5), (3, 5), (6, 59)):
            self.assertEqual(shift_seconds(at(h, m)), 0)
        self.assertEqual(shift_seconds(at(23, 0)), 1800)

    def test_never_exceeds_job_limit(self):
        self.assertLessEqual(shift_seconds(at(7)), 50 * 60)
