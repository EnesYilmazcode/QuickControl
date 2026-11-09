# Agent benchmark

A real Claude agent (`claude -p`, Sonnet) gets the same task prompt on every arm.
Only the MCP server behind it changes:

| Arm | Server | Tools the agent can use |
| --- | --- | --- |
| `stock` | Windows MCP 0.8.8 as published | Snapshot, Screenshot, Click, Type, ... |
| `qc1` | `rapid_server.py`, QuickControl before this work | Stock tools with faster typing, FillForm, Sequence, the takeover policy |
| `qc` | `agent_server.py`, the current default | All of the above plus Observe, Act, SurveyMenus, OpenBrowser |

Built-in Claude Code tools are disabled (`--tools ""`), so the agent can only act
through the server. Each run starts from a fresh state: a new Chrome profile with
a local debug port at a fixed window position, or a freshly started Calculator.
User settings, hooks and memory are not loaded.

A run passes only when an independent check agrees, never on the agent's word:

| Task | Start | Check |
| --- | --- | --- |
| `sauce` | saucedemo.com login | Page is the checkout overview with exactly the two cheapest items, total $19.42, and the answer says $19.42 |
| `todo` | TodoMVC (React) | URL hash is `#/active` and only "Buy milk" and "Book dentist" are listed |
| `github` | GitHub advanced search | Result URL has the query, `language:Python`, `stars:>1000`, sort by stars, and the answer names the real top result |
| `wiki` | Wikipedia main page | Answer has 1870, scarlet and gray |
| `demoqa` | demoqa.com practice form | Confirmation dialog shows every submitted value, including the custom State and City dropdowns |
| `calc` | Windows Calculator | The display itself reads 68,315 and so does the answer |

Agent time is Claude Code's own `duration_ms`: model time plus tool time, after
the MCP server has connected.

## Run it

```powershell
uv venv --python 3.14 bench/.venv
uv pip install --python bench/.venv/Scripts/python.exe windows-mcp==0.8.8 websocket-client mcp
bench/.venv/Scripts/python.exe -X utf8 bench/agent_bench.py --arms stock,qc1,qc --reps 2 --tag main
bench/.venv/Scripts/python.exe -X utf8 bench/summarize.py --tag main
bench/.venv/Scripts/python.exe -X utf8 bench/trace.py bench/results/<run dir>
```

Do not touch the mouse or keyboard during a run. Stock Windows MCP pauses for
about ten seconds after any physical input, and the summary counts those runs as
interrupted. `watchdog_probe.py` reproduces a stock control dropout with no
physical input at all (see the results).
