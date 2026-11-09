"""QuickControl agent profile: rapid input plus Observe, Act, SurveyMenus and OpenBrowser."""
from runtime_profile import install
from rapid_input import install as install_input
from sequence_tool import install as install_sequence
from uia_context import install as install_context
from agent_tools import install as install_agent

install()
install_context()
install_input()
install_sequence()
install_agent()

from windows_mcp.__main__ import main

if __name__ == '__main__':
    main()
