"""Realistic agent tasks. Each has a start state, a prompt, and an independent check.

Checks read the browser over the run's local debug port, or the Calculator
display through UIA. The agent's own claim of success is never trusted.
"""
import json
import re
from urllib.request import urlopen

PORT = 9333


def page_eval(expression, contains=''):
    import websocket
    with urlopen(f'http://127.0.0.1:{PORT}/json/list', timeout=3) as response:
        pages = [t for t in json.load(response) if t.get('type') == 'page' and contains in t.get('url', '')]
    if not pages:
        return None, None
    ws = websocket.create_connection(pages[0]['webSocketDebuggerUrl'], timeout=5,
                                     origin=f'http://localhost:{PORT}')
    try:
        ws.send(json.dumps({'id': 1, 'method': 'Runtime.evaluate',
                            'params': {'expression': expression, 'returnByValue': True}}))
        while True:
            message = json.loads(ws.recv())
            if message.get('id') == 1:
                return pages[0]['url'], message['result']['result'].get('value')
    finally:
        ws.close()


def norm(text):
    return re.sub(r'[\s,]', '', (text or '').lower())


def check_sauce(answer):
    url, text = page_eval('document.body.innerText')
    ok_page = bool(url and 'checkout-step-two' in url)
    items = bool(text and 'Sauce Labs Onesie' in text and 'Sauce Labs Bike Light' in text
                 and text.count('QTY') <= 1 and 'Backpack' not in text)
    total = bool(text and '19.42' in text)
    named = bool(text and 'Total: $19.42' in text)
    return {'passed': ok_page and items and total and '19.42' in (answer or ''),
            'url': url, 'items_ok': items, 'total_on_page': named, 'answer_has_total': '19.42' in (answer or '')}


def check_todo(answer):
    url, rows = page_eval("[location.hash, [...document.querySelectorAll('.todo-list li')].map(li => [li.innerText.trim(), li.className]), localStorage.getItem('react-todos')]")
    if not rows:
        return {'passed': False, 'reason': 'no page'}
    hashpart, visible, stored = rows
    try:
        todos = json.loads(stored or '[]')
    except Exception:
        todos = []
    titles = {t.get('title'): t.get('completed') for t in todos}
    want = {'Buy milk': False, 'Email Sam': True, 'Book dentist': False, 'Pay rent': True}
    stored_ok = titles == want
    visible_titles = sorted(v[0] for v in visible)
    view_ok = hashpart == '#/active' and visible_titles == ['Book dentist', 'Buy milk']
    return {'passed': view_ok and (stored_ok or not todos), 'hash': hashpart, 'visible': visible_titles,
            'stored': titles}


def check_github(answer):
    url, top = page_eval("(() => { const a = document.querySelector('[data-testid=\"results-list\"] h3 a, .search-title a'); return a ? a.innerText.trim() : null })()", 'github.com/search')
    from urllib.parse import parse_qs, urlsplit
    query = parse_qs(urlsplit(url or '').query) if url else {}
    q = (query.get('q') or [''])[0].lower()
    criteria = ('mcp' in q and 'server' in q and 'language:python' in q and 'stars:>1000' in q.replace(' ', ''))
    sort_ok = (query.get('s') or [''])[0] == 'stars' and (query.get('o') or ['desc'])[0] == 'desc'
    answer_ok = bool(top) and norm(top) in norm(answer)
    return {'passed': criteria and sort_ok and answer_ok, 'url': url, 'top': top,
            'criteria_ok': criteria, 'sort_ok': sort_ok, 'answer_ok': answer_ok}


def check_wiki(answer):
    low = (answer or '').lower()
    url, _ = page_eval('1', 'wikipedia.org')
    return {'passed': '1870' in low and 'scarlet' in low and ('gray' in low or 'grey' in low),
            'url': url}


def check_demoqa(answer):
    url, text = page_eval("(() => { const m = document.querySelector('.modal-content'); return m ? m.innerText : '' })()", 'demoqa')
    text = text or ''
    want = ['Grace Hopper', 'grace@navy.mil', 'Female', '6145550123', 'Reading', 'NCR Delhi']
    present = {w: (w in text) for w in want}
    return {'passed': 'Thanks for submitting the form' in text and all(present.values()), 'fields': present}


def check_calc(answer):
    import subprocess
    script = (r"Add-Type -AssemblyName UIAutomationClient; "
              r"$w=[Windows.Automation.AutomationElement]::RootElement.FindFirst('Children',"
              r"(New-Object Windows.Automation.PropertyCondition([Windows.Automation.AutomationElement]::NameProperty,'Calculator')));"
              r"$d=$w.FindFirst('Descendants',(New-Object Windows.Automation.PropertyCondition([Windows.Automation.AutomationElement]::AutomationIdProperty,'CalculatorResults')));"
              r"$d.Current.Name")
    display = subprocess.run(['powershell', '-NoProfile', '-Command', script], capture_output=True,
                             text=True, timeout=30).stdout.strip()
    return {'passed': '68315' in norm(display) and '68315' in norm(answer), 'display': display}


