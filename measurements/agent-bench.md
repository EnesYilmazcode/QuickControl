# Real agent runs, 2026-11-08

A real Claude Sonnet agent did six realistic tasks on three tool servers. Same
prompt, same model, fresh start state every run, and a run passes only when an
independent check of the page or the Calculator display agrees. Setup and the
checks are in [bench/README.md](../bench/README.md). Every run is in
[agent-bench-runs.jsonl](agent-bench-runs.jsonl), failures included.

## Result

With the new Observe and Act tools the agent passed **12 of 12** runs at a
median of **7.1 s**. Stock Windows MCP passed 5 of 12 at a median of 65.5 s.
QuickControl before this work (v1) passed 11 of 12 at 43.6 s.

| Task | Windows MCP | QuickControl v1 | QuickControl agent tools |
| --- | ---: | ---: | ---: |
| Shop checkout (saucedemo) | 1/2, 255.8 s, 42 turns | 1/2, 62.2 s, 45 turns | 2/2, 21.7 s, 10 turns |
| Todo app (TodoMVC) | 0/2 | 2/2, 25.3 s, 14 turns | 2/2, 7.6 s, 4 turns |
| GitHub search | 1/2, 11.7 s, 8 turns | 2/2, 12.4 s, 7 turns | 2/2, 5.9 s, 2 turns |
| Wikipedia lookup | 1/2, 24.5 s, 16 turns | 2/2, 32.5 s, 12 turns | 2/2, 5.4 s, 2 turns |
| Registration form with custom dropdowns (demoqa) | 1/2, 75.3 s, 50 turns | 2/2, 73.5 s, 25 turns | 2/2, 7.6 s, 4 turns |
| Calculator | 1/2, 65.5 s, 21 turns | 2/2, 97.9 s, 16 turns | 2/2, 6.0 s, 4 turns |

Times are medians of passing runs, measured by Claude Code itself (model plus tools).

| Arm | Passed | Median time | Median turns | Time in the model | Total time | Total cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Windows MCP | 5/12 | 65.5 s | 21 | 27% | 670 s | $0.86 |
| QuickControl v1 | 11/12 | 43.6 s | 15 | 32% | 578 s | $1.29 |
| QuickControl agent tools | 12/12 | 7.1 s | 4 | 64% | 108 s | $0.44 |

The medians are 9.2x faster than stock and 6.1x faster than v1. Total time across
all twelve runs, including failures, is 6.2x lower than stock. Cost is about half,
because a text observation is smaller than a screenshot (median 57k input tokens
against 107k).

**Where the time went.** Stock spent 73% of its time inside tools, mostly full
Snapshots of 20 to 24 s each. With Observe and Act the median run spent 2.0 s in
tools in total, and the model is now 64% of the time. That is why replaying a
saved routine did not help much (below): the remaining cost is the model reading
and answering, not the desktop.

## Harder tasks: tabs, two apps, installing something

The six tasks above are mostly one form on one page. These three move around:

- **Tabs:** look up two facts in two new Wikipedia tabs, keep them open, and go back
  to TodoMVC to add both as todos.
- **Browser to Calculator:** log in to the shop, add up all six prices in Windows
  Calculator (the total must stay on its display), then add the total as a todo in
  a second tab.
- **Install:** install JSON Formatter from the Chrome Web Store and pin it to the
  toolbar. Checked by reading the profile on disk: the extension folder exists and
  Chrome's preferences list it as pinned.

| Task | Windows MCP | QuickControl v1 | Agent tools, first try | Agent tools after the fixes below |
| --- | ---: | ---: | ---: | ---: |
| Tabs | 48.0 s, 30 calls | 27.8 s, 17 calls | **9.9 s, 2 calls** | |
| Browser to Calculator | 45.2 s, 31 calls | 79.8 s, 22 calls | 68.2 s, 18 calls | **12.0 s and 14.3 s, 4 to 5 calls** |
| Install and pin an extension | 61.4 s, 30 calls | 36.6 s, 10 calls | 36.9 s, 11 calls | **27.3 s and 17.0 s, 5 to 8 calls** |

Every run passed. Stock and v1 got one run each here, so treat their numbers as rough.

The first try lost on two of the three, and the traces showed why. Observe and Act
only saw inside the web page:

