# Desktop profile validation

November 7, 2026, Windows MCP 0.8.8 and Python 3.14.

- 19 offline tests pass: client behavior, Escape down/up timing, auto-repeat,
  intervening keys, lease changes, ordinary physical input filtering, injected
  input passthrough, screenshot suspension, expired hook deadlines, upstream
  behavior outside a lease, monitor defaults, explicit regions and tool help.
- `verify_native_profile.py` passes with the installed Windows MCP structures
  and real ControlCoordinator. Simulated physical callbacks ignore mouse/raw
  motion, distinguish key repeat from double Escape, cancel checkpoints before
  queue processing, enter user control, and leave no AI-held inputs pending.
- A live stdio server launched through `profile.example.toml` captures the primary
  display by default (1920 x 1080), explicitly captures display 1, captures the
  3840 x 1080 virtual desktop with `display=[0,1]` (image downscaled by upstream),
  and captures a 200 x 200 region on the second display. All calls completed.
- The configured host registration launches the profile successfully. Returned
  tool help names the primary-monitor default, and an argument-free Screenshot
  returns only display 0 on the measured host.
- The overlay panel function returns None; the existing edge/cursor indicator
  and health-check path remain enabled.

The keyboard callback tests use simulated event structures, not physical
keypresses. A manual double-Escape test on the target keyboard remains useful.
No speed claim is made from these behavior checks. Desktop images and host
configuration backups are kept local.
