#!/usr/bin/env python

"""
MCP server for the Cozmo robot: lets an AI assistant that speaks the Model Context Protocol look through the camera,
move and talk (docs/specs/mcp-server.md). It talks on its standard input and output; nothing listens on the network.

    pip install pycozmo[mcp]
    pycozmo_mcp.py [--no-drive] [--max-speed MMPS] [--max-drive MM]
"""

import sys

from pycozmo.mcp_server import main


if __name__ == "__main__":
    sys.exit(main())
