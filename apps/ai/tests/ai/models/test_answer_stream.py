import json

import httpx
import pytest

from app.ai.models.answer_stream import AnswerDecoder
from app.ai.models.ollama import create_ollama_adapter
from app.ai.models.ollama_http import OllamaSettings
from app.ai.models.profiles import load_model_profile
from app.ai.schemas import ConversationGenerationRequest, ConversationMessage


def test_decoder_handles_split_json_escapes_and_holds_thinking_tags() -> None:
    decoder = AnswerDecoder()
    output = ""
    for character in json.dumps({"answer": "Hello\nLocal Ω answer"}):
        output += decoder.feed(character)
    assert output == "Hello\nLocal Ω answer"
    decoder = AnswerDecoder()
    assert decoder.feed('{"answer":"<thi') == ""
    assert decoder.feed('nk>private reasoning</think>"}') == ""


@pytest.mark.asyncio
async def test_real_adapter_streams_only_answer_and_disposes_thinking() -> None:
    profile = load_model_profile()
    emitted: list[str | None] = []
    requests: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3:4b"}]})
        payload = json.loads(request.content)
        requests.append(payload)
        frames = []
        for content, done in [('{"answer":"Hello', False), (' world"}', True)]:
            frames.append(
                json.dumps(
                    {
                        "model": "qwen3:4b",
                        "done": done,
                        "message": {"role": "assistant", "content": content, "thinking": "PRIVATE"},
                        "done_reason": "stop" if done else None,
                    }
                )
            )
        return httpx.Response(200, content="\n".join(frames))

    adapter = create_ollama_adapter(
        profile=profile, settings=OllamaSettings(), transport=httpx.MockTransport(handler)
    )

    async def delta(text: str | None) -> None:
        emitted.append(text)

    try:
        result = await adapter.generate_conversation(
            ConversationGenerationRequest(
                model="qwen3:4b",
                system_prompt="Answer clearly",
                messages=(ConversationMessage(role="user", content="Hi"),),
                limits=profile.text_limits,
            ),
            on_delta=delta,
        )
    finally:
        await adapter.close()
    assert result.text == "Hello world"
    assert emitted == [None, "Hello", " world"]
    assert requests[0]["stream"] is True and requests[0]["think"] is False
    assert "PRIVATE" not in str(emitted)
