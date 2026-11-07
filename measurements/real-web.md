# Real-website runs, 2026-11-07

Both profiles get the identical tool calls on the live site. Only the server
differs: stock Windows MCP 0.8.8 (`baseline_server.py`) or QuickControl
(`rapid_server.py`). Each run launches a fresh, isolated Chrome profile at a
fixed window position, so coordinates come from one observed screenshot.

A run passes only when a read-only check of that isolated browser confirms the
outcome. URL, query and title come from Chrome's `/json/list`. Page text comes
from one fixed `document.body.innerText` read. Scenario files supply data only,
never code. Timing starts at the first action and ends once that check passes.

## Tasks

| Task | Actions | Passing means |
| --- | --- | --- |
| [GitHub advanced search](../demos/scenarios/github-rapid.json) | Query field, then stars, forks, size and pushed date by Tab, then Search | Results URL has `mcp language:Python stars:>100 forks:>10 size:<50000 pushed:>2025-01-01`, and `CursorTouch/Windows-MCP` is listed |
| [calculator.net mortgage](../demos/scenarios/mortgage-rapid.json) | One `Type` call: clear price, then down payment, term, rate, tax, insurance by Tab, then Enter | Submitted query holds all six values, and the page shows **$2,355.12** principal and interest (computed independently) |

## Results

| Task | Windows MCP runs | QuickControl runs | Medians | Speedup |
| --- | --- | --- | --- | ---: |
| GitHub search | 7.55, 7.45, 7.69 s | 2.50, 2.44, 2.53 s | 7.55 vs 2.50 s | 3.0x |
| Mortgage | 5.26, 5.27, 6.33 s | 1.74, 2.02, 1.90 s | 5.27 vs 1.90 s | 2.8x |

Every run is in [real-web-results.json](real-web-results.json), failures included.

## What went wrong on the way

- **Google Advanced Search drops keystrokes after the first field**, for both
  profiles. Focus moves to the second field and everything typed after that is
  lost, including Enter. Not a QuickControl bug, so Google is not used as a
  demo.
- That investigation added a **Tab focus check**: after each Tab, QuickControl
  waits until the focused control actually changes, then settles 30 ms. If
  focus never moves, it stops with an error instead of typing into the wrong
  field. It costs about 0.2 s on the GitHub task (2.27 s before, 2.50 s after).
  The 2.27 s run is excluded from the median.
- The first mortgage pair typed everything correctly, but my expected query
  said `450000`. The site reformats it to `450,000` on blur. The expectation
  was fixed and both sides re-run.
- The earlier checker evaluated `querySelector` with `throwOnSideEffect`, which
  Chrome rejects as a possible side effect. It now uses `/json/list` plus one
  fixed text read.
- A Mermaid Live Editor demo was dropped. New text was appended to the sample
  diagram instead of replacing it, and long text already pastes quickly on
  stock Windows MCP, so the demo would not have shown much.

## Reproduce

```powershell
uv run --with mcp --with websocket-client python demos/real_web.py --profile rapid `
  --url https://github.com/search/advanced --title GitHub `
  --plan demos/scenarios/github-rapid.json --record --debug-port 9226 --load-wait 6
```

Swap `--profile baseline` and the matching `-baseline.json` plan for the other
side. Coordinates assume a 1920x1080 primary display. Render a comparison with
`demos/render_demo.py <baseline.mp4> <rapid.mp4> <out.mp4> --gif --caption "..."`.
