"""Text observation and element actions for Chrome over the DevTools protocol.

Input goes through Input.dispatchMouseEvent / Input.insertText, which Chrome
treats as trusted user input, so React and other frameworks see real events.
Only a local debugger port is used. Nothing here reads cookies or storage.
"""
from __future__ import annotations

import base64
import json
import os
import threading
import time
from urllib.parse import urlsplit
from urllib.request import urlopen

DEFAULT_PORTS = (9333, 9222, 9223, 9224, 9225, 9226, 9229)

LIB = r"""
(() => {
 if (window.__qcLib && window.__qcLib.v === '__QCV__') return true;
 const SEL = 'a[href],button,input:not([type=hidden]),select,textarea,summary,[role=button],[role=link],[role=checkbox],[role=radio],[role=combobox],[role=option],[role=tab],[role=menuitem],[role=menuitemcheckbox],[role=switch],[role=textbox],[role=searchbox],[role=listbox],[role=slider],[role=spinbutton],[contenteditable=""],[contenteditable=true],[onclick],[tabindex]:not([tabindex="-1"])';
 const clean = s => (s || '').replace(/\s+/g, ' ').trim();
 const visible = el => {
   const r = el.getBoundingClientRect();
   if (r.width < 1 || r.height < 1) {
     // Styled checkboxes and radios are often hidden behind a visible label.
     if (el.tagName === 'INPUT' && el.labels && el.labels.length) return visible(el.labels[0]);
     return false;
   }
   const cs = getComputedStyle(el);
   return cs.visibility !== 'hidden' && cs.display !== 'none';
 };
 const idLike = s => !s || (/^[\w-]+$/.test(s) && /[-_\d]|[a-z][A-Z]/.test(s));
 const labelOf = el => {
   const base = labelBase(el);
   if (!idLike(base) || !['INPUT', 'TEXTAREA'].includes(el.tagName)) return base;
   // Custom widgets (react-select, date pickers) show their label in a nearby element.
   let p = el.parentElement;
   for (let k = 0; p && k < 5; k++, p = p.parentElement) {
     const t = clean(p.innerText);
     if (t && t.length < 50) return t;
   }
   return base;
 };
 const labelBase = el => {
   let s = clean(el.getAttribute('aria-label')); if (s) return s;
   const lb = el.getAttribute('aria-labelledby');
   if (lb) { s = clean(lb.split(/\s+/).map(i => document.getElementById(i)).filter(Boolean).map(e => e.innerText).join(' ')); if (s) return s; }
   if (el.labels && el.labels.length) { s = clean([...el.labels].map(l => l.innerText).join(' ')); if (s) return s; }
   const t = el.tagName;
   if (t === 'INPUT' || t === 'TEXTAREA') {
     if (['submit', 'button', 'reset'].includes(el.type)) return clean(el.value);
     if (el.placeholder) return clean(el.placeholder);
   }
   if (t !== 'SELECT' && t !== 'INPUT' && t !== 'TEXTAREA') { s = clean(el.innerText || el.textContent); if (s) return s.slice(0, 80); }
   const img = el.querySelector && el.querySelector('img[alt]');
   return clean(el.title || (img && img.alt) || el.getAttribute('name') || el.id || '');
 };
 const roleOf = el => {
   const r = el.getAttribute('role'); if (r) return r;
   const t = el.tagName.toLowerCase();
   if (t === 'a') return 'link';
   if (t === 'select') return 'select';
   if (t === 'textarea') return 'textbox';
   if (t === 'input') {
     const ty = el.type || 'text';
     if (ty === 'checkbox' || ty === 'radio') return ty;
     if (['submit', 'button', 'reset', 'image'].includes(ty)) return 'button';
     return ty === 'text' ? 'input' : 'input:' + ty;
   }
   if (el.isContentEditable) return 'textbox';
   return t === 'button' || t === 'summary' ? 'button' : t;
 };
 const inView = r => r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth;
 const lib = {
  els: [],
  scan(all) {
    const out = [], seen = new Set();
    for (const el of document.querySelectorAll(SEL)) {
      if (seen.has(el) || !visible(el)) continue;
      seen.add(el);
      if (!all && !inView(el.getBoundingClientRect())) continue;
      out.push(el);
    }
    return out;
  },
  info(el) {
    const role = roleOf(el), d = {role, name: labelOf(el).slice(0, 100)};
    if (el.tagName === 'SELECT') {
      d.value = el.selectedOptions[0] ? clean(el.selectedOptions[0].text) : '';
      d.options = [...el.options].slice(0, 40).map(o => clean(o.text));
      if (el.options.length > 40) d.more = el.options.length - 40;
    } else if (role === 'checkbox' || role === 'radio' || role === 'switch') {
      d.checked = el.checked !== undefined ? el.checked : el.getAttribute('aria-checked') === 'true';
    } else if ('value' in el && el.tagName !== 'BUTTON' && el.tagName !== 'LI') {
      d.value = String(el.value).slice(0, 200);
    } else if (el.isContentEditable) {
      d.value = clean(el.innerText).slice(0, 200);
    }
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') d.disabled = true;
    if (role === 'combobox' || el.getAttribute('aria-haspopup')) {
      d.popup = el.getAttribute('aria-expanded') === 'true' ? 'open' : 'closed';
    }
    if (el.href && role === 'link') { try { const u = new URL(el.href); d.href = u.pathname + u.search; } catch (e) {} }
    return d;
  },
  observe(all, maxText) {
    let els = lib.scan(all), items = els.map(lib.info);
    // Drop an element that repeats the previous one (image link + title link).
    const keep = items.map((d, i) => !(i > 0 && d.role === items[i - 1].role && d.name === items[i - 1].name && d.href === items[i - 1].href && els[i - 1].closest('li,article,tr,[class*=item i],[class*=card i]') === els[i].closest('li,article,tr,[class*=item i],[class*=card i]')));
    els = els.filter((e, i) => keep[i]); items = items.filter((d, i) => keep[i]);
    // Repeated generic labels ("Add to cart") get the name of their card.
    const counts = {}; items.forEach(d => counts[d.role + d.name] = (counts[d.role + d.name] || 0) + 1);
    items.forEach((d, i) => {
      if (counts[d.role + d.name] < 2) return;
      let p = els[i].parentElement;
      for (let depth = 0; p && depth < 8; depth++, p = p.parentElement) {
        const line = (p.innerText || '').split(/\n/).map(clean).find(l => l && l !== d.name && l.length > 2 && l.length < 70);
        if (line && (p.innerText || '').length < 600) { d.ctx = line; break; }
      }
    });
    lib.els = els;
    let text = '';
    if (maxText > 0) {
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      const parts = []; let size = 0, node;
      while ((node = walker.nextNode()) && size < maxText) {
        const p = node.parentElement; const s = clean(node.textContent);
        if (!s || !p || ['SCRIPT', 'STYLE', 'NOSCRIPT', 'OPTION'].includes(p.tagName)) continue;
        const r = p.getBoundingClientRect();
        if (r.width < 1 || !inView(r)) continue;
        if (p.closest('a,button,label,select,[role=button],[role=option]')) continue;
        parts.push(s); size += s.length + 1;
      }
      text = parts.join(' ').slice(0, maxText);
    }
    const doc = document.scrollingElement || document.documentElement;
    return {items, text, title: document.title, url: location.href,
            scroll: [Math.round(scrollY), Math.max(0, doc.scrollHeight - innerHeight)],
            viewport: [innerWidth, innerHeight]};
  },
  find(query, limit) {
    const body = document.body.innerText, low = body.toLowerCase(), q = query.toLowerCase(), out = [];
    let i = low.indexOf(q);
    while (i >= 0 && out.length < limit) {
      out.push(clean(body.slice(Math.max(0, i - 160), i + q.length + 160)));
      i = low.indexOf(q, i + q.length + 160);
    }
    return out;
  },
  resolve(target) {
    if (typeof target === 'number') {
      const el = lib.els[target - 1];
      if (!el || !el.isConnected) throw new Error('Element [' + target + '] is gone (page changed). Use a {"text": ...} target or Observe again.');
      return el;
    }
    const want = clean(target.text || target.name || '').toLowerCase();
    const role = target.role; const nth = target.nth || 0;
    let best = 0, hits = [];
    for (const el of lib.scan(true)) {
      const d = lib.info(el);
      if (role && d.role !== role && !(role === 'input' && d.role.startsWith('input'))) continue;
      const n = d.name.toLowerCase(); let s = 0;
      if (!want) s = 1; else if (n === want) s = 4; else if (n.startsWith(want)) s = 3;
      else if (n.includes(want)) s = 2;
      else if ([el.getAttribute('name'), el.id, el.placeholder, el.getAttribute('aria-label'), el.title].some(v => (v || '').toLowerCase() === want)) s = 4;
      else if ([el.placeholder, el.getAttribute('aria-label'), el.title].some(v => (v || '').toLowerCase().includes(want))) s = 2;
      if (!s) continue;
      if (target.near) {
        const near = target.near.toLowerCase(); let p = el.parentElement, found = false;
        for (let k = 0; p && k < 8 && !found; k++, p = p.parentElement)
          if ((p.innerText || '').length < 800 && (p.innerText || '').toLowerCase().includes(near)) found = true;
        if (!found) continue;
      }
      if (s > best) { best = s; hits = [el]; } else if (s === best) hits.push(el);
    }
    if (!hits.length) throw new Error('No element matches ' + JSON.stringify(target));
    if (nth >= hits.length) throw new Error('Only ' + hits.length + ' elements match ' + JSON.stringify(target));
    return hits[nth];
  },
  point(target) {
    const el = lib.resolve(target);
    el.scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'});
    let box = el.getBoundingClientRect();
    if ((box.width < 1 || box.height < 1) && el.labels && el.labels.length) box = el.labels[0].getBoundingClientRect();
    const x = box.left + Math.min(box.width / 2, 40 + box.width / 4), y = box.top + box.height / 2;
    const hit = document.elementFromPoint(x, y);
    const ok = hit && (hit === el || el.contains(hit) || hit.contains(el) || (el.labels && [...el.labels].some(l => l.contains(hit))));
    const d = lib.info(el);
    return {x, y, ok, blocker: ok ? '' : (hit ? (hit.tagName.toLowerCase() + (hit.id ? '#' + hit.id : '') + ' "' + clean(hit.innerText).slice(0, 40) + '"') : 'nothing'), role: d.role, name: d.name};
  },
  focusFor(target) {
    const el = lib.resolve(target);
    el.scrollIntoView({block: 'center', behavior: 'instant'});
    el.focus();
    if (el.select && el.tagName !== 'SELECT') { try { el.select(); } catch (e) {} }
    else if (el.isContentEditable) { const r = document.createRange(); r.selectNodeContents(el); const s = getSelection(); s.removeAllRanges(); s.addRange(r); }
    return document.activeElement === el || el.contains(document.activeElement);
  },
  valueOf(target) {
    const el = lib.resolve(target);
    return el.isContentEditable ? el.innerText : el.value;
  },
  choose(target, option) {
    const el = lib.resolve(target);
    if (el.tagName !== 'SELECT') throw new Error('Element is not a native select; click it, then click the option');
    const want = clean(String(option)).toLowerCase();
    let idx = [...el.options].findIndex(o => clean(o.text).toLowerCase() === want || o.value.toLowerCase() === want);
    if (idx < 0) idx = [...el.options].findIndex(o => clean(o.text).toLowerCase().includes(want));
    if (idx < 0) throw new Error('No option ' + JSON.stringify(option) + ' in ' + JSON.stringify([...el.options].map(o => clean(o.text)).slice(0, 40)));
    const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set;
    el.focus();
    setter.call(el, el.options[idx].value);
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
    return clean(el.options[idx].text);
  },
  popupItems() {
    const sel = '[role=option],[role=menuitem],[role=menuitemradio],[role=menuitemcheckbox],[role=listbox] li,[role=menu] li,[class*=option i],[class*=menu-item i],[class*=dropdown-item i],li';
    const out = [];
    for (const el of document.querySelectorAll(sel)) {
      if (!visible(el)) continue;
      const r = el.getBoundingClientRect(); if (!inView(r)) continue;
      const s = clean(el.innerText); if (!s || s.length > 120) continue;
      out.push({el, s, r});
    }
    return out;
  },
 };
 lib.v = '__QCV__';
 window.__qcLib = lib;
 return true;
})()
"""

