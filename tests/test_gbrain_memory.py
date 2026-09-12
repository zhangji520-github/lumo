from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp import types as mcp_types

from lumo.agent import Agent
from lumo.config import MemoryConfig
from lumo.conversation import ConversationManager
from lumo.memory.auto_memory import MemoryManager
from lumo.memory.gbrain import GBrainMemoryBackend


class FakeGBrainClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, arguments))
        if name == "context_pack":
            payload = {"protocol_version": 1, "text": "warm project context"}
        elif name == "recall":
            payload = {
                "protocol_version": 1,
                "facts": [
                    {
                        "fact": "Lumo uses hybrid memory",
                        "fact_id": "42",
                        "provenance": "design review",
                    }
                ],
                "results": [],
            }
        else:
            payload = {"protocol_version": 1, "status": "inserted", "id": "43"}
        return SimpleNamespace(
            isError=False,
            content=[mcp_types.TextContent(type="text", text=json.dumps(payload))],
        )


class FakeMCPManager:
    def __init__(self, client: FakeGBrainClient) -> None:
        self.client = client

    async def get_client(self, name: str):
        return self.client


@pytest.mark.asyncio
async def test_gbrain_recall_warms_once_and_renders_provenance(tmp_path: Path) -> None:
    client = FakeGBrainClient()
    backend = GBrainMemoryBackend(
        FakeMCPManager(client),
        server_name="gbrain",
        project_root=str(tmp_path / "Lumo"),
        timeout_seconds=1,
        budget_tokens=1200,
    )

    first = await backend.recall("what memory mode?", session_id="session-a")
    second = await backend.recall("what memory mode?", session_id="session-a")

    assert first.available is True
    assert "warm project context" in first.text
    assert "Lumo uses hybrid memory" in first.text
    assert "provenance=design review" in first.text
    assert second.text.count("warm project context") == 0
    assert [name for name, _ in client.calls].count("context_pack") == 1
    assert [name for name, _ in client.calls].count("recall") == 2


@pytest.mark.asyncio
async def test_gbrain_unavailable_fails_open(tmp_path: Path) -> None:
    backend = GBrainMemoryBackend(
        None,
        server_name="gbrain",
        project_root=str(tmp_path),
        timeout_seconds=0.1,
        budget_tokens=500,
    )
    result = await backend.recall("prior decision")
    assert result.available is False
    assert "do not infer that prior memory is empty" in result.warning


@pytest.mark.asyncio
async def test_gbrain_outbox_retries_transport_failure(tmp_path: Path) -> None:
    client = FakeGBrainClient()
    backend = GBrainMemoryBackend(
        FakeMCPManager(client),
        server_name="gbrain",
        project_root=str(tmp_path),
        timeout_seconds=1,
        budget_tokens=500,
    )
    backend.queue_remember(
        {"fact": "durable fact", "provenance": "user request"},
        "connection lost",
    )
    assert backend.outbox_path.is_file()

    assert await backend.flush_outbox() == 1
    assert not backend.outbox_path.exists()
    assert ("remember", {"fact": "durable fact", "provenance": "user request"}) in client.calls


@pytest.mark.asyncio
async def test_agent_consumes_pending_recall_before_inference() -> None:
    agent = object.__new__(Agent)
    agent.memory_manager = SimpleNamespace()
    agent.memory_recall_task = asyncio.create_task(asyncio.sleep(0, result="recalled"))
    agent._memory_recall_consumed = False
    conversation = ConversationManager()
    conversation.add_user_message("question")

    assert await agent._recall_long_term_memory(conversation) == "recalled"
    assert agent._memory_recall_consumed is True
    assert agent.memory_recall_task is None


@pytest.mark.asyncio
async def test_hybrid_prefetch_keeps_markdown_when_gbrain_is_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    manager = MemoryManager(
        str(tmp_path / "project"),
        config=MemoryConfig(mode="hybrid", auto_capture=False),
        mcp_manager=None,
    )
    memory_file = manager.project_mem_dir / "decision.md"
    memory_file.parent.mkdir(parents=True, exist_ok=True)
    memory_file.write_text(
        "---\nname: decision\ndescription: architecture choice\ntype: project\n---\n\nUse hybrid memory.\n",
        encoding="utf-8",
    )

    async def selector(system: str, user: str) -> str:
        return '{"selected_memories":["decision.md"]}'

    result = await manager.prefetch("architecture", selector=selector)
    assert "Use hybrid memory" in result
    assert "GBrain long-term memory was unavailable" in result


@pytest.mark.asyncio
async def test_extraction_writes_top_level_type_and_multiline_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    manager = MemoryManager(str(tmp_path / "project"))
    conversation = ConversationManager()
    conversation.add_user_message("Remember my preference")

    class ExtractClient:
        async def stream(self, *args, **kwargs):
            from lumo.tools.base import StreamEnd, TextDelta

            yield TextDelta(
                "MEMORY_NAME: review-style\n"
                "MEMORY_TYPE: feedback\n"
                "MEMORY_DESC: review preference\n"
                "MEMORY_BODY: First line.\nSecond line.\n---"
            )
            yield StreamEnd(stop_reason="end")

    await manager.extract(ExtractClient(), conversation, "openai")
    saved = manager.user_mem_dir / "review-style.md"
    content = saved.read_text(encoding="utf-8")
    assert "\ntype: feedback\n" in content
    assert "metadata:" not in content
    assert "First line.\nSecond line." in content
