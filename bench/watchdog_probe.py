"""Does one stock Snapshot make Windows MCP's control watchdog fail open?

Starts a stock server, checks ControlStatus, calls Snapshot, then polls
ControlStatus for a few seconds. Read-only: no input is sent.
"""
import asyncio
import json
import sys
import time
from pathlib import Path

PY = str(Path(__file__).resolve().parent / '.venv' / 'Scripts' / 'python.exe')


async def main(server_args):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    params = StdioServerParameters(command=PY, args=server_args)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            async def status():
                response = await session.call_tool('ControlStatus', {})
                return json.loads(response.content[0].text)['state']
            print('before', await status(), flush=True)
            tool, args = (('Move', {'loc': [1500, 600]}) if 'move' in sys.argv else ('Snapshot', {}))
            for call in range(3):
                started = time.perf_counter()
                if 'combo' in sys.argv:
                    tool = 'Snapshot+Move'
                    await session.call_tool('Snapshot', {'use_vision': True})
                    response = await session.call_tool('Move', {'loc': [1500, 600 + call]})
                else:
                    response = await session.call_tool(tool, args)
                text = ' '.join(b.text for b in response.content if b.type == 'text')
                print(f'{tool} {call + 1}: {time.perf_counter() - started:.1f} s',
                      'error' if 'CONTROL_' in text else 'ok', flush=True)
                states = []
                for _ in range(6):
                    states.append(await status())
                    await asyncio.sleep(0.5)
                print('  status after:', states, flush=True)


if __name__ == '__main__':
    rest = [a for a in sys.argv[1:] if a not in ('move', 'combo')]
    asyncio.run(main(rest or ['-m', 'windows_mcp', 'serve']))
