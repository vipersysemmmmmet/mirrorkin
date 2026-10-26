"""MCP server giving tiny you's brain tools that only touch her job sandbox.

Started by Claude Code for one job; the container name comes from TINY_SANDBOX.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.mcpserver import MCPServer  # noqa: E402

from tiny import sandbox  # noqa: E402

CONTAINER = os.environ["TINY_SANDBOX"]
server = MCPServer("sandbox", instructions=(
    "An isolated Linux machine (Node 22, Python 3, git, build tools; internet access) for doing "
    f"this job. Work in {sandbox.WORKDIR}. Nothing here touches the human's computer."))


@server.tool(description="Run a bash command in the job sandbox and return its output.")
def run(command: str, timeout_seconds: int = 600) -> str:
    return sandbox.run(CONTAINER, command, timeout=min(max(timeout_seconds, 1), 1800))


@server.tool(description="Write a text file in the job sandbox (relative paths go under the work folder).")
def write_file(path: str, content: str) -> str:
    return sandbox.write_file(CONTAINER, path, content)


@server.tool(description="Read a text file from the job sandbox.")
def read_file(path: str) -> str:
    return sandbox.read_file(CONTAINER, path)


if __name__ == "__main__":
    server.run("stdio")
