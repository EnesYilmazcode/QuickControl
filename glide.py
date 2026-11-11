"""Visible cursor glide before Click and Type, so a person can follow the agent.

Upstream Click teleports the cursor. This eases it from where it is to the
target first, along a straight line, so the eye can track each step. The path
is deterministic: same start and end, same path. QC_GLIDE_MS sets the longest
glide (default 450 ms); 0 turns it off.
"""
import functools
import os
import time

MIN_MS = 150


def ease(t):
    """Smoothstep: slow start, quick middle, slow arrival."""
    return t * t * (3 - 2 * t)


def glide_path(start, end, max_ms, fps=60):
    """Points from start (excluded) to end (included), and the frame delay."""
    (x0, y0), (x1, y1) = start, end
    dist = max(abs(x1 - x0), abs(y1 - y0))
    if max_ms <= 0 or dist < 4:
        return [], 0.0
    ms = min(max_ms, MIN_MS + dist * 0.25)
    frames = max(2, int(ms / 1000 * fps))
    pts = []
    for i in range(1, frames + 1):
        k = ease(i / frames)
        pts.append((round(x0 + (x1 - x0) * k), round(y0 + (y1 - y0) * k)))
    return pts, ms / 1000 / frames


def target_of(loc):
    if loc is None:
        return None
    if isinstance(loc, (list, tuple)) and len(loc) == 2 and all(isinstance(v, int) and not isinstance(v, bool) for v in loc):
        return int(loc[0]), int(loc[1])
    return None


def install(max_ms=None):
    """Wrap Desktop.click and Desktop.type. Returns the originals."""
    from windows_mcp.desktop import service
    if max_ms is None:
        max_ms = int(os.environ.get('QC_GLIDE_MS', '450'))
    originals = (service.Desktop.click, service.Desktop.type)
    if getattr(service.Desktop.click, '_quickcontrol_glide', False) or max_ms <= 0:
        return originals

    def glide_to(loc):
        target = target_of(loc)
        if target is None:
            return
        owner = service.get_controller()
        pts, delay = glide_path(tuple(service.uia.GetCursorPos()), target, max_ms)
        for x, y in pts:
            owner.checkpoint_current()
            service.uia.SetCursorPos(x, y)
            owner.record_step_current()
            time.sleep(delay)

    def wrap(fn):
        @functools.wraps(fn)
        def wrapped(self, loc, *args, **kwargs):
            glide_to(loc)
            return fn(self, loc, *args, **kwargs)
        wrapped._quickcontrol_glide = True
        return wrapped

    service.Desktop.click = wrap(originals[0])
    service.Desktop.type = wrap(originals[1])
    return originals