- Chrome's own install popup and its Extensions menu were invisible, so the agent
  fell back to screenshots.
- Other apps were invisible too: the agent spent six calls finding Calculator.
- A plan could not cross apps, so going from the browser to Calculator and back took
  a separate call for each move.

Three changes fixed that:

- **Chrome's own UI:** a click label not found in the page is looked up in Chrome's
  own buttons and popups, through UIA, in tens of milliseconds. The browser view
  lists them. The whole install is now four clicks in text: Add to Chrome, Add
  extension, Extensions, Pin JSON Formatter.
- **Other windows:** Observe lists the other open windows.
- **Plans that cross apps:** a plan switches between browser and desktop steps on its
  own. `{"window": "Calculator"}, {"type": "29.99+9.99+...="}` and then
  `{"tab": "TodoMVC"}` all run in one Act.

## A clean rerun of the shop task

Chrome's breach warning for the demo store's public password appeared in the
first runs and blocked page input on every arm. With it switched off in the
profile and no interruptions on any arm:

| | Windows MCP | QuickControl v1 | QuickControl agent tools |
| --- | ---: | ---: | ---: |
| Shop checkout, 2 runs each | 182.7 s, 19 turns | 50.5 s, 24 turns | **9.1 s, 5 turns** |

That is 20x on a login, sort, add-to-cart and checkout flow across four pages.

## Why stock failed so often

Stock failures were not the model getting the task wrong. In 11 of 12 stock runs
a tool call came back `CONTROL_UNAVAILABLE` or "user is in control", and the agent
had to wait or give up. [watchdog_probe.py](../bench/watchdog_probe.py) reproduces
it with no physical input at all:

| Sequence, 3 times | Windows MCP 0.8.8 | QuickControl profile |
| --- | --- | --- |
| Snapshot only | stays `ai` | stays `ai` |
| Move only | stays `ai` | stays `ai` |
| Snapshot with `use_vision`, then one Move | `unavailable` about 1 s later, then `user` with a 10 s wait | stays `ai` every time |

Agents call Snapshot with vision constantly, so this hits almost every stock run.
The QuickControl takeover profile does not trip it. It is worth reporting upstream.
v1 shares that profile and was interrupted in 3 of 12 runs, once by a start-of-run
"user" state whose source is unknown.

## Saved routines

One agent run recorded the shop flow as a routine with `{{first}}`, `{{last}}`
and `{{zip}}` placeholders. Two later runs got the same task with different
customer details and replayed it: **9.9 s and 8.8 s**, both passing. That is no
faster than a fresh QuickControl run (8.9 and 9.2 s). Planning the flow costs
the model about as much as finding and calling the routine. Routines would matter
for longer flows or a smaller model, not here.

## What changed for the agent

A typical QuickControl run of the registration form is three calls:

1. `Observe` lists the form: inputs, radio buttons, checkboxes, and the custom
   State and City dropdowns, in 3 ms.
2. `Act` fills nine fields, picks both dropdown values, submits and waits for the
   dialog, in about one second, then returns the new view.
3. The agent reads the dialog title from that view and answers.

The stock agent took 25 to 50 turns for the same form, each with a screenshot and
coordinates.

## Engine timings

| Operation | Before | Now |
| --- | ---: | ---: |
| Read a Chrome page | Snapshot 20 to 24 s | Observe 2 to 5 ms |
| Read Calculator | Snapshot 20 to 24 s | Observe 76 to 390 ms |
| Fill one web field | Type about 0.5 s plus a click | 30 to 45 ms |
| Whole registration form, including both custom dropdowns | many turns | 0.97 s in one Act |
| Thirteen Calculator buttons and a result check | many turns | 0.5 s in one Act |

## Limits

- Two runs per task and arm. The gaps are large, but individual medians are noisy.
- One model (Sonnet) and one machine with two 1080p displays.
- The QuickControl agent sometimes skipped the UI. On Wikipedia and GitHub it
  navigated straight to the target URL with `goto`. Stock could type into the
  address bar too, and on GitHub it did. On the four other tasks it went through
  the page like a user would.
- The browser fast path needs Chrome started with a local debugging port.
  `OpenBrowser` does that with a dedicated profile. Your everyday Chrome
  window is not attached unless you start it that way.
- Interrupted runs are counted, not dropped. Excluding them would make stock look
  better on time and worse on reliability.
