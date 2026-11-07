"""Small, platform-independent decisions for QuickControl's desktop profile."""


class EscapeTakeover:
    """Require two distinct Escape presses; holding Escape never counts twice."""

    def __init__(self, interval=0.5):
        self.interval = interval
        self.first = None
        self.down = False
        self.generation = None

    def key(self, vk, down, now, generation):
        if generation != self.generation:
            self.first = None
            self.down = False
            self.generation = generation
        if vk != 0x1B:
            if down:
                self.first = None
            return False
        if not down:
            self.down = False
            return False
        if self.down:
            return False
        self.down = True
        if self.first is not None and 0 <= now - self.first <= self.interval:
            self.first = None
            return True
        self.first = now
        return False


def default_displays(displays, selected, region):
    """An explicit selection (including [] for all displays) always wins."""
    if selected is not None or region is not None:
        return selected
    if not displays:
        raise RuntimeError('No active display available')
    primary = next((display for display in displays if display.primary), displays[0])
    return [primary.index]