TASKS = {
    'sauce': {
        'url': 'https://www.saucedemo.com/',
        'prompt': ('Log in to this Swag Labs store as standard_user with password secret_sauce. '
                   'Sort the products by price from low to high, add the two cheapest products to the cart, '
                   'then check out with first name Ada, last name Lovelace, ZIP code 43210. Stop on the '
                   'checkout overview page (do not click Finish) and tell me the order total.'),
        'check': check_sauce,
    },
    'todo': {
        'url': 'https://todomvc.com/examples/react/dist/',
        'prompt': ('In this TodoMVC app, add four todos: "Buy milk", "Email Sam", "Book dentist", "Pay rent". '
                   'Then mark "Email Sam" and "Pay rent" as completed and switch the list filter to Active.'),
        'check': check_todo,
    },
    'github': {
        'url': 'https://github.com/search/advanced',
        'prompt': ('Use this GitHub advanced search page to find repositories written in Python that match '
                   '"mcp server" and have more than 1000 stars, sorted by most stars. '
                   'Tell me the full name (owner/repo) of the top result.'),
        'check': check_github,
    },
    'wiki': {
        'url': 'https://en.wikipedia.org/wiki/Main_Page',
        'prompt': ('Using Wikipedia in this browser, find the year The Ohio State University was founded '
                   'and its official school colors. Tell me both.'),
        'check': check_wiki,
    },
    'demoqa': {
        'url': 'https://demoqa.com/automation-practice-form',
        'prompt': ('Fill out this student registration form: first name Grace, last name Hopper, email '
                   'grace@navy.mil, gender Female, mobile 6145550123, hobby Reading, state NCR, city Delhi. '
                   'Leave the other fields as they are, submit the form, and tell me the title of the '
                   'confirmation dialog.'),
        'check': check_demoqa,
    },
    'calc': {
        'app': 'calc',
        'prompt': ('Windows Calculator is open. Use it (not mental math) to compute 1234 times 56 minus 789, '
                   'leave the result on its display, and tell me the result.'),
        'check': check_calc,
    },
}

# Same store task with different customer details, for replaying a saved routine.
TASKS['sauce-warm'] = dict(TASKS['sauce'], prompt=TASKS['sauce']['prompt']
                           .replace('first name Ada, last name Lovelace, ZIP code 43210',
                                    'first name Grace, last name Hopper, ZIP code 10001'))


# ---- Multi-tab, cross-app and install tasks -------------------------------

def all_pages():
    with urlopen(f'http://127.0.0.1:{PORT}/json/list', timeout=3) as response:
        return [t for t in json.load(response) if t.get('type') == 'page']


def todo_titles():
    _, stored = page_eval("[...document.querySelectorAll('.todo-list li label')].map(l => l.innerText)", 'todomvc')
    return stored or []


def check_tabs(answer):
    titles = todo_titles()
    urls = [p['url'] for p in all_pages()]
    eiffel = any('1889' in t and 'eiffel' in t.lower() for t in titles)
    everest = any(('8849' in norm(t) or '8848' in norm(t)) and 'everest' in t.lower() for t in titles)
    wiki_tabs = sum('wikipedia.org' in u for u in urls)
    return {'passed': eiffel and everest and wiki_tabs >= 2, 'todos': titles, 'wiki_tabs': wiki_tabs,
            'tabs': len(urls)}


def check_crossapp(answer):
    titles = todo_titles()
    calc = check_calc('')
    todo_ok = any('129.94' in t for t in titles)
    return {'passed': todo_ok and '129.94' in norm(calc.get('display')), 'todos': titles,
            'display': calc.get('display')}


EXTENSION_ID = 'bcjindcccaagfpapjjmafapmmgkkhgoa'  # JSON Formatter


def check_install(answer, profile=None):
    import time
    from pathlib import Path
    installed = pinned = False
    if profile:
        deadline = time.time() + 20  # Chrome writes Preferences on a short delay.
        while time.time() < deadline:
            installed = (Path(profile) / 'Default' / 'Extensions' / EXTENSION_ID).exists()
            try:
                prefs = json.loads((Path(profile) / 'Default' / 'Preferences').read_text(encoding='utf-8'))
                pinned = EXTENSION_ID in prefs.get('extensions', {}).get('pinned_extensions', [])
            except Exception:
                pass
            if installed and pinned:
                break
            time.sleep(1)
    return {'passed': installed and pinned, 'installed': installed, 'pinned': pinned}


TASKS['tabs'] = {
    'urls': ['https://todomvc.com/examples/react/dist/'],
    'prompt': ('Using two new browser tabs, look up on Wikipedia the year the Eiffel Tower was completed '
               '(one tab) and the elevation of Mount Everest in metres (another tab). Keep those tabs open. '
               'Then go back to the TodoMVC tab and add two todos: "Eiffel Tower <year>" and '
               '"Everest <metres> m", with the real numbers filled in.'),
    'check': check_tabs,
}
TASKS['crossapp'] = {
    'urls': ['https://www.saucedemo.com/', 'https://todomvc.com/examples/react/dist/'],
    'calc': True,
    'prompt': ('Two browser tabs are open: Swag Labs and TodoMVC. Log in to Swag Labs as standard_user '
               'with password secret_sauce and add up the prices of all six products using the Windows '
               'Calculator (it is open; leave the total on its display). Then, in the TodoMVC tab, add a todo '
               'reading "Swag total <total>" with the total you got.'),
    'check': check_crossapp,
}
TASKS['install'] = {
    'urls': ['https://chromewebstore.google.com/detail/json-formatter/' + EXTENSION_ID],
    'prompt': ('Install the JSON Formatter extension shown on this Chrome Web Store page into this browser, '
               'then pin it to the Chrome toolbar so its icon is always visible.'),
    'check': check_install,
    'needs_profile': True,
}
