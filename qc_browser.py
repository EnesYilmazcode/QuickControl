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
      lib.last = el;
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
    lib.last = hits[nth];
    return hits[nth];
  },
  // The element the previous resolve() returned; labels can change after typing.
  lastEl() {
    if (!lib.last || !lib.last.isConnected) throw new Error('The element is gone (page changed)');
    return lib.last;
  },
  lastValue() { const el = lib.lastEl(); return el.isContentEditable ? el.innerText : el.value; },
  lastInfo() { return lib.info(lib.lastEl()); },
  // A label target that finds this same element again on a later run.
  describeLast() {
    const el = lib.lastEl(), d = lib.info(el), name = d.name.toLowerCase();
    const same = lib.scan(true).filter(e => { const i = lib.info(e); return i.role === d.role && i.name.toLowerCase() === name; });
    return {text: d.name, role: d.role, nth: Math.max(0, same.indexOf(el))};
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
  // Survey bookkeeping by element identity, not text.
  popupMark() { for (const i of lib.popupItems()) i.el.__qcSeen = true; return true; },
  popupNew() { return lib.popupItems().filter(i => !i.el.__qcSeen).map(i => ({s: i.s, r: [i.r.left, i.r.top, i.r.width, i.r.height]})); },
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
        self.events = []  # (sequence, method, params)
        self.seq = 0
        self.main_frame = None
        try:
            self.send('Page.enable')
            self.main_frame = self.send('Page.getFrameTree')['frameTree']['frame']['id']
        except CDPError:
            pass

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
                    self.seq += 1
                    self.events.append((self.seq, message['method'], message.get('params', {})))
                    del self.events[:-200]

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

    def mark(self):
        return {'seq': self.seq, 'state': state_token(self)}

    def main_events(self, since):
        out = []
        for seq, method, params in self.events:
            if seq <= since:
                continue
            frame = params.get('frameId') or params.get('frame', {}).get('id')
            if method == 'Page.loadEventFired' or frame in (None, self.main_frame):
                out.append(method)
        return out

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

    def _open(self, port, target):
        if self.page is None or self.page.target_id != target['id']:
            if self.page:
                self.page.close()
            self.page = Page(port, target)
            self.port = port
        return self.page

    def connect(self, prefer_title=None):
        """Stay on the tab we are driving while it exists; otherwise prefer the
        tab whose title the Chrome window shows, else the most recent tab."""
        port, pages = self.targets()
        if not pages:
            raise CDPError('No Chrome with a debugging port found. Start one with OpenBrowser.')
        if prefer_title:
            for page in pages:
                if page.get('title') and prefer_title.startswith(page['title']):
                    return self._open(port, page)
        if self.page is not None:
            for page in pages:
                if page['id'] == self.page.target_id:
                    return self.page
        return self._open(port, pages[0])

    def front_tab(self, window_title):
        """True when a Chrome window titled like one of our tabs is in front.
        Electron apps and a normal Chrome share the window class, so the class
        alone does not prove this is the debug browser."""
        port, pages = self.targets()
        for page in pages:
            title = page.get('title') or ''
            if title and window_title.startswith(title) and 'Chrome' in window_title:
                self._open(port, page)
                return True
        return False

    def tab_ids(self):
        return [p['id'] for p in self.targets()[1]]

    def tabs(self):
        return [(p['id'], p.get('title') or p.get('url', '')) for p in self.targets()[1]]

    def _http(self, path, method='GET'):
        from urllib.request import Request
        with urlopen(Request(f'http://127.0.0.1:{self.port}{path}', method=method), timeout=5) as response:
            body = response.read().decode('utf-8', 'replace')
        try:
            return json.loads(body)
        except ValueError:
            return body

    def activate(self, target_id):
        self._http(f'/json/activate/{target_id}')
        port, pages = self.targets()
        for page in pages:
            if page['id'] == target_id:
                return self._open(port, page)
        raise CDPError('That tab closed')

    def follow_new_tab(self, before):
        """A click that opened a tab: Chrome shows it, so drive it too."""
        time.sleep(0.05)
        port, pages = self.targets()
        fresh = [p for p in pages if p['id'] not in before]
        if not fresh:
            return None
        page = self._open(port, fresh[0])
        settle(page, page.mark(), quiet=0.5)
        return fresh[0].get('title') or fresh[0].get('url')

    def tab_step(self, step):
        what = step['tab']
        if what == 'new':
            url = str(step.get('url', 'about:blank'))
            if url != 'about:blank' and not url.startswith(('http://', 'https://')):
                raise ValueError('tab new needs an http(s) url')
            from urllib.parse import quote
            created = self._http('/json/new?' + quote(url, safe=':/?&=#%'), method='PUT')
            page = self.activate(created['id'])
            if url != 'about:blank':
                wait_for(page, {'url': url.split('#')[0][:40]}, 15)
                settle(page, page.mark(), quiet=0.5)
            return f'opened new tab {url}'
        if what == 'close':
            if self.page is None:
                raise CDPError('No tab to close')
            closing = self.page.target_id
            self.page.close()
            self.page = None
            self._http(f'/json/close/{closing}')
            time.sleep(0.2)
            page = self.connect()
            self._http(f'/json/activate/{page.target_id}')
            return 'closed the tab'
        want = str(what).lower()
        for target_id, title in self.tabs():
            if want in title.lower():
                self.activate(target_id)
                return f'switched to tab "{title}"'
        for page in self.targets()[1]:
            if want in page.get('url', '').lower():
                self.activate(page['id'])
                return f'switched to tab "{page.get("title")}"'
        raise LookupError(f'No tab matches {what!r}; open tabs: {[t for _, t in self.tabs()]}')

    def call(self, fn, prefer_title=None):
        page = self.connect(prefer_title)
        try:
            return fn(page)
        except (OSError, ConnectionError) as error:
            # A dropped socket is safe to reopen for reads, but the caller
            # decides about input; surface the failure instead of retrying.
            self.page = None
            raise CDPError(f'Browser connection dropped: {error}') from None


def format_observation(data, elapsed_ms=None, max_items=150, tabs=None):
    lines = [f"Browser: {data['title']} | {data['url']}"]
    sy, sh = data['scroll']
    extra = f"scroll {sy}/{sh}" if sh else 'no scroll'
    if elapsed_ms is not None:
        extra += f', observed in {elapsed_ms:.0f} ms'
    lines[0] += f'  ({extra})'
    if tabs and len(tabs) > 1:
        lines.append('Tabs: ' + ' | '.join(tabs) + '  (switch with {"tab": "words"})')
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


STARTS = ('Page.frameRequestedNavigation', 'Page.frameStartedLoading', 'Page.frameScheduledNavigation',
          'Page.frameNavigated')
DONE = ('Page.loadEventFired', 'Page.frameStoppedLoading')


def settle(page, before, quiet=0.15, timeout=15.0):
    """After a click or Enter: wait for a page load if one started, else a short quiet period.

    `before` is page.mark(). Chrome's navigation events show a load as soon as it
    is requested, before the URL changes, so a slow server is not mistaken for
    "nothing happened".
    """
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
        events = page.main_events(before['seq'])
        if any(e in STARTS for e in events) or now[0] != before['state'][0] or now[2] != before['state'][2]:
            navigated = True
        if navigated:
            loaded = any(e in DONE for e in events) and now[1] == 'complete'
            if loaded or (now[1] == 'complete' and now[2] != before['state'][2]):
                time.sleep(0.1)
                return True
        elif time.monotonic() >= end_quiet:
            return False
        time.sleep(0.03)
    if navigated:
        raise CDPError(f'Page load did not finish within {timeout:g} s')
    return False


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
