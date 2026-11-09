"""Print an agent run's tool calls and results: python bench/trace.py <run dir>."""
import json, sys
for line in open(sys.argv[1] + '/stream.jsonl', encoding='utf-8'):
    e = json.loads(line)
    if e.get('type') == 'assistant':
        for b in e['message']['content']:
            if b.get('type') == 'tool_use':
                print('CALL', b['name'].replace('mcp__wm__', ''), json.dumps(b['input'])[:260])
            elif b.get('type') == 'text':
                print('TEXT', b['text'][:200])
    if e.get('type') == 'user':
        for b in e['message']['content']:
            if isinstance(b, dict) and b.get('type') == 'tool_result':
                c = b['content']
                t = c if isinstance(c, str) else ' '.join(x.get('text', '') for x in c if x.get('type') == 'text')
                print('  ->', t[:int(sys.argv[2]) if len(sys.argv) > 2 else 160].replace('\n', ' / '))
