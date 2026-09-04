from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static

from lumo.runtime.models import RuntimeSpec, ScenarioRecord


class InlineScenarioWidget(Vertical, can_focus=True):
    BINDINGS = [
        Binding("up", "cursor_up", "Up", priority=True),
        Binding("down", "cursor_down", "Down", priority=True),
        Binding("enter", "select", "Use", priority=True),
        Binding("escape", "cancel", "Cancel", priority=True),
    ]

    class Selected(Message):
        def __init__(self, scenario_id: str | None) -> None:
            super().__init__()
            self.scenario_id = scenario_id

    def __init__(self, records: list[ScenarioRecord], current_id: str, **kwargs) -> None:
        super().__init__(id="scenario-inline", **kwargs)
        self._records = records
        self._filtered = list(records)
        self._current_id = current_id
        self._cursor = next(
            (i for i, item in enumerate(self._filtered) if item.id == current_id),
            0,
        )
        self._search = ""

    def compose(self) -> ComposeResult:
        yield Static(self._build_content(), id="scenario-content")

    def on_mount(self) -> None:
        self.focus()

    def _build_content(self) -> str:
        lines = [f"[dim]Scenarios ({len(self._filtered)} of {len(self._records)})[/]\n"]
        if self._search:
            lines.append(f"  ⌕ {self._search}\n")
        else:
            lines.append("  [dim]⌕ Type to search…[/]\n")
        for index, record in enumerate(self._filtered[:10]):
            marker = "[bold cyan]❯[/]" if index == self._cursor else " "
            current = " [green]current[/]" if record.id == self._current_id else ""
            if record.healthy and record.definition is not None:
                title = record.definition.name
                summary = (
                    f"{len(record.definition.packs)} packs · "
                    f"{len(record.definition.mcp)} MCP · "
                    f"{len(record.definition.cli)} CLI · "
                    f"{len(record.definition.plugins)} plugins"
                )
                lines.append(f"{marker} [bold]{title}[/] [dim]({record.id}, {record.source})[/]{current}")
                lines.append(f"    [dim]{summary}[/]")
            else:
                lines.append(f"{marker} [red]{record.id} · broken[/]")
                lines.append(f"    [red]{record.error}[/]")
            lines.append("")
        lines.append("[dim]Enter use in a new runtime · Esc cancel[/]")
        return "\n".join(lines)

    def _refresh(self) -> None:
        self.query_one("#scenario-content", Static).update(self._build_content())

    def _refilter(self) -> None:
        query = self._search.lower()
        self._filtered = [
            item for item in self._records
            if not query
            or query in item.id.lower()
            or (
                item.definition is not None
                and query in item.definition.name.lower()
            )
        ]
        self._cursor = 0
        self._refresh()

    def action_cursor_up(self) -> None:
        if self._cursor > 0:
            self._cursor -= 1
            self._refresh()

    def action_cursor_down(self) -> None:
        if self._cursor < min(len(self._filtered), 10) - 1:
            self._cursor += 1
            self._refresh()

    def action_select(self) -> None:
        if not self._filtered:
            return
        record = self._filtered[self._cursor]
        if record.healthy:
            self.post_message(self.Selected(record.id))

    def action_cancel(self) -> None:
        self.post_message(self.Selected(None))

    def on_key(self, event) -> None:
        if event.key == "backspace":
            if self._search:
                self._search = self._search[:-1]
                self._refilter()
            event.stop()
        elif len(event.key) == 1 and event.key.isprintable():
            self._search += event.key
            self._refilter()
            event.stop()


class InlineScenarioSwitchWidget(Vertical, can_focus=True):
    """Confirm a runtime switch with an explicit capability comparison."""

    BINDINGS = [
        Binding("up", "cursor_up", "Up", priority=True),
        Binding("down", "cursor_down", "Down", priority=True),
        Binding("enter", "select", "Select", priority=True),
        Binding("escape", "cancel", "Cancel", priority=True),
    ]

    class Responded(Message):
        def __init__(self, confirmed: bool, scenario_id: str) -> None:
            super().__init__()
            self.confirmed = confirmed
            self.scenario_id = scenario_id

    def __init__(
        self,
        current: RuntimeSpec,
        target: RuntimeSpec,
        **kwargs,
    ) -> None:
        super().__init__(id="scenario-switch-inline", **kwargs)
        self.current = current
        self.target = target
        self._cursor = 0

    def compose(self) -> ComposeResult:
        yield Static(self._build_content(), id="scenario-switch-content")

    def on_mount(self) -> None:
        self.focus()

    @staticmethod
    def _summary(spec: RuntimeSpec) -> str:
        scenario = spec.scenario
        external = (
            len(spec.mcp_servers)
            + len(spec.cli_definitions)
            + len(spec.plugin_descriptors)
        )
        return (
            f"{len(scenario.packs)} packs  ·  "
            f"{external} external  ·  "
            f"{'memory' if scenario.features.memory else 'no memory'}"
        )

    def _build_content(self) -> str:
        current = self.current.scenario
        target = self.target.scenario
        options = [
            "Switch runtime and start a new session",
            "Keep current runtime",
        ]
        lines = [
            "",
            "  [bold]Switch Agent Runtime[/bold]",
            "  [dim]────────────────────────────────────────[/dim]",
            "",
            f"  [dim]FROM[/dim]  [bold]{current.name}[/bold]  [dim]({current.id})[/dim]",
            f"        [dim]{self._summary(self.current)}[/dim]",
            "",
            f"  [dim]TO[/dim]    [bold][green]{target.name}[/green][/bold]  [dim]({target.id})[/dim]",
            f"        [dim]{self._summary(self.target)}[/dim]",
            "",
            "  [yellow]A new session will be created.[/yellow]",
            "  [dim]The current session remains saved under its existing runtime.[/dim]",
            "",
        ]
        for index, option in enumerate(options):
            if index == self._cursor:
                lines.append(f"  [bold][cyan]❯[/cyan][/bold] [bold]{option}[/bold]")
            else:
                lines.append(f"    [dim]{option}[/dim]")
        lines.extend(["", "  [dim]Enter select · Esc cancel[/dim]"])
        return "\n".join(lines)

    def _refresh(self) -> None:
        self.query_one("#scenario-switch-content", Static).update(
            self._build_content()
        )

    def action_cursor_up(self) -> None:
        if self._cursor > 0:
            self._cursor -= 1
            self._refresh()

    def action_cursor_down(self) -> None:
        if self._cursor < 1:
            self._cursor += 1
            self._refresh()

    def action_select(self) -> None:
        self.post_message(self.Responded(
            confirmed=self._cursor == 0,
            scenario_id=self.target.scenario.id,
        ))

    def action_cancel(self) -> None:
        self.post_message(self.Responded(
            confirmed=False,
            scenario_id=self.target.scenario.id,
        ))