import hashlib as _hashlib
LIB = LIB.replace('__QCV__', _hashlib.sha1(LIB.encode()).hexdigest()[:10])

KEYS = {
    'enter': (13, 'Enter', '\r'), 'tab': (9, 'Tab', ''), 'escape': (27, 'Escape', ''),
    'esc': (27, 'Escape', ''), 'backspace': (8, 'Backspace', ''), 'delete': (46, 'Delete', ''),
    'arrowdown': (40, 'ArrowDown', ''), 'down': (40, 'ArrowDown', ''), 'arrowup': (38, 'ArrowUp', ''),
    'up': (38, 'ArrowUp', ''), 'arrowleft': (37, 'ArrowLeft', ''), 'left': (37, 'ArrowLeft', ''),
    'arrowright': (39, 'ArrowRight', ''), 'right': (39, 'ArrowRight', ''), 'home': (36, 'Home', ''),
    'end': (35, 'End', ''), 'pagedown': (34, 'PageDown', ''), 'pageup': (33, 'PageUp', ''),
    'space': (32, ' ', ' '),
}
MODIFIERS = {'alt': 1, 'ctrl': 2, 'control': 2, 'meta': 4, 'win': 4, 'shift': 8}


class CDPError(RuntimeError):
    pass


class Page:
    """One persistent websocket to one page target."""

    def __init__(self, port, target):
        try:
            import websocket
        except ImportError:
            raise CDPError('The browser fast path needs websocket-client: uv run --with websocket-client ...') from None
        self.port, self.target_id = port, target['id']
        endpoint = target['webSocketDebuggerUrl']
        if urlsplit(endpoint).hostname not in ('localhost', '127.0.0.1', '::1'):
            raise CDPError('Refusing a non-local debugger endpoint')
        self.ws = websocket.create_connection(endpoint, timeout=15, origin=f'http://localhost:{port}',
                                              suppress_origin=False)
        self.next_id = 0
        self.lock = threading.Lock()
        self.events = []

    def send(self, method, params=None, timeout=15):
        with self.lock:
            self.next_id += 1
            ident = self.next_id
            self.ws.settimeout(timeout)
            self.ws.send(json.dumps({'id': ident, 'method': method, 'params': params or {}}))
            while True:
                message = json.loads(self.ws.recv())
                if message.get('id') == ident:
                    if 'error' in message:
                        raise CDPError(f"{method}: {message['error'].get('message')}")
                    return message.get('result', {})
                if 'method' in message:
                    self.events.append(message['method'])
                    del self.events[:-50]

    def eval(self, expression, timeout=15):
        result = self.send('Runtime.evaluate', {'expression': expression, 'returnByValue': True,
                                                'awaitPromise': True, 'userGesture': True}, timeout)
        if result.get('exceptionDetails'):
            detail = result['exceptionDetails']
            message = detail.get('exception', {}).get('description') or detail.get('text')
            raise CDPError(str(message).split('\n')[0].replace('Error: ', ''))
        return result.get('result', {}).get('value')

    def lib(self, call, timeout=15):
        return self.eval(LIB + ';window.__qcLib.' + call, timeout)

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


