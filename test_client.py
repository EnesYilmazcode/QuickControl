import asyncio
import contextlib
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from quickcontrol import call_steps, image_to_desktop


class ClientTests(unittest.TestCase):
    def test_user_control_stops_batch(self):
        class Session:
            async def call_tool(self, name, args):
                self.assert_status = name
                if name != 'ControlStatus': raise AssertionError('Action ran while user owned control')
                return SimpleNamespace(is_error=False, content=[SimpleNamespace(type='text', text='{"state":"user_control","wait_seconds":2}')])
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(RuntimeError):
                asyncio.run(call_steps(Session(), [{'name':'ControlStatus'}, {'name':'Click'}], Path(directory), 1))

    def test_snake_case_sdk_image_and_error_fields(self):
        class Session:
            async def call_tool(self, name, args):
                return SimpleNamespace(is_error=False, content=[SimpleNamespace(type='image', mime_type='image/jpeg', data='aGVsbG8=')])
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            asyncio.run(call_steps(Session(), [{'name':'Screenshot'}], Path(directory), 1))
            self.assertEqual((Path(directory)/'000-0.jpg').read_bytes(), b'hello')

    def test_coordinate_mapping(self):
        self.assertEqual(image_to_desktop((100,50),(960,540),(1920,0,3840,1080)), [2120,100])
        self.assertEqual(image_to_desktop((10,20),(670,360),(390,240,1060,600)), [400,260])
        self.assertEqual(image_to_desktop((0,0),(100,100),(-1920,-200,-1820,-100)), [-1920,-200])
        with self.assertRaises(ValueError):
            image_to_desktop((100,0),(100,100),(0,0,100,100))

    def test_batch_stops_on_failure_without_retry(self):
        class Session:
            def __init__(self): self.calls = []
            async def call_tool(self, name, args):
                self.calls.append(name)
                return SimpleNamespace(isError=name=='bad', content=[])
        session = Session()
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(RuntimeError):
                asyncio.run(call_steps(session, [{'name':name} for name in ['ok','bad','never']], Path(directory), 1))
            self.assertTrue((Path(directory)/'calls.json').exists())
        self.assertEqual(session.calls, ['ok','bad'])


if __name__ == '__main__': unittest.main()
