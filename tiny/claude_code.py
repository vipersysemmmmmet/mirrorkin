"""Use Claude Code as tiny you's brain when there's no Anthropic API key yet.

Runs the `claude` command-line tool on the developer's own Claude subscription. This is
a stand-in for building and testing; real users' tinys go through the server and the API.

Safety: each call gets only the tools it needs (none for chat, web search for the Scout),
never file or shell access, and runs from an empty temporary folder.
"""

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


class ClaudeCodeError(Exception):
    pass


CLAUDE = os.environ.get("CLAUDE_BIN") or shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")


def available() -> bool:
    return os.path.exists(CLAUDE)


SANDBOX_TOOLS = ["mcp__sandbox__run", "mcp__sandbox__write_file", "mcp__sandbox__read_file"]
BROWSER_TOOLS = [f"mcp__browser__{t}" for t in
                 ("open_url", "read_page", "click", "type_text", "choose", "scroll", "go_back", "wait_for_human",
                  "check_email", "create_wallet", "list_wallets",
                  "start_background_task", "tell_human", "basedagents")]


def ask(prompt: str, system: str, *, image: bytes | None = None, tools: list[str] | None = None,
        schema: dict | None = None, timeout: int = 180, sandbox: str | None = None, browser: bool = False):
    """One request. Returns text, or a dict when `schema` is given.

    With `sandbox` (a running job container's name), the brain also gets tools to run code,
    and they only reach inside that container. With `browser`, she gets her own visible browser
    (rules enforced in browser_mcp.py).
    """
    if not available():
        raise ClaudeCodeError("Claude Code isn't installed, and there's no API key yet.")
    content = [{"type": "text", "text": prompt}]
    if image:
        content.insert(0, {"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg", "data": base64.standard_b64encode(image).decode()}})
    message = {"type": "user", "message": {"role": "user", "content": content}}
    cmd = [CLAUDE, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
           "--system-prompt", system, "--no-session-persistence",
           ]
    # The exact tool list: built-ins named in `tools`, plus the sandbox's own tools when there
    # is one (MCP tools must be listed here too, or they never load). Nothing else, so the brain
    # never gets Bash or file access on the Mac itself.
    allowed = list(tools or []) + (SANDBOX_TOOLS if sandbox else []) + (BROWSER_TOOLS if browser else [])
    cmd += ["--tools", ",".join(allowed)]
    if allowed:
        cmd += ["--allowedTools", ",".join(allowed)]
    if schema:
        cmd += ["--json-schema", json.dumps(schema)]
    with tempfile.TemporaryDirectory() as empty:
        servers = {}
        path_env = os.environ.get("PATH", "") + ":" + os.path.expanduser("~/.docker/bin")
        if sandbox:
            servers["sandbox"] = {"command": sys.executable, "args": [str(Path(__file__).with_name("sandbox_mcp.py"))],
                                  "env": {"TINY_SANDBOX": sandbox, "PATH": path_env}}
        if browser:
            env = {"PATH": path_env}
            env.update({k: os.environ[k] for k in ("TINY_BROWSER_HIDDEN", "TINY_TEST_HUMAN") if k in os.environ})
            servers["browser"] = {"command": sys.executable, "args": [str(Path(__file__).with_name("browser_mcp.py"))],
                                  "env": env}
        if servers:
            config = {"mcpServers": servers}
            config_path = os.path.join(empty, "mcp.json")
            with open(config_path, "w") as f:
                json.dump(config, f)
            # --strict-mcp-config: only this server, never any other MCP servers on the machine
            cmd += ["--mcp-config", config_path, "--strict-mcp-config"]
        # The sandbox connector starts alongside Claude Code; if it isn't ready when the
        # session begins, the brain has no tools for the whole run. Retry when that happens.
        for _attempt in range(3):
            try:
                run = subprocess.run(cmd, input=json.dumps(message) + "\n", capture_output=True,
                                     text=True, timeout=timeout, cwd=empty,
                                     # Wait for the sandbox connector before starting
                                     env={**os.environ, "MCP_CONNECTION_NONBLOCKING": "false"})
            except subprocess.TimeoutExpired:
                raise ClaudeCodeError("That took too long. Try again?")
            result, sandbox_ready = None, False
            for line in run.stdout.splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("subtype") == "init":
                    connected = {s.get("name") for s in event.get("mcp_servers", []) if s.get("status") == "connected"}
                    sandbox_ready = set(servers) <= connected
                if event.get("type") == "result":
                    result = event
            if not servers or sandbox_ready:
                break
        else:
            raise ClaudeCodeError("My tools didn't start (sandbox needs Docker running).")
    if result is None or result.get("is_error"):
        detail = (result or {}).get("result") or run.stderr.strip()[:200] or "no reply"
        raise ClaudeCodeError(f"Claude Code couldn't answer: {detail}")
    if schema:
        if not isinstance(result.get("structured_output"), dict):
            raise ClaudeCodeError("Claude Code didn't return the expected format.")
        return result["structured_output"]
    return (result.get("result") or "").strip()


def transcript(history: list[dict]) -> str:
    """Turn a chat history into one prompt (the CLI takes a single message)."""
    lines = []
    for m in history[:-1]:
        who = "Human" if m["role"] == "user" else "You (tiny)"
        lines.append(f"{who}: {m['content']}")
    earlier = "Conversation so far:\n" + "\n".join(lines) + "\n\n" if lines else ""
    return f"{earlier}Your human just said: {history[-1]['content']}\n\nReply as tiny you."
