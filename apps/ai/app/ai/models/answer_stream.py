"""Incrementally decode only the JSON answer string; never expose the envelope."""

import json
import re
from collections.abc import Awaitable, Callable

from app.ai.errors import InvalidStructuredOutput

AnswerDelta = Callable[[str | None], Awaitable[None]]


class AnswerDecoder:
    def __init__(self) -> None:
        self.raw = ""
        self.visible = ""

    def feed(self, content: str) -> str:
        self.raw += content
        if len(self.raw) > 100_000:
            raise InvalidStructuredOutput("Streaming response exceeds its limit")
        match = re.match(r'^\s*\{\s*"answer"\s*:\s*"', self.raw)
        if not match:
            # Hold incomplete/legacy envelopes for final validation. Never display them.
            return ""
        fragment = self.raw[match.end() :]
        end = 0
        while end < len(fragment):
            if fragment[end] == '"':
                break
            if fragment[end] == "\\":
                width = 6 if fragment[end : end + 2] == "\\u" else 2
                if end + width > len(fragment):
                    break
                end += width
            else:
                end += 1
        try:
            value: str = json.loads('"' + fragment[:end] + '"')
        except ValueError, UnicodeError:
            return ""
        # Hold markup from its opening angle bracket so split thinking tags can
        # never become provisional text. Final validation rejects thinking tags.
        if "<" in value:
            value = value[: value.index("<")]
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            return ""
        if not value.startswith(self.visible):
            raise InvalidStructuredOutput("Streaming answer changed its prefix")
        delta = value[len(self.visible) :]
        self.visible = value
        return delta