class Browser:
    def __init__(self):
        self.page = None
        self.port = None

    def ports(self):
        configured = os.environ.get('QC_CDP_PORT')
        return (int(configured),) if configured else DEFAULT_PORTS

    def targets(self):
        for port in ([self.port] if self.port else []) + [p for p in self.ports() if p != self.port]:
            try:
                with urlopen(f'http://127.0.0.1:{port}/json/list', timeout=0.4) as response:
                    pages = [t for t in json.load(response) if t.get('type') == 'page'
                             and not t.get('url', '').startswith(('devtools://', 'chrome-extension://'))]
                if pages:
                    return port, pages
            except Exception:
                continue
        return None, []

    def available(self):
        return bool(self.targets()[1])

    def connect(self, prefer_title=None):
        port, pages = self.targets()
        if not pages:
            raise CDPError('No Chrome with a debugging port found. Start one with OpenBrowser.')
        chosen = pages[0]
        if prefer_title:
            for page in pages:
                if page.get('title') and page['title'] in prefer_title:
                    chosen = page
                    break
        if self.page is None or self.page.target_id != chosen['id']:
            if self.page:
                self.page.close()
            self.page = Page(port, chosen)
            self.port = port
        return self.page

    def call(self, fn, prefer_title=None):
        page = self.connect(prefer_title)
        try:
            return fn(page)
        except (OSError, ConnectionError) as error:
            # A dropped socket is safe to reopen for reads, but the caller
            # decides about input; surface the failure instead of retrying.
            self.page = None
            raise CDPError(f'Browser connection dropped: {error}') from None


