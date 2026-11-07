<h1 align="center">QuickControl</h1>

<p align="center"><b>Faster Windows control for AI agents.</b><br>
Batch desktop actions through one MCP connection and measure where the time goes.</p>

Starting a desktop server for every command adds seconds before anything happens.
QuickControl keeps one connection open for a sequence of Windows MCP calls, saves
each result, and stops the sequence when a tool fails. In a local test, three
screenshots took **25.1 seconds separately and 10.1 seconds in one batch**.

Built on [CursorTouch's Windows MCP](https://github.com/CursorTouch/Windows-MCP).
Windows MCP provides the desktop tools, screenshots, accessibility tree, and
control ownership checks. I added connection reuse for command sequences,
per-call timings, separate output files, coordinate conversion checks, and a
benchmark harness. There is also an experimental profile for faster short-text
entry. This repository contains the client and experiments; it depends on the
upstream server.

## Run

Requires Windows, [uv](https://docs.astral.sh/uv/), and an interactive desktop.
The measured server version is Windows MCP 0.8.8 on Python 3.14.

```powershell
git clone https://github.com/EnesYilmazcode/QuickControl.git
cd QuickControl
uv run --python 3.14 --with mcp python quickcontrol.py --batch '@benchmark.json'
```

By default, QuickControl reads the `windows-mcp` entry from
`~/.codex/config.toml`. If you already use Windows MCP there, no configuration
change is needed. Otherwise, save this as `server.local.toml`:

```toml
[mcp_servers.windows-mcp]
command = 'uvx'
args = ['--python', '3.14', 'windows-mcp==0.8.8', 'serve']
startup_timeout_sec = 120
```

Pass it with `--config server.local.toml`. Use an absolute executable path if
`uvx` is not on your PATH. QuickControl does not change your MCP registration.

## One call or a sequence

Check control ownership:

```powershell
uv run --with mcp python quickcontrol.py ControlStatus
```

Capture one display using the included argument file:

```powershell
uv run --with mcp python quickcontrol.py Screenshot '@screenshot.json'
```

A batch is an array of tool calls. The included `benchmark.json` checks ownership
and captures three screenshots:

```json
[
  {"name": "ControlStatus"},
  {"name": "Screenshot", "arguments": {"display": [0]}},
  {"name": "Screenshot", "arguments": {"display": [0]}},
  {"name": "Screenshot", "arguments": {"display": [0]}}
]
```

Calls run in order on one initialized session. An MCP error stops the remaining
calls. A ControlStatus response that is not `ready` also stops the batch.
Completed actions stay completed; nothing is automatically retried or rolled
back. Upstream ownership checks still apply to desktop input.

Use `@file.json` arguments in PowerShell to avoid native-command quoting issues.
Run without a tool name to list the server's tool schemas.

## Results you can inspect

Each run gets its own directory under `~/.codex/tmp/windows-mcp/`:

```text
20261107-002301-271d7601/
  session.json    connection startup time
  calls.json      arguments, timings, text, image paths, and error flags
  001-1.png       a screenshot from one call
```

Use `--output artifacts` to keep runs in the project directory, or `--timeout 30`
to change the per-call timeout in seconds. Calls are limited to 100 per batch.
Logs include tool arguments and returned text; screenshots contain what was on
the desktop. Local artifacts are excluded from Git.

When using an agent with directly exposed Windows MCP tools, use those tools.
They already keep a connection open. QuickControl's batch client helps when
calling the server through individual shell commands.

## What helped

| Change | Local result |
| --- | --- |
| Reuse one connection for three captures | 25.057 s to 10.142 s, 59.5% less wall time |
| Use a screenshot when a UI tree is unnecessary | Screenshot median 0.313 s; one full snapshot 22.791 s |
| Crop a known window | 92.5% smaller image payload; no capture-time improvement in this run |
| Paste short plain text, experimental | Three-field fill mean 6.86 s to 5.58 s, 18.6% less time |

These are small local measurements, not a claim that all computer-use tasks run
60% faster. The snapshot returns more information than a screenshot. The batch
comparison has one completed round; the typing comparison has two runs per
profile. See [measurements](measurements/README.md) for the setup and saved values.

For ordinary desktop work, start with a screenshot of the relevant display.
Crop when the target is known, and request the accessibility tree when you need
control identities. Cropped image coordinates need both the crop offset and the
image scale before they can be used for a click. The tested
`image_to_desktop()` helper handles that conversion.

Batch actions only while their targets stay stable. After navigation or layout
changes, inspect a fresh observation before deciding where to click next.

## Experimental text profile

`fast_server.py` lowers the upstream short-text paste threshold from 20 characters
to 1. It uses Windows MCP's existing paste implementation and retains its
clipboard restoration and ownership checks. Text containing braces, newlines,
or tabs keeps the original keystroke path.

From this repository directory:

```powershell
uv run --with mcp python quickcontrol.py --config fast-profile.example.toml --batch '@benchmark.json'
```

This starts the experimental server for that invocation only. To test typing,
open `fixture.html`, observe the current field positions, and create a batch
with Type or MultiEdit calls. The **Verify locally** button checks three exact
values: `alpha-123`, `bravo+456`, and `charlie {789}`.

Both measured Chrome fills passed, including replacement of existing values.
Windows Forms controls behaved differently: `clear=true` reported success but
left old text in repeated tests. Check the actual result before continuing.

The profile uses a private upstream attribute and is not enabled by default.
Other apps may handle paste differently, and restoring plain clipboard text
does not preserve every rich clipboard format.

## Development

```powershell
python -m unittest -v test_client.py
powershell -NoProfile -File .\benchmark.ps1
```

The tests cover batch cancellation, user control, SDK field compatibility, and
coordinate conversion. They do not touch the desktop. The benchmark makes real
read-only captures and takes about 35 seconds on the measured setup.

## Credits and license

[Windows MCP](https://github.com/CursorTouch/Windows-MCP), by Jeomon George and
CursorTouch contributors, provides the underlying desktop automation and is
MIT licensed. QuickControl is an independent companion project by Enes Yilmaz.

QuickControl is [MIT licensed](LICENSE).
