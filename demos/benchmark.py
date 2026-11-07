"""Outcome-verified timing helpers for live Windows MCP GUI experiments.

Navigation, screenshot inspection and observed anchor selection happen before
measure_task. The caller supplies dispatch, so the exact same workload can use
individual upstream tools or an optimized server Sequence without DOM input.
"""
import argparse
import asyncio
import json
from pathlib import Path
import statistics
import time
from urllib.parse import urlencode
from urllib.request import urlopen

try:
    from .cases import actions
except ImportError:
    from cases import actions


def fetch_result(base_url, run):
    with urlopen(base_url.rstrip('/') + '/api/result?' + urlencode({'run': run}), timeout=3) as response:
        return json.load(response)


async def measure_task(*, profile, task, run, base_url, dispatch, anchor, evidence=None):
    """Time observed initial click through exact server-verified submission.

    dispatch receives semantic GUI actions, beginning with an observed desktop
    click. It must await completion and raise on any tool failure. No browser
    selector or DOM mutation belongs in a driver. Unique run IDs prevent stale
    success being reused. Warm tool time and outcome latency are both reported.
    """
    prior = await asyncio.to_thread(fetch_result, base_url, run)
    if prior.get('submitted'):
        raise ValueError('Run already submitted; navigate to a fresh run ID')
    started = time.perf_counter()
    tool_result = await dispatch([{'type': 'click', 'x': anchor[0], 'y': anchor[1]}] + actions(task))
    tools_done = time.perf_counter()
    deadline = tools_done + 3
    while True:
        result = await asyncio.to_thread(fetch_result, base_url, run)
        if result.get('submitted') or time.perf_counter() >= deadline:
            break
        await asyncio.sleep(.02)
    ended = time.perf_counter()
    return {'profile': profile, 'task': task, 'run': run, 'passed': result.get('passed', False),
            'tool_ms': round((tools_done-started)*1000, 3),
            'verified_ms': round((ended-started)*1000, 3),
            'anchor': list(anchor), 'result': result, 'driver': tool_result,
            'evidence': evidence,
            'scope': 'Warm GUI input through exact local submission; excludes startup, navigation and agent reasoning'}


def summarize(rows):
    groups = {}
    for row in rows:
        key = row['profile'], row['task']
        groups.setdefault(key, []).append(row)
    summary = []
    for (profile, task), values in sorted(groups.items()):
        successes = [v for v in values if v['passed']]
        timings = [v['verified_ms'] for v in successes]
        summary.append({'profile': profile, 'task': task, 'attempts': len(values),
                        'passed': len(successes), 'failed': len(values)-len(successes),
                        'median_verified_ms': round(statistics.median(timings), 3) if timings else None,
                        'min_verified_ms': min(timings) if timings else None,
                        'max_verified_ms': max(timings) if timings else None})
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', choices=['form', 'edit', 'search'])
    parser.add_argument('--summarize', type=Path)
    args = parser.parse_args()
    if args.plan:
        print(json.dumps(actions(args.plan), ensure_ascii=False, indent=2))
    elif args.summarize:
        print(json.dumps(summarize(json.loads(args.summarize.read_text(encoding='utf-8'))), indent=2))
    else:
        parser.error('Choose --plan or --summarize')