def format_observation(data, elapsed_ms=None, max_items=150):
    lines = [f"Browser: {data['title']} | {data['url']}"]
    sy, sh = data['scroll']
    extra = f"scroll {sy}/{sh}" if sh else 'no scroll'
    if elapsed_ms is not None:
        extra += f', observed in {elapsed_ms:.0f} ms'
    lines[0] += f'  ({extra})'
    for index, item in enumerate(data['items'][:max_items], 1):
        line = f"[{index}] {item['role']} \"{item['name']}\""
        if item.get('ctx'):
            line += f" (for \"{item['ctx']}\")"
        if 'value' in item and item['value'] != item['name'] and item['value'] != '':
            line += f" value=\"{item['value']}\""
        elif 'value' in item and item['role'] != 'select' and item['value'] == '':
            line += ' (empty)'
        if 'options' in item:
            line += ' options: ' + ' | '.join(item['options']) + (f" (+{item['more']} more)" if item.get('more') else '')
        if 'checked' in item:
            line += ' checked' if item['checked'] else ' unchecked'
        if item.get('popup'):
            line += f" menu {item['popup']}"
        if item.get('disabled'):
            line += ' disabled'
        if item.get('href') and item['role'] == 'link':
            line += f" -> {item['href'][:80]}"
        lines.append(line)
    if len(data['items']) > max_items:
        lines.append(f'... {len(data["items"]) - max_items} more elements (Observe with all=true or scroll)')
    if data.get('text'):
        lines.append('Text: ' + data['text'])
    return '\n'.join(lines)


