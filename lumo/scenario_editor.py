from __future__ import annotations

from dataclasses import replace

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static

from lumo.runtime.models import (
    CapabilityDescriptor,
    CapabilityKind,
    CapabilityRef,
    ScenarioDefinition,
    SkillSelection,
)

_KINDS = [
    CapabilityKind.PACK,
    CapabilityKind.MCP,
    CapabilityKind.CLI,
    CapabilityKind.PLUGIN,
    CapabilityKind.SKILL,
]


class ScenarioEditorWidget(Vertical, can_focus=True):
    BINDINGS = [
        Binding("up", "cursor_up", "Up", priority=True),
        Binding("down", "cursor_down", "Down", priority=True),
        Binding("space", "toggle", "Toggle", priority=True),
        Binding("tab", "next_kind", "Next category", priority=True),
        Binding("shift+tab", "previous_kind", "Previous category", priority=True),
        Binding("ctrl+s", "save", "Save", priority=True),
        Binding("enter", "toggle", "Toggle", priority=True),
        Binding("escape", "cancel", "Cancel", priority=True),
    ]

    class Saved(Message):
        def __init__(self, definition: ScenarioDefinition | None) -> None:
            super().__init__()
            self.definition = definition

    def __init__(
        self,
        definition: ScenarioDefinition,
        capabilities: list[CapabilityDescriptor],
        **kwargs,
    ) -> None:
        super().__init__(id="scenario-editor-inline", **kwargs)
        self._definition = definition
        self._capabilities = capabilities
        self._kind_index = 0
        self._cursor = 0
        self._selected: dict[CapabilityKind, set[str]] = {
            CapabilityKind.PACK: set(definition.packs),
            CapabilityKind.MCP: (
                {
                    item.id
                    for item in capabilities
                    if item.kind == CapabilityKind.MCP
                }
                if definition.use_all_configured_mcp
                else {item.id for item in definition.mcp}
            ),
            CapabilityKind.CLI: {item.id for item in definition.cli},
            CapabilityKind.PLUGIN: {item.id for item in definition.plugins},
            CapabilityKind.SKILL: (
                {
                    item.id
                    for item in capabilities
                    if item.kind == CapabilityKind.SKILL
                }
                if definition.skills.include == ("*",)
                else set(definition.skills.include)
            ),
        }

    def compose(self) -> ComposeResult:
        yield Static(self._build_content(), id="scenario-editor-content")

    def on_mount(self) -> None:
        self.focus()

    @property
    def _kind(self) -> CapabilityKind:
        return _KINDS[self._kind_index]

    def _items(self) -> list[CapabilityDescriptor]:
        return [item for item in self._capabilities if item.kind == self._kind]

    def _build_content(self) -> str:
        tabs = []
        for kind in _KINDS:
            label = kind.value.upper()
            tabs.append(
                f"[bold reverse] {label} [/]"
                if kind == self._kind
                else f" {label} "
            )
        lines = [
            f"[bold #9B8AFB]Edit scenario: {self._definition.name}[/]",
            " | ".join(tabs),
            "",
        ]
        items = self._items()
        if not items:
            lines.append("  [dim]No capabilities discovered in this category.[/]")
        for index, item in enumerate(items[:14]):
            cursor = "[bold cyan]❯[/]" if index == self._cursor else " "
            selected = item.id in self._selected[self._kind]
            check = "[green]●[/]" if selected else "○"
            state = "" if item.available else " [red]unavailable[/]"
            lines.append(f"{cursor} {check} [bold]{item.title}[/]{state}")
            lines.append(
                f"    [dim]{item.id} · {item.risk} · {item.description}[/]"
            )
            if item.unavailable_reason:
                lines.append(f"    [red]{item.unavailable_reason}[/]")
        lines.extend([
            "",
            "[dim]Tab category · Space toggle · Ctrl+S save · Esc cancel[/]",
        ])
        return "\n".join(lines)

    def _refresh(self) -> None:
        self.query_one("#scenario-editor-content", Static).update(
            self._build_content()
        )

    def action_cursor_up(self) -> None:
        if self._cursor > 0:
            self._cursor -= 1
            self._refresh()

    def action_cursor_down(self) -> None:
        if self._cursor < min(len(self._items()), 14) - 1:
            self._cursor += 1
            self._refresh()

    def action_next_kind(self) -> None:
        self._kind_index = (self._kind_index + 1) % len(_KINDS)
        self._cursor = 0
        self._refresh()

    def action_previous_kind(self) -> None:
        self._kind_index = (self._kind_index - 1) % len(_KINDS)
        self._cursor = 0
        self._refresh()

    def action_toggle(self) -> None:
        items = self._items()
        if not items:
            return
        item = items[self._cursor]
        if not item.available:
            return
        selected = self._selected[self._kind]
        if item.id in selected:
            selected.remove(item.id)
        else:
            selected.add(item.id)
        self._refresh()

    def action_save(self) -> None:
        def refs(kind: CapabilityKind) -> tuple[CapabilityRef, ...]:
            existing_items = {
                CapabilityKind.MCP: self._definition.mcp,
                CapabilityKind.CLI: self._definition.cli,
                CapabilityKind.PLUGIN: self._definition.plugins,
            }[kind]
            existing = {item.id: item for item in existing_items}
            return tuple(
                existing.get(item_id, CapabilityRef(id=item_id))
                for item_id in sorted(self._selected[kind])
            )

        skill_ids = tuple(sorted(self._selected[CapabilityKind.SKILL]))
        definition = replace(
            self._definition,
            packs=tuple(sorted(self._selected[CapabilityKind.PACK])),
            mcp=refs(CapabilityKind.MCP),
            cli=refs(CapabilityKind.CLI),
            plugins=refs(CapabilityKind.PLUGIN),
            skills=SkillSelection(
                include=skill_ids,
                exclude=self._definition.skills.exclude,
            ),
            use_all_configured_mcp=False,
        )
        self.post_message(self.Saved(definition))

    def action_cancel(self) -> None:
        self.post_message(self.Saved(None))
