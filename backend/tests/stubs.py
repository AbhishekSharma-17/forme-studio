"""Shared OpenAI stub for hermetic tests.

Builds a fake ``AsyncOpenAI``-shaped object whose ``images.generate`` and
``images.edit`` methods can be inspected after a call (records kwargs and
returns either a one-shot response or an async-iterable stream of partial
+ completed events).
"""

from __future__ import annotations

from typing import Any

# A 1x1 transparent PNG, base64-encoded.
TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


class StubData:
    def __init__(self, b64: str) -> None:
        self.b64_json = b64


class StubUsage:
    """Mimics OpenAI's usage block: top-level scalar fields + nested dict."""

    def __init__(self) -> None:
        self.input_tokens = 1200
        self.output_tokens = 4096
        self.input_tokens_details = {"cached_tokens": 200, "image_tokens": 400}
        self.total_tokens = 1200 + 4096

    def model_dump(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "input_tokens_details": self.input_tokens_details,
            "total_tokens": self.total_tokens,
        }


class StubResponse:
    def __init__(self, n: int) -> None:
        self.data = [StubData(TINY_PNG_B64) for _ in range(n)]
        self.usage = StubUsage()


class StubStreamEvent:
    """Mimics one event in the gpt-image-2 streaming response."""

    def __init__(
        self,
        etype: str,
        b64: str,
        idx: int,
        usage: StubUsage | None = None,
    ) -> None:
        self.type = etype
        self.b64_json = b64
        self.image_generation_index = idx
        self.usage = usage


class StubStream:
    """Async-iterable replay of pre-built events."""

    def __init__(self, events: list[StubStreamEvent]) -> None:
        self._events = events

    def __aiter__(self) -> StubStreamIter:
        return StubStreamIter(self._events)


class StubStreamIter:
    def __init__(self, events: list[StubStreamEvent]) -> None:
        self._events = events
        self._i = 0

    def __aiter__(self) -> StubStreamIter:
        return self

    async def __anext__(self) -> StubStreamEvent:
        if self._i >= len(self._events):
            raise StopAsyncIteration
        ev = self._events[self._i]
        self._i += 1
        return ev


class StubImages:
    def __init__(self) -> None:
        self.edit_calls: list[dict[str, Any]] = []
        self.generate_calls: list[dict[str, Any]] = []

    async def generate(self, **kwargs: Any) -> StubResponse | StubStream:
        self.generate_calls.append(kwargs)
        if kwargs.get("stream"):
            return make_stream(int(kwargs.get("n", 1)))
        return StubResponse(int(kwargs.get("n", 1)))

    async def edit(self, **kwargs: Any) -> StubResponse | StubStream:
        """Returns a stream when ``stream=True``, a response otherwise."""
        self.edit_calls.append(kwargs)
        if kwargs.get("stream"):
            return make_stream(int(kwargs.get("n", 1)))
        return StubResponse(int(kwargs.get("n", 1)))


def make_stream(n: int) -> StubStream:
    """Build one partial + one completed per variant; usage on the first completed."""
    events: list[StubStreamEvent] = []
    for idx in range(n):
        events.append(
            StubStreamEvent("image_generation.partial_image", TINY_PNG_B64, idx)
        )
        events.append(
            StubStreamEvent(
                "image_generation.completed",
                TINY_PNG_B64,
                idx,
                StubUsage() if idx == 0 else None,
            )
        )
    return StubStream(events)


class StubOpenAIClient:
    def __init__(self) -> None:
        self.images = StubImages()
