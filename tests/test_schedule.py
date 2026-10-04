"""core/schedule.py: slot arithmetic, retries and the earliest-deadline-first queue."""

import pathlib
import sys
import unittest
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components/radoneye_log"))
from core import schedule as S  # noqa: E402

R0 = datetime(2026, 9, 30, 12, 0, 20, tzinfo=timezone.utc)   # a computed rollover


class Slots(unittest.TestCase):
    def test_a_then_b_then_next_a(self):
        self.assertEqual(S.a_or_b_after(R0, R0), ("A", R0 + timedelta(seconds=60), R0))
        self.assertEqual(S.a_or_b_after(R0, R0 + timedelta(seconds=60)), ("B", R0 + timedelta(seconds=360), R0))
        nxt = R0 + timedelta(minutes=10)
        self.assertEqual(S.a_or_b_after(R0, R0 + timedelta(seconds=360)), ("A", nxt + timedelta(seconds=60), nxt))

    def test_works_far_from_the_anchor(self):
        kind, slot, roll = S.a_or_b_after(R0, R0 + timedelta(days=3, seconds=100))
        self.assertEqual((kind, slot), ("B", R0 + timedelta(days=3, seconds=360)))


class Retries(unittest.TestCase):
    def job(self, kind):
        return S.Job(R0 + timedelta(minutes=10), R0 + timedelta(seconds=60), "X", kind, R0 + timedelta(seconds=60), R0)

    def test_three_attempts_for_reads(self):
        j1 = self.job("A")
        j2 = S.retry_job(j1)
        j3 = S.retry_job(j2)
        self.assertEqual((j2.attempt, j2.due), (2, j1.slot + timedelta(seconds=20)))
        self.assertEqual((j3.attempt, j3.due), (3, j1.slot + timedelta(seconds=60)))
        self.assertIsNone(S.retry_job(j3))

    def test_two_attempts_for_a_pull_none_for_parallel_or_pull_now(self):
        self.assertEqual(S.retry_job(self.job("pull")).due, R0 + timedelta(seconds=120))
        self.assertIsNone(S.retry_job(S.retry_job(self.job("pull"))))
        self.assertIsNone(S.retry_job(self.job("parallel")))
        self.assertIsNone(S.retry_job(self.job("pull_now")))


class Queue(unittest.TestCase):
    def test_earliest_deadline_first_among_due_jobs(self):
        q = S.JobQueue()
        late = S.Job(R0 + timedelta(minutes=9), R0, "late", "A", R0)
        urgent = S.Job(R0 + timedelta(minutes=2), R0 + timedelta(seconds=1), "urgent", "A", R0)
        future = S.Job(R0 + timedelta(minutes=1), R0 + timedelta(hours=1), "future", "A", R0)
        for j in (late, urgent, future, urgent):
            q.put(j)
        self.assertEqual(len(q), 3)                       # duplicate ignored
        self.assertEqual(q.pop_due(R0 + timedelta(seconds=5)).serial, "urgent")
        self.assertEqual(q.pop_due(R0 + timedelta(seconds=5)).serial, "late")
        self.assertIsNone(q.pop_due(R0 + timedelta(seconds=5)))
        self.assertEqual(q.next_due(), R0 + timedelta(hours=1))
        q.drop("future")
        self.assertEqual(len(q), 0)

    def test_has_chain_ignores_parallel_jobs(self):
        q = S.JobQueue()
        q.put(S.Job(R0 + timedelta(minutes=2), R0, "A", "A", R0))
        q.put(S.Job(R0 + timedelta(minutes=10), R0 + timedelta(seconds=270), "A", "parallel", R0))
        q.put(S.Job(R0 + timedelta(minutes=10), R0 + timedelta(seconds=270), "P", "parallel", R0))
        self.assertTrue(q.has_chain("A"))
        self.assertFalse(q.has_chain("P"))                # only a parallel job queued
        self.assertFalse(q.has_chain("B"))
        self.assertEqual(q.pop_due(R0 + timedelta(seconds=5)).kind, "A")
        self.assertFalse(q.has_chain("A"))                # the parallel job remains but is not a chain


if __name__ == "__main__":
    unittest.main()
