"""Windows MCP fallback client: one connection per command or bounded batch."""
import argparse
import asyncio
import base64
import json
from pathlib import Path
import sys
import time
import tomllib
import uuid


def load_json(value):
    return json.loads(Path(value[1:]).read_text(encoding='utf-8-sig') if value.startswith('@') else value)


def image_to_desktop(point, image_size, region):
    """Map image pixels into virtual desktop pixels, including crop offset."""
    x, y = point
    width, height = image_size
    left, top, right, bottom = region
    if width <= 0 or height <= 0 or right <= left or bottom <= top:
        raise ValueError('Invalid image size or region')
    if not (0 <= x < width and 0 <= y < height):
        raise ValueError('Point is outside image')
    return [min(right-1, round(left+x*(right-left)/width)),
            min(bottom-1, round(top+y*(bottom-top)/height))]


def failed(result):
    return bool(getattr(result, 'is_error', getattr(result, 'isError', False)))


async def call_steps(session, steps, output, timeout):
    rows = []
    for number, step in enumerate(steps):
        started = time.perf_counter()
        result = await asyncio.wait_for(session.call_tool(step['name'], step.get('arguments', {})), timeout)
        row = {'name': step['name'], 'arguments': step.get('arguments', {}),
               'elapsed_ms': round((time.perf_counter()-started)*1000, 2),
               'is_error': failed(result), 'content': []}
        for index, block in enumerate(result.content):
            if block.type == 'image':
                mime = getattr(block, 'mime_type', getattr(block, 'mimeType', 'image/png'))
                suffix = '.jpg' if mime == 'image/jpeg' else '.png'
                path = output / f'{number:03}-{index}{suffix}'
                data = base64.b64decode(block.data)
                path.write_bytes(data)
                row['content'].append({'type': 'image', 'path': str(path), 'bytes': len(data)})
                print(f'IMAGE: {path}', flush=True)
            elif block.type == 'text':
                row['content'].append({'type': 'text', 'text': block.text})
                print(block.text, flush=True)
        rows.append(row)
        if step['name'] == 'ControlStatus':
            states = []
            for item in row['content']:
                if item['type'] == 'text':
                    try:
                        states.append(json.loads(item['text']).get('state'))
                    except (ValueError, AttributeError):
                        pass
            row['control_not_ready'] = states != ['ready']
        # Persist completed steps before raising. Never retry a desktop mutation.
        (output / 'calls.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')
        print(f"TIMING {step['name']}: {row['elapsed_ms']} ms", file=sys.stderr, flush=True)
        if row['is_error'] or row.get('control_not_ready'):
            raise RuntimeError(f"{step['name']} failed; remaining batch cancelled")
    return rows


async def main():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tool', nargs='?')
    parser.add_argument('arguments', nargs='?', default='{}')
    parser.add_argument('--batch', help='JSON array or @file of {name, arguments}; sequential, fail-fast')
    parser.add_argument('--config', type=Path, default=Path.home()/'.codex/config.toml')
    parser.add_argument('--output', type=Path, default=Path.home()/'.codex/tmp/windows-mcp')
    parser.add_argument('--timeout', type=float, default=60)
    args = parser.parse_args()
    if args.batch and args.tool:
        parser.error('Choose a tool or --batch')
    steps = load_json(args.batch) if args.batch else ([{'name':args.tool, 'arguments':load_json(args.arguments)}] if args.tool else [])
    if not isinstance(steps, list) or len(steps) > 100:
        parser.error('Batch must be an array with at most 100 steps')
    for step in steps:
        if not isinstance(step, dict) or not isinstance(step.get('name'), str) or not isinstance(step.get('arguments', {}), dict):
            parser.error('Each step needs a name and optional arguments object')
    config = tomllib.loads(args.config.read_text(encoding='utf-8'))
    server = config.get('mcp_servers', {}).get('windows-mcp')
    if not server:
        parser.error(f'windows-mcp is missing from {args.config}; pass the real host config with --config')
    output = args.output / (time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
    output.mkdir(parents=True)
    started = time.perf_counter()
    params = StdioServerParameters(command=server['command'], args=server.get('args', []), env=server.get('env'))
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await asyncio.wait_for(session.initialize(), server.get('startup_timeout_sec', 120))
            startup_ms = round((time.perf_counter()-started)*1000, 2)
            (output/'session.json').write_text(json.dumps({'startup_ms':startup_ms}), encoding='utf-8')
            print(f'TIMING startup: {startup_ms} ms; RESULTS: {output}', file=sys.stderr, flush=True)
            if not steps:
                result = await asyncio.wait_for(session.list_tools(), args.timeout)
                print(json.dumps([tool.model_dump() for tool in result.tools]))
            else:
                await call_steps(session, steps, output, args.timeout)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    asyncio.run(main())
