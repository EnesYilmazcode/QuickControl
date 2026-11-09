"""Summarize bench/results/runs.jsonl as Markdown: python bench/summarize.py [--tag main]."""
import argparse
import json
import statistics
from pathlib import Path

ARM_NAMES = {'stock': 'Windows MCP', 'qc1': 'QuickControl v1', 'qc': 'QuickControl agent tools',
             'qc-warm': 'Saved routine'}


def med(values):
    return statistics.median(values) if values else None


def fmt(value, unit='s'):
    if value is None:
        return '-'
    return f'{value:.1f} {unit}' if unit == 's' else f'{value:.0f}'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tag', default='main')
    parser.add_argument('--file', default=str(Path(__file__).parent / 'results' / 'runs.jsonl'))
    parser.add_argument('--export', help='Write compact rows (all tags) to this JSONL path')
    args = parser.parse_args()
    rows = [json.loads(line) for line in open(args.file, encoding='utf-8') if line.strip()]
    if args.export:
        keep = ('task', 'arm', 'rep', 'tag', 'model', 'started', 'passed', 'agent_s', 'api_s', 'turns',
                'tool_calls', 'tools', 'cost_usd', 'input_tokens', 'output_tokens', 'interrupted_calls',
                'check', 'answer')
        with open(args.export, 'w', encoding='utf-8') as out:
            for row in rows:
                out.write(json.dumps({k: row.get(k) for k in keep}, ensure_ascii=False) + '\n')
    rows = [r for r in rows if r.get('tag') == args.tag]
    arms = [a for a in ('stock', 'qc1', 'qc', 'qc-warm') if any(r['arm'] == a for r in rows)]
    tasks = list(dict.fromkeys(r['task'] for r in rows))
    print('| Task | ' + ' | '.join(ARM_NAMES[a] for a in arms) + ' |')
    print('| --- | ' + ' | '.join('---:' for _ in arms) + ' |')
    for task in tasks:
        cells = []
        for arm in arms:
            runs = [r for r in rows if r['task'] == task and r['arm'] == arm]
            passed = [r for r in runs if r['passed']]
            time = med([r['agent_s'] for r in passed])
            turns = med([r['turns'] for r in passed])
            cells.append(f"{len(passed)}/{len(runs)} pass, {fmt(time)}, {fmt(turns, 'n')} turns" if passed
                         else f'0/{len(runs)} pass')
        print(f'| {task} | ' + ' | '.join(cells) + ' |')
    print()
    print('| Arm | Passed | Median time (passing) | Median turns | Model share | Total time | Total cost | Interrupted runs |')
    print('| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |')
    for arm in arms:
        runs = [r for r in rows if r['arm'] == arm]
        passed = [r for r in runs if r['passed']]
        print(f"| {ARM_NAMES[arm]} | {len(passed)}/{len(runs)} | {fmt(med([r['agent_s'] for r in passed]))} | "
              f"{fmt(med([r['turns'] for r in passed]), 'n')} | "
              f"{100 * sum(r['api_s'] for r in runs) / max(1e-9, sum(r['agent_s'] for r in runs)):.0f}% | "
              f"{sum(r['agent_s'] for r in runs):.0f} s | "
              f"${sum(r['cost_usd'] or 0 for r in runs):.2f} | "
              f"{sum(1 for r in runs if r.get('interrupted_calls'))} |")


if __name__ == '__main__':
    main()
