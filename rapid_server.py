"""QuickControl R&D profile with native Unicode input and bounded sequences."""
from runtime_profile import install
from rapid_input import install as install_input
from sequence_tool import install as install_sequence
from uia_context import install as install_context

install()
install_context()
install_input()
install_sequence()

from windows_mcp.__main__ import main

if __name__ == '__main__':
    main()
