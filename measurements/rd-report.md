# Real desktop R&D: November 7, 2026

QuickControl reduced an eight-field contact intake from **17.99 to 1.27 seconds**
in this local experiment. This combines faster native input with an observed
keyboard traversal; it is not a 14× improvement to every click or every app.

| Task | Baseline median | QuickControl median | Speedup | Exact outcomes |
| --- | ---: | ---: | ---: | --- |
| Eight-field contact intake | 17.989 s | 1.275 s | 14.11× | 3/3 each |
| Real Notepad Unicode JSON edit and save | 13.791 s | 2.833 s | 4.87× | 3/3 each |
| Find and update support ticket | 6.968 s | 4.134 s | 1.69× | 3/3 each |
| Replace a plain-text dispatch note | 2.350 s | 2.080 s | 1.13× | 3/3 each |

[Watch the original-speed comparison](../assets/quickcontrol-demo.mp4).
The two clips are the median selected form attempts. The shorter clip holds its
final PASS frame. Recording includes a little time before and after input;
the captions show instrumented warm task times, not video duration.

## Hypotheses and decisions

1. Per-character sleeps dominate Unicode, braces and short-field entry.
   Replace the typing backend with bounded Win32 SendInput chunks, preserve
   UTF-16 surrogate pairs, and leave the clipboard untouched. Real Notepad
   saved the exact expected Japanese, emoji, accents, braces and multiline JSON.
2. Repeated pointer focus and tool calls dominate stable forms. FillForm focuses
   once and traverses an already observed, empty form using its known Tab order.
   This workflow change and native input together produced the largest gain.
3. Arbitrarily shortening pointer waits might help. A 50 ms focus experiment
   wrote the notes into the reference field and failed exact verification.
   Rejected: the shipped profile retains the upstream 500 ms focus wait.
4. More batching might remove agent round trips. Sequence provides up to 16
   prevalidated Click/Type/Shortcut actions under the existing ownership gate.
   No separate speedup is claimed for Sequence; the recorded form uses FillForm.
5. Faster input needs reliable ownership propagation. An audit found upstream
   executor work lost ContextVars. The bridge now preserves the control token
   on the existing COM-affine worker and drains cooperative cancellation before
   releasing the gate. Offline tests cover cancellation during native input.

Microsoft documents that [SendInput inserts events serially](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput).
A successful enqueue is not proof an application consumed the text correctly;
every timed task therefore has an independent exact outcome check.

## Method and limits

Windows desktop, Chrome and Notepad, Windows MCP 0.8.8, Python 3.14. Baseline
uses `baseline_server.py`: the previous QuickControl control/monitor policy with
upstream typing. The optimized profile uses `rapid_server.py`, also available
through the default `server.py`. Both reuse one connection within a task.

Timing starts immediately before the first observed input and ends after
verification. Server startup, app launch, navigation, initial observation and
model reasoning are excluded. Search includes two intermediate screenshots.
The browser workloads are fictional local fixtures resembling common tasks;
Notepad is a real native editor. No DOM, DevTools or HTTP writes perform input.
The local endpoint only verifies submitted fixture values; Notepad is checked
by reading the saved file. The form baseline uses eight pointer edits plus a
save click; optimized uses one observed keyboard traversal including save.

These are three selected runs per profile/task on one machine, with profiles
run in blocks rather than randomized order. They do not establish broad app
compatibility or statistical confidence. Plain-text editing gains little because
upstream already pastes longer ordinary text efficiently. The edit cohort
predates the final context bridge; the final form/search cohorts include it.
One earlier native optimized run predates the final foreground harness.

The [sanitized measurements](rd-results.json) retain all saved task outcomes,
including exploratory successes, the rejected focus experiment, and a failed
search whose fixture was behind Notepad. That targeting failure prompted unique
fixture titles, exact window activation and a foreground assertion before
timing. Two setup-only failures are recorded separately. Raw desktop recordings
stay local because they may include unrelated windows. Published video is
cropped to the synthetic fixture and reviewed at first, middle and final frames.

The evaluation takes inspiration from [OSWorld's execution-based tasks](https://github.com/xlang-ai/OSWorld).
This is a small custom workload, not an OSWorld run or score.

## Reproduce

Run from the repository in an interactive Windows desktop. Install `uv` and
Chrome; `--record` also requires ffmpeg on PATH. Start the local fixture:

```powershell
python demos/fixture_server.py --port 8765
uv run --with mcp python demos/run_live.py --profile baseline --task form --strategy pointer --rounds 3 --record
uv run --with mcp python demos/run_live.py --profile rapid --task form --strategy keyboard --rounds 3 --record
uv run --with mcp python demos/native_editor.py --profile baseline
uv run --with mcp python demos/native_editor.py --profile rapid
```

Use `--inspect` on the first run to review its screenshot before authorizing the
known fixture coordinates through the local go file. Desktop scaling or app
layout changes require new observed coordinates. `run_live.py` positions its own
unique fixture window; `native_editor.py` only targets its own synthetic file.
For edit/search use `--task edit` or `--task search` and `--strategy pointer` for
both profiles. Alternate profile order for a stronger follow-up study.

Render two reviewed 1240×900 form recordings with `demos/render_demo.py`.
Its default crop removes the window edges, keeps original playback speed and
generates decoded audit frames plus a recording manifest.
