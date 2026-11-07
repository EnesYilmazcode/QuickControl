"""Comparison profile: original Windows MCP typing with QuickControl control policy."""
from runtime_profile import install

install()

from windows_mcp.__main__ import main

if __name__ == '__main__':
    main()
