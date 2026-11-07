# Measurements

Recorded November 7, 2026 on Windows with Windows MCP 0.8.8, Python 3.14,
two 1080p displays, and a local Chrome form. Values are in [results.json](results.json).
Original logs and desktop captures remain local; they are not shipped here.

## Connection reuse

The same client captured three primary-display screenshots in separate processes,
then captured three screenshots and called ControlStatus inside one connection.
Separate launches took 25.057 seconds total; the batch took 10.142 seconds.
That is 59.5% less wall time in one completed comparison, including startup and
shutdown. Each process exited successfully. An attempted second round exceeded
the shell timeout and is excluded.

This measures the fallback command path. Agents using directly exposed MCP tools
already have a persistent connection and do not gain this startup saving.

## Observation

One full accessibility snapshot took 22.791 seconds and did not expose Chrome's
controls. Three primary-display screenshot calls had a median of 0.313 seconds.
The two tools return different information; this is a workflow choice, not an
equivalent-output benchmark.

Cropping the Chrome window reduced encoded image size from 749,994 to 56,079
bytes (92.5%). It did not improve median capture time in this run: 0.718 seconds
for the crop versus 0.313 seconds for the primary display. Smaller image input
may help an agent read a target, but downstream model latency was not measured.

## Typing

Both profiles used the fallback client and the same three fields. Upstream took
7.006 and 6.714 seconds; the experimental paste profile took 5.634 and 5.534 seconds.
Mean time fell 18.6%. The fixture's exact-value verification showed PASS after
both experimental fills, including replacement of existing content. Two fields
used paste; the field containing braces retained upstream keystroke typing.

Separate direct-tool tests found little engine-side difference between MultiEdit
and three Type calls: both took roughly 6.6 seconds. MultiEdit can still reduce
the number of model/tool round trips.

Windows Forms test fields failed repeated clear=true replacement even though the
tool reported success. Ctrl+A did not select their contents; Home then Shift+End
visibly selected a single line. Chrome replacement passed. This is a reason to
check actual outcomes, not evidence of an overall accuracy improvement.

## Reproduce

With uv and Windows MCP configured under `[mcp_servers.windows-mcp]` in
`~/.codex/config.toml`, run from the repository directory:

```powershell
powershell -NoProfile -File .\benchmark.ps1
```

This takes about 35 seconds on the measured setup and performs read-only captures.
It saves logs and images under ignored `artifacts/`, plus a local
`fallback-comparison.json` containing exit codes and wall times. Do not include
failed processes when calculating speed improvements.

For typing, open `fixture.html` in a separate browser window, take a screenshot,
and construct Type or MultiEdit arguments from the current field locations.
Use the same inputs and window position for both profiles, repeat replacement,
then click **Verify locally**. `fixture.ps1` provides the Windows Forms variant.
Do not reuse coordinates from a different machine or window placement.

These small samples do not measure complete agent task duration, reasoning time,
network waits, or general accuracy. Cross-app paste behavior, Unicode, rich
clipboard formats, and delayed paste handlers need broader testing.
