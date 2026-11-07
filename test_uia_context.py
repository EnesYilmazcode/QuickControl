"""Worker-context and cancellation contracts, without Windows or desktop input."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import contextvars
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from uia_context import InputCancelled, build_runner, check_cancelled, install


class UIAContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_preserves_context_on_existing_single_worker(self):
        token = contextvars.ContextVar('test_token', default=None)
        token.set(123)
        with ThreadPoolExecutor(max_workers=1) as executor:
            runner = build_runner(executor, lambda: False)
            first = await runner(lambda: (token.get(), threading.get_ident()))
            second = await runner(lambda: (token.get(), threading.get_ident()))
        self.assertEqual(first[0], 123)
        self.assertEqual(first, second)
        self.assertNotEqual(first[1], threading.get_ident())

    async def test_cancel_does_not_finish_gate_until_worker_cooperatively_stops(self):
        started = threading.Event()
        allow_checkpoint = threading.Event()
        stopped = threading.Event()
        steps = []
        def worker():
            started.set()
            try:
                if not allow_checkpoint.wait(2):
                    raise RuntimeError('test worker timed out')
                check_cancelled()
                steps.append('must not inject')
            finally:
                stopped.set()
        with ThreadPoolExecutor(max_workers=1) as executor:
            runner = build_runner(executor, lambda: False)
            task = asyncio.create_task(runner(worker))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 1))
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                self.assertFalse(stopped.is_set())
                task.cancel()  # A second cancellation still cannot release gate.
                await asyncio.sleep(0)
                self.assertFalse(task.done())
            finally:
                allow_checkpoint.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertTrue(stopped.is_set())
            self.assertEqual(steps, [])
            # A new invocation must not inherit the cancelled event.
            self.assertEqual(await runner(lambda: (check_cancelled(), 'ready')[1]), 'ready')

    async def test_worker_exception_propagates_without_retry(self):
        error = RuntimeError('native failure')
        calls = []
        def worker():
            calls.append(1)
            raise error
        with ThreadPoolExecutor(max_workers=1) as executor:
            runner = build_runner(executor, lambda: False)
            with self.assertRaises(RuntimeError) as caught:
                await runner(worker)
        self.assertIs(caught.exception, error)
        self.assertEqual(calls, [1])

    async def test_already_affine_call_stays_on_current_thread(self):
        runner = build_runner(None, lambda: True)
        self.assertEqual(await runner(threading.get_ident), threading.get_ident())
        check_cancelled()  # No cancellation event outside a worker invocation.

    async def test_cancelled_queued_work_cannot_begin_input(self):
        release_busy = threading.Event()
        started_busy = threading.Event()
        calls = []
        with ThreadPoolExecutor(max_workers=1) as executor:
            def busy():
                started_busy.set()
                release_busy.wait(2)
            busy_future = executor.submit(busy)
            runner = build_runner(executor, lambda: False)
            task = asyncio.create_task(runner(lambda: calls.append('must not start')))
            try:
                self.assertTrue(await asyncio.to_thread(started_busy.wait, 1))
                await asyncio.sleep(0)
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
            finally:
                release_busy.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                busy_future.result(timeout=1)
        self.assertEqual(calls, [])

    async def test_install_updates_exact_aliases_and_is_idempotent(self):
        async def original():
            pass
        affinity = SimpleNamespace(run_uia_affine=original, _executor=object(), is_uia_thread=lambda: False)
        analytics = SimpleNamespace(run_uia_affine=original)
        infrastructure = SimpleNamespace(thread_affinity=affinity, run_uia_affine=original)
        modules = {'windows_mcp': SimpleNamespace(infrastructure=infrastructure),
                   'windows_mcp.infrastructure': infrastructure,
                   'windows_mcp.infrastructure.thread_affinity': affinity,
                   'windows_mcp.infrastructure.analytics': analytics}
        with patch.dict('sys.modules', modules):
            adapted = install()
            self.assertIs(install(), adapted)
        self.assertIs(affinity.run_uia_affine, adapted)
        self.assertIs(analytics.run_uia_affine, adapted)
        self.assertIs(infrastructure.run_uia_affine, adapted)


if __name__ == '__main__':
    unittest.main()
