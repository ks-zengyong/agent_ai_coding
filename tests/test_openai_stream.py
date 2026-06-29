"""Tests for OpenAI provider streaming event parsing."""
from __future__ import annotations

import json

import pytest

from src.llm.base import StreamEventType
from src.llm.openai_provider import OpenAIProvider


@pytest.mark.asyncio
async def test_chat_stream_events_parses_reasoning_and_content(monkeypatch):
    """SSE chunks with reasoning_content yield THINKING_DELTA then TEXT_DELTA."""

    sse_lines = [
        'data: {"choices":[{"delta":{"reasoning_content":"think "},"index":0}]}',
        'data: {"choices":[{"delta":{"content":"hi"},"index":0}]}',
        "data: [DONE]",
    ]

    class FakeResponse:
        status_code = 200

        async def aiter_lines(self):
            for line in sse_lines:
                yield line

        def raise_for_status(self):
            pass

    class FakeStreamCtx:
        async def __aenter__(self):
            return FakeResponse()

        async def __aexit__(self, *args):
            pass

    class FakeClient:
        def stream(self, *args, **kwargs):
            return FakeStreamCtx()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(
        "src.llm.openai_provider.httpx.AsyncClient",
        lambda **kwargs: FakeClient(),
    )

    provider = OpenAIProvider(api_url="http://localhost/v1", api_key="k")
    events = []
    async for event in provider.chat_stream_events([]):
        events.append(event)

    types = [e.type for e in events]
    assert StreamEventType.THINKING_DELTA in types
    assert StreamEventType.TEXT_DELTA in types
    assert StreamEventType.DONE in types

    thinking = "".join(e.text for e in events if e.type == StreamEventType.THINKING_DELTA)
    text = "".join(e.text for e in events if e.type == StreamEventType.TEXT_DELTA)
    assert thinking == "think "
    assert text == "hi"
