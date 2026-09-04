from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static

from lumo.scenario_designer.models import ScenarioDraft


class ScenarioDraftReviewWidget(Vertical, can_focus=True):
    BINDINGS = [
        Binding("up", "cursor_up", "Up", priority=True),
        Binding("down", "cursor_down", "Down", priority=True),
        Binding("enter", "select", "Select", priority=True),
        Binding("escape", "cancel", "Cancel", priority=True),
    ]

    class Responded(Message):
        def __init__(self, action: str) -> None:
            super().__init__()
            self.action = action

    def __init__(self, draft: ScenarioDraft, **kwargs) -> None:
        super().__init__(id="scenario-draft-review", **kwargs)
        self.draft = draft
        self._has_errors = any(
            item.severity == "error" for item in draft.diagnostics
        )
        self._options = (
            ["Save draft only", "Cancel"]
            if self._has_errors
            else ["Save and start", "Save only", "Cancel"]
        )
        self._cursor = 0

    def compose(self) -> ComposeResult:
        yield Static(self._build_content(), id="scenario-draft-content")

    def on_mount(self) -> None:
        self.focus()

    def _build_content(self) -> str:
        definition = self.draft.definition
        experience = definition.experience
        lines = [
            "",
            "  [bold]Review Scenario Proposal[/bold]",
            "  [dim]────────────────────────────────────────[/dim]",
            "",
            f"  [bold][green]{definition.name}[/green][/bold]  [dim]({definition.id})[/dim]",
            f"  Role: {experience.role or 'general assistant'}",
            f"  Mission: {experience.mission or definition.description}",
            "",
            "  [bold]Capabilities[/bold]",
        ]
        if self.draft.matched_capabilities:
            for item in self.draft.matched_capabilities:
                lines.append(
                    f"    [green]✓[/green] {item.title} "
                    f"[dim]({item.kind.value}, {item.risk})[/dim]"
                )
        else:
            lines.append("    [dim]No matched capabilities[/dim]")
        for capability_id in self.draft.missing_capability_ids:
            lines.append(f"    [red]✗[/red] {capability_id} [dim]missing[/dim]")

        if self.draft.diagnostics:
            lines.extend(["", "  [bold]Diagnostics[/bold]"])
            for item in self.draft.diagnostics:
                color = "red" if item.severity == "error" else "yellow"
                lines.append(
                    f"    [{color}]{item.severity.upper()}[/{color}] "
                    f"{item.message}"
                )

        prompt_preview = self.draft.prompt_text.strip().replace("\n", " ")
        if len(prompt_preview) > 180:
            prompt_preview = prompt_preview[:177] + "..."
        lines.extend([
            "",
            "  [bold]Prompt preview[/bold]",
            f"    [dim]{prompt_preview or '(empty)'}[/dim]",
            "",
        ])
        for index, option in enumerate(self._options):
            if index == self._cursor:
                lines.append(f"  [bold][cyan]❯[/cyan][/bold] [bold]{option}[/bold]")
            else:
                lines.append(f"    [dim]{option}[/dim]")
        lines.extend(["", "  [dim]Enter select · Esc cancel[/dim]"])
        return "\n".join(lines)

    def _refresh(self) -> None:
        self.query_one("#scenario-draft-content", Static).update(
            self._build_content()
        )

    def action_cursor_up(self) -> None:
        if self._cursor > 0:
            self._cursor -= 1
            self._refresh()

    def action_cursor_down(self) -> None:
        if self._cursor < len(self._options) - 1:
            self._cursor += 1
            self._refresh()

    def action_select(self) -> None:
        labels = {
            "Save and start": "save-start",
            "Save only": "save",
            "Save draft only": "save",
            "Cancel": "cancel",
        }
        self.post_message(self.Responded(labels[self._options[self._cursor]]))

    def action_cancel(self) -> None:
        self.post_message(self.Responded("cancel"))
