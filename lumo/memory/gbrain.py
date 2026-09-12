"""GBrain MEMORY_VERBS v1 adapter used by Lumo's memory orchestrator."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lumo.mcp.manager import MCPManager

log = logging.getLogger(__name__)


class GBrainUnavailable(RuntimeError):
    """The configured GBrain server could not serve the requested operation."""


@dataclass
class GBrainRecall:
    text: str = ""
    available: bool = True
    warning: str = ""


class GBrainMemoryBackend:
    """Small, fail-open adapter over GBrain's frozen seven-verb MCP surface."""

    def __init__(
        self,
        manager: MCPManager | None,
        *,
        server_name: str,
        project_root: str,
        timeout_seconds: float,
        budget_tokens: int,
    ) -> None:
        self.manager = manager
        self.server_name = server_name
        self.timeout_seconds = timeout_seconds
        self.budget_tokens = budget_tokens
        project_name = Path(project_root).name.lower()
        safe_name = re.sub(r"[^a-z0-9-]+", "-", project_name).strip("-") or "project"
        self.project_entity = f"projects/{safe_name}"
        self._warmed_sessions: set[str] = set()
        self.outbox_path = Path(project_root) / ".lumo" / "memory" / "gbrain-outbox.jsonl"

    @property
    def configured(self) -> bool:
        return self.manager is not None

    def prompt(self, *, hybrid: bool) -> str:
        mode_note = (
            "Markdown memory remains the home for compact standing user/feedback rules. "
            "GBrain is the source of truth for searchable project facts, decisions, events, "
            "entities, and cross-source knowledge. Do not duplicate the same fact in both."
            if hybrid
            else "GBrain is the source of truth for durable memory in this runtime."
        )
        prefix = f"mcp_{self.server_name}_"
        return (
            "# GBrain compiled long-term memory\n\n"
            f"{mode_note}\n\n"
            "Relevant GBrain context is recalled before the first model inference. "
            "For additional memory work, use the MEMORY_VERBS v1 tools:\n"
            f"- `{prefix}recall` for saved facts and evidence\n"
            f"- `{prefix}entity` for a person/project/company card\n"
            f"- `{prefix}remember` only when the user explicitly asks to remember something\n"
            f"- `{prefix}forget` to retire a fact by id\n"
            f"- `{prefix}synthesize` only for cross-page reasoning; it may be slow or paid\n\n"
            f"Use `{self.project_entity}` for facts about this project. Every remembered fact "
            "must include concrete provenance. To correct a fact, recall it, forget the old id, "
            "remember the replacement, and verify the readback. Never claim a failed write was "
            "saved. A GBrain outage means memory is unavailable, not that no prior memory exists."
        )

    async def recall(self, query: str, *, session_id: str = "") -> GBrainRecall:
        if self.manager is None:
            return self._unavailable("the configured MCP manager is unavailable")

        async def operation() -> str:
            parts: list[str] = []
            await self.flush_outbox()

            warm_key = session_id or "default"
            if warm_key not in self._warmed_sessions:
                payload = await self._call_json(
                    "context_pack",
                    {
                        "entities": f"{self.project_entity},people/me",
                        "budget_tokens": self.budget_tokens,
                        **({"session_id": session_id} if session_id else {}),
                    },
                )
                warm = payload.get("text")
                if isinstance(warm, str) and warm.strip():
                    parts.append(warm.strip())
                self._warmed_sessions.add(warm_key)

            payload = await self._call_json(
                "recall",
                {
                    "query": query,
                    "entity": self.project_entity,
                    "budget_tokens": self.budget_tokens,
                    "limit": 10,
                    **({"session_id": session_id} if session_id else {}),
                },
            )
            recalled = _render_recall(payload)
            if recalled:
                parts.append(recalled)
            return "\n\n".join(parts)

        try:
            text = await asyncio.wait_for(operation(), timeout=self.timeout_seconds)
            return GBrainRecall(text=text)
        except Exception as exc:
            log.warning("GBrain recall degraded: %s", exc)
            return self._unavailable(str(exc))

    async def rehydrate(self, *, session_id: str = "") -> GBrainRecall:
        if self.manager is None:
            return self._unavailable("the configured MCP manager is unavailable")
        try:
            payload = await asyncio.wait_for(
                self._call_json(
                    "context_pack",
                    {
                        "entities": f"{self.project_entity},people/me",
                        "budget_tokens": self.budget_tokens,
                        **({"session_id": session_id} if session_id else {}),
                    },
                ),
                timeout=self.timeout_seconds,
            )
            text = payload.get("text")
            return GBrainRecall(text=text.strip() if isinstance(text, str) else "")
        except Exception as exc:
            log.warning("GBrain rehydration degraded: %s", exc)
            return self._unavailable(str(exc))

    def queue_remember(self, arguments: dict[str, Any], error: str) -> None:
        """Persist an explicitly-authorized remember call after a transport failure."""
        if not isinstance(arguments.get("fact"), str) or not arguments["fact"].strip():
            return
        if not isinstance(arguments.get("provenance"), str) or not arguments["provenance"].strip():
            return
        self.outbox_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "tool": "remember",
            "arguments": arguments,
            "queued_at": datetime.now(timezone.utc).isoformat(),
            "last_error": error[:1000],
        }
        with self.outbox_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    async def flush_outbox(self) -> int:
        """Retry queued transport failures and retain entries that still fail."""
        if not self.outbox_path.is_file() or self.manager is None:
            return 0
        try:
            rows = [
                json.loads(line)
                for line in self.outbox_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except (OSError, json.JSONDecodeError):
            return 0

        retained: list[dict[str, Any]] = []
        flushed = 0
        for row in rows:
            try:
                await self._call_json("remember", dict(row.get("arguments") or {}))
                flushed += 1
            except Exception as exc:
                row["last_error"] = str(exc)[:1000]
                retained.append(row)

        try:
            if retained:
                temp = self.outbox_path.with_suffix(".tmp")
                temp.write_text(
                    "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in retained),
                    encoding="utf-8",
                )
                temp.replace(self.outbox_path)
            else:
                self.outbox_path.unlink(missing_ok=True)
        except OSError:
            pass
        return flushed

    async def _call_json(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        assert self.manager is not None
        client = await self.manager.get_client(self.server_name)
        if client is None:
            raise GBrainUnavailable(f"MCP server '{self.server_name}' is not configured")
        result = await client.call_tool(tool, arguments)
        text_parts = [
            block.text for block in result.content if isinstance(getattr(block, "text", None), str)
        ]
        raw = "\n".join(text_parts).strip()
        if bool(result.isError):
            raise GBrainUnavailable(raw or f"GBrain {tool} returned an error")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GBrainUnavailable(f"GBrain {tool} returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise GBrainUnavailable(f"GBrain {tool} returned a non-object response")
        if payload.get("error"):
            raise GBrainUnavailable(str(payload.get("message") or payload["error"]))
        return payload

    def _unavailable(self, detail: str) -> GBrainRecall:
        warning = (
            "GBrain long-term memory was unavailable for this turn. Continue with the current "
            "conversation and any Markdown memory, but do not infer that prior memory is empty."
        )
        return GBrainRecall(available=False, warning=warning + f" Detail: {detail[:300]}")


def _render_recall(payload: dict[str, Any]) -> str:
    facts = payload.get("facts") if isinstance(payload.get("facts"), list) else []
    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    if not facts and not results:
        return ""

    lines = ["# GBrain recalled memory", ""]
    for item in facts:
        if not isinstance(item, dict) or not item.get("fact"):
            continue
        provenance = item.get("provenance") or item.get("source") or "unknown provenance"
        fact_id = item.get("fact_id") or item.get("id")
        suffix = f" [fact_id={fact_id}; provenance={provenance}]" if fact_id else f" [provenance={provenance}]"
        lines.append(f"- {item['fact']}{suffix}")
    for item in results:
        if not isinstance(item, dict) or not item.get("chunk"):
            continue
        title = item.get("title") or item.get("slug") or "memory evidence"
        provenance = item.get("provenance") or item.get("slug") or "unknown provenance"
        lines.append(f"- {title}: {item['chunk']} [provenance={provenance}]")
    return "\n".join(lines).strip()
