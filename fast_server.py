"""Experimental short-text paste profile; preserves upstream arbitration and tools."""
from windows_mcp.desktop.service import Desktop
from windows_mcp.__main__ import main

# Existing upstream paste implementation retains clipboard restoration and
# ownership checkpoints. Braces/newlines/tabs continue using the original path.
Desktop._LONG_TEXT_PASTE_THRESHOLD = 1

if __name__ == '__main__':
    main()
