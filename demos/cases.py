"""Synthetic, exact-match desktop workloads. No account or network services needed."""

FORM = {
    'name': 'Zoë García',
    'email': 'zoe.garcia+demo@example.com',
    'company': 'Northwind Research & Design',
    'role': 'Product engineer',
    'phone': '+1 (202) 555-0147',
    'city': 'Montréal',
    'reference': 'QC-2026/11 #42',
    'notes': 'Please send the “quick start” guide — thanks!',
}
EDIT_OLD = 'Draft: The shipment arrives Tuesday. Please contact the old address.'
EDIT_NEW = 'Final: The shipment arrives Friday, 13 November. Contact ops+desk@example.com — approved.'
SEARCH = {'query': 'billing', 'ticket': 'QC-204', 'note': 'Reviewed invoice #204 — please reissue.'}
EXPECTED = {'form': FORM, 'edit': {'text': EDIT_NEW}, 'search': SEARCH}


def verdict(task, actual):
    expected = EXPECTED.get(task)
    return {'task': task, 'passed': expected is not None and actual == expected,
            'actual': actual, 'expected': expected}


def actions(task):
    """Semantic actions after a screenshot-observed click in the first field.

    The driver must use real GUI input. These are deliberately not selectors or
    JavaScript snippets, and can be adapted to baseline and optimized MCP tools.
    """
    if task == 'form':
        result = []
        for value in FORM.values():
            result.extend([{'type': 'text', 'text': value}, {'type': 'key', 'key': 'tab'}])
        return result + [{'type': 'key', 'key': 'enter'}]
    if task == 'edit':
        return [{'type': 'key', 'key': 'ctrl+a'}, {'type': 'text', 'text': EDIT_NEW},
                {'type': 'key', 'key': 'tab'}, {'type': 'key', 'key': 'enter'}]
    if task == 'search':
        return [{'type': 'text', 'text': SEARCH['query']},
                {'type': 'key', 'key': 'enter'}, {'type': 'key', 'key': 'enter'},
                {'type': 'text', 'text': SEARCH['note']},
                {'type': 'key', 'key': 'tab'}, {'type': 'key', 'key': 'enter'}]
    raise ValueError(f'Unknown task: {task}')