def observe(page, all_elements=False, max_text=1500):
    started = time.perf_counter()
    data = page.lib(f'observe({json.dumps(bool(all_elements))}, {int(max_text)})')
    return data, (time.perf_counter() - started) * 1000


def mouse_click(page, x, y, clicks=1):
    page.send('Input.dispatchMouseEvent', {'type': 'mouseMoved', 'x': x, 'y': y})
    for count in range(1, clicks + 1):
        page.send('Input.dispatchMouseEvent', {'type': 'mousePressed', 'x': x, 'y': y,
                                               'button': 'left', 'clickCount': count})
        page.send('Input.dispatchMouseEvent', {'type': 'mouseReleased', 'x': x, 'y': y,
                                               'button': 'left', 'clickCount': count})


def press(page, combo):
    parts = [p.strip() for p in str(combo).replace('-', '+').split('+') if p.strip()]
    if not parts:
        raise ValueError('press needs a key')
    *mods, key = parts
    modifiers = 0
    for mod in mods:
        if mod.lower() not in MODIFIERS:
            raise ValueError(f'Unknown modifier {mod!r}')
        modifiers |= MODIFIERS[mod.lower()]
    lowered = key.lower()
    if lowered in KEYS:
        vk, name, text = KEYS[lowered]
        code = name if len(name) > 1 else 'Space'
    elif len(key) == 1:
        name, text = key, key
        vk = ord(key.upper())
        code = ('Key' + key.upper()) if key.isalpha() else ('Digit' + key if key.isdigit() else '')
    else:
        raise ValueError(f'Unknown key {key!r}')
    params = {'modifiers': modifiers, 'windowsVirtualKeyCode': vk, 'key': name, 'code': code}
    commands = []
    if modifiers == 2 and lowered == 'a':
        commands = ['selectAll']
    if text and modifiers in (0, 8):
        page.send('Input.dispatchKeyEvent', {**params, 'type': 'keyDown', 'text': text})
    else:
        page.send('Input.dispatchKeyEvent', {**params, 'type': 'rawKeyDown', 'commands': commands})
    page.send('Input.dispatchKeyEvent', {**params, 'type': 'keyUp'})


def state_token(page):
    return page.eval('[location.href, document.readyState, performance.timeOrigin]')


def settle(page, before, quiet=0.12, timeout=15.0):
    """Wait for a click/Enter's consequences: navigation if one starts, else a short quiet period."""
    deadline = time.monotonic() + timeout
    end_quiet = time.monotonic() + quiet
    navigated = False
    while time.monotonic() < deadline:
        try:
            now = state_token(page)
        except Exception:
            time.sleep(0.05)  # Mid-navigation the context can vanish briefly.
            navigated = True
            continue
        if now[0] != before[0] or now[2] != before[2]:
            navigated = True
        if navigated and now[1] == 'complete':
            time.sleep(0.15)
            return True
        if not navigated and time.monotonic() >= end_quiet:
            return False
        time.sleep(0.03)
    return navigated


def wait_for(page, condition, timeout):
    deadline = time.monotonic() + timeout
    text, url, gone = condition.get('text'), condition.get('url'), condition.get('gone')
    while True:
        try:
            href, body = page.eval('[location.href, document.body ? document.body.innerText : ""]')
            ok = ((not text or text.lower() in body.lower()) and (not url or url in href)
                  and (not gone or gone.lower() not in body.lower()))
            if ok:
                return True
        except Exception:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def capture(page, rect, scale=1.0):
    x, y, w, h = rect
    data = page.send('Page.captureScreenshot', {'format': 'jpeg', 'quality': 70, 'clip': {
        'x': max(0, x - 6), 'y': max(0, y - 6), 'width': w + 12, 'height': h + 12, 'scale': scale}})
    return base64.b64decode(data['data'])
