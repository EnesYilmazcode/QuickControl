"""Windows MCP with deliberate Escape takeover and primary-display captures."""
from runtime_profile import install

install()

from windows_mcp.__main__ import main

if __name__ == '__main__':
    main()
