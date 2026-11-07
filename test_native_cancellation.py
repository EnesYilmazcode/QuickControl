"""Integrate the worker bridge with actual chunked input, without OS injection."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from rapid_input import type_text
from test_rapid_input import Ledger
from uia_context import build_runner


class NativeCancellationTests(unittest.TestCase):
    def test_cancelled_native_chunk_drains_and_stops_before_next_chunk(self):
        async def exercise():
            entered=threading.Event()
            release=threading.Event()
            sent=[]
            owner=SimpleNamespace(input_ledger=Ledger(),checkpoint_current=Mock(),
                record_step_current=Mock(),physical_key_down=lambda _:False)
            def sender(events):
                sent.append(events)
                entered.set()
                if not release.wait(2): raise TimeoutError('Test native batch was not released')
                return len(events)
            desktop=SimpleNamespace(_validate_screen_point=Mock())
            uia=SimpleNamespace(Click=Mock())
            with ThreadPoolExecutor(max_workers=1) as executor:
                runner=build_runner(executor,lambda:False)
                task=asyncio.create_task(runner(type_text,desktop,[1,2],'a'*100,
                    owner=owner,uia=uia,sender=sender,as_bool=lambda value,_:value))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait,1))
                    task.cancel()
                    await asyncio.sleep(.01)
                    self.assertFalse(task.done(),'Control gate was released while native work was active')
                finally:
                    release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertEqual(len(sent),1,'Another text chunk ran after cancellation')
            self.assertFalse(owner.input_ledger.holds)
        asyncio.run(exercise())


if __name__=='__main__':
    unittest.main()
