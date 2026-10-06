"""core/timing.py: the cap covers connect+read only; disconnect is bounded separately and never loses a read."""

import asyncio
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components/reliable_radoneye"))
from core.timing import capped_then_close  # noqa: E402


def run(coro):
    return asyncio.run(coro)


class CappedThenClose(unittest.TestCase):
    def setUp(self):
        self.closed = 0

    def _work(self, delay, result="ok", exc=None):
        async def work():
            await asyncio.sleep(delay)
            if exc:
                raise exc
            return result
        return work

    def _close(self, delay, exc=None):
        async def close():
            await asyncio.sleep(delay)
            self.closed += 1
            if exc:
                raise exc
        return close

    def test_slow_close_does_not_discard_result(self):
        # the live bug: work finished inside the cap, disconnect ran past it
        r = run(capped_then_close(self._work(0.05), self._close(0.2), cap_s=0.15, close_cap_s=0.5))
        self.assertEqual(r, "ok")
        self.assertEqual(self.closed, 1)

    def test_close_timeout_still_returns_result(self):
        async def hang():
            await asyncio.sleep(10)
        r = run(capped_then_close(self._work(0), hang, cap_s=1, close_cap_s=0.05))
        self.assertEqual(r, "ok")

    def test_close_error_still_returns_result(self):
        r = run(capped_then_close(self._work(0), self._close(0, RuntimeError("x")), cap_s=1, close_cap_s=1))
        self.assertEqual(r, "ok")

    def test_work_timeout_raises_and_still_closes(self):
        with self.assertRaises(asyncio.TimeoutError):
            run(capped_then_close(self._work(1), self._close(0), cap_s=0.05, close_cap_s=1))
        self.assertEqual(self.closed, 1)

    def test_work_error_propagates_and_closes(self):
        with self.assertRaises(ValueError):
            run(capped_then_close(self._work(0, exc=ValueError("v")), self._close(0), cap_s=1, close_cap_s=1))
        self.assertEqual(self.closed, 1)

    def test_worst_case_total_is_cap_plus_close_cap(self):
        loop_t = []

        async def go():
            t0 = asyncio.get_running_loop().time()
            with self.assertRaises(asyncio.TimeoutError):
                async def hang():
                    await asyncio.sleep(10)
                await capped_then_close(self._work(10), hang, cap_s=0.1, close_cap_s=0.1)
            loop_t.append(asyncio.get_running_loop().time() - t0)
        run(go())
        self.assertGreaterEqual(loop_t[0], 0.19)   # both bounds were used: close really ran (and hung)
        self.assertLess(loop_t[0], 0.35)

    def test_cancellation_still_closes_and_propagates(self):
        async def go():
            async def hang():
                await asyncio.sleep(10)
            task = asyncio.ensure_future(capped_then_close(hang, self._close(0), cap_s=5, close_cap_s=1))
            await asyncio.sleep(0.05)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        run(go())
        self.assertEqual(self.closed, 1)


if __name__ == "__main__":
    unittest.main()
