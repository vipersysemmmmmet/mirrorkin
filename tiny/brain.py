"""Tiny you's conversational brain.

Each AI provider is one class with a `reply` method. Pick one with TINY_BRAIN in
.env (default: claude). Add another model by writing a class here and
registering it in BRAINS.
"""

import base64
import os

import anthropic


def with_image(history: list[dict], image: bytes | None) -> list[dict]:
    """Attach a JPEG screenshot to the last user message."""
    if not image:
        return history
    last = history[-1]
    blocks = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                           "data": base64.standard_b64encode(image).decode()}},
              {"type": "text", "text": last["content"]}]
    return history[:-1] + [{"role": "user", "content": blocks}]


def plain(text: str) -> str:
    """Tidy markdown for tiny you's plain-text chat window and speech bubble."""
    import re
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"\1: \2", text)  # [title](url) -> title: url
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)                           # **bold**
    text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)                     # headings
    return text.strip()


class BrainError(Exception):
    pass


class ClaudeBrain:
    MODEL = "claude-opus-5-5"

    def __init__(self) -> None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise BrainError("I can't talk yet: add your ANTHROPIC_API_KEY to .env.")
        self.client = anthropic.Anthropic()

    # Web search lets tiny you look up anything current; a few searches per message at most.
    TOOLS = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 3}]
    MAX_CONTINUATIONS = 3

    def reply(self, system: str, history: list[dict], image: bytes | None = None) -> str:
        messages = with_image(history, image)
        assistant_content = []
        try:
            for _ in range(self.MAX_CONTINUATIONS):
                response = self.client.beta.messages.create(
                    model=self.MODEL,
                    max_tokens=8000,
                    system=system,
                    messages=messages,
                    tools=self.TOOLS,
                    thinking={"type": "adaptive"},
                    output_config={"effort": "low"},
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                )
                if response.stop_reason != "pause_turn":
                    break
                # The search loop paused; send back what we have and it resumes.
                assistant_content += response.content
                messages = with_image(history, image) + [{"role": "assistant", "content": assistant_content}]
        except anthropic.AuthenticationError:
            raise BrainError("My API key doesn't work. Check ANTHROPIC_API_KEY in .env.")
        except anthropic.RateLimitError:
            raise BrainError("I'm rate limited. Give me a minute.")
        except anthropic.APIStatusError as e:
            raise BrainError(f"API error ({e.status_code}): {e.message}")
        except anthropic.APIConnectionError:
            raise BrainError("I can't reach the internet right now.")
        if response.stop_reason == "refusal":
            raise BrainError("I'd rather not answer that one.")
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        if not text:
            raise BrainError("I lost my train of thought. Try again?")
        return plain(text)


class ServerBrain:
    """Chats through the Soul Simulator server, which holds the AI keys and enforces plan limits."""

    def reply(self, system: str, history: list[dict], image: bytes | None = None) -> str:
        from . import account
        body = {"system": system, "messages": history}
        if image:
            body["image"] = base64.standard_b64encode(image).decode()
        try:
            return account.call("POST", "/chat", body)["reply"]
        except account.ServerError as e:
            if e.status == 401:
                raise BrainError("Please log in again from Account & plan.")
            raise BrainError(str(e))


class ClaudeCodeBrain:
    """Stand-in brain: Claude Code on the developer's own subscription, until there's an API key."""

    def reply(self, system: str, history: list[dict], image: bytes | None = None) -> str:
        from . import claude_code
        try:
            # Chat gets her full toolkit: web, her own browser and her own inbox (rules in browser_mcp.py)
            return plain(claude_code.ask(claude_code.transcript(history), system, image=image,
                                         tools=["WebSearch", "WebFetch"], browser=True, timeout=900,
                                         hands_optional=True))
        except claude_code.ClaudeCodeError as e:
            raise BrainError(str(e))


BRAINS = {"claude": ClaudeBrain, "server": ServerBrain, "claude_code": ClaudeCodeBrain}


def get_brain():
    from . import account
    # Logged-in users chat through the server; developers can use their own key directly;
    # with neither, Claude Code stands in.
    if account.logged_in():
        default = "server"
    elif os.environ.get("ANTHROPIC_API_KEY"):
        default = "claude"
    else:
        default = "claude_code"
    name = os.environ.get("TINY_BRAIN", default).lower()
    if name not in BRAINS:
        raise BrainError(f"Unknown TINY_BRAIN '{name}'. Options: {', '.join(BRAINS)}.")
    return BRAINS[name]()
