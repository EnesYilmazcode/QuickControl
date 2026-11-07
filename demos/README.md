# Real GUI speed lab

Three recognizable desktop tasks use fictional data and exact outcome checks:

| Task | Everyday example | What must match |
| --- | --- | --- |
| `form` | Contact intake or an application | Eight fields, including accents, smart quotes and punctuation |
| `edit` | Correcting an existing dispatch note | Entire replacement, with no leftover draft text |
| `search` | Finding and updating a support ticket | Search query, selected ticket and saved internal note |

Start the fixture with `python demos/fixture_server.py --port 8765`, then open
`http://127.0.0.1:8765/?task=form&run=baseline-form-1` in Chrome through normal
desktop navigation. Change `task` and use a unique `run` for every attempt.
Observe the first field in a screenshot before clicking it. `cases.actions(task)`
provides the remaining GUI text/key actions; the fixture has natural Tab order.
The search task uses Enter to search, Enter to open the result, then text, Tab,
Enter to save. No real accounts or submissions are involved.

The page shows PASS only after the local server compares every submitted value
against its expected value. A read-only `GET /api/result?run=...` returns the
independently checked values. The driver must not fill fields using the DOM,
JavaScript, DevTools or HTTP. Reading the outcome endpoint is verification only.
Duplicate run submissions are rejected to prevent stale success.

`benchmark.measure_task()` accepts a GUI action dispatcher for either profile.
It measures from the initial screenshot-observed click through exact verified
submission, records tool-only and verified times, and preserves failures.
Launch, navigation, observation and model reasoning are outside that warm-input
measurement. Report them separately if measuring end-to-end agent latency.
Use the same task, starting state, observed anchor and normal app settings for
both profiles; alternate baseline/optimized order and repeat at least three
times. Keep every attempt. These synthetic results are not a general GUI
benchmark score or evidence of equivalent performance in every application.

Generate a semantic action plan with `python demos/benchmark.py --plan form`.
Summarize saved measurement rows with
`python demos/benchmark.py --summarize measurements.json`.
Run fixture checks with `python -m unittest demos.test_workloads`.

For an honest demo, record the Chrome fixture at normal speed with `ffmpeg`
`gdigrab`, recording each full attempt from before the click to the visible PASS.
Crop to the fixture window so unrelated desktop content is excluded. Put the
baseline and optimized recordings beside each other, starting at the initial
click, and freeze the shorter recording on its PASS while the longer finishes.
Label both profiles and state that startup, navigation and reasoning are
excluded. Keep the raw recordings and timing JSON alongside the comparison.
