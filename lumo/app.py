# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com
from __future__ import annotations

import asyncio
import os
import random
import time as _time
from pathlib import Path
from typing import Any

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message as TMessage
from textual.widgets import Markdown, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from lumo.agent import (
    Agent,
    CompactNotification,
    ErrorEvent,
    HookEvent,
    LoopComplete,
    PermissionRequest,
    PermissionResponse,
    RetryEvent,
    StreamText,
    ThinkingText,
    ToolResultEvent,
    ToolUseEvent,
    TurnComplete,
    UsageEvent,
)
from lumo.client import (
    AuthenticationError,
    LLMClient,
    LLMError,
    create_client,
    resolve_context_window,
)
from lumo.commands import (
    CommandContext,
    CommandRegistry,
    complete,
    parse_command,
)
from lumo.commands.completion import CompletionPopup
from lumo.commands.handlers import register_all_commands
from lumo.config import AppConfig, MCPServerConfig, ProviderConfig
from lumo.hooks import HookContext, HookEngine, load_hooks
from lumo.conversation import ConversationManager, Message
from lumo.mcp import ConnectResult, MCPManager
from lumo.memory import (
    MemoryManager,
    Session,
    SessionManager,
    find_relevant_memories,
    generate_session_summary,
    load_instructions,
    make_compact_boundary,
    render_reminder,
)
from lumo.permissions import (
    DangerousCommandDetector,
    PathSandbox,
    PermissionChecker,
    PermissionMode,
    RuleEngine,
)
from lumo.agents.loader import AgentLoader
from lumo.agents.task_manager import TaskManager
from lumo.agents.trace import TraceManager
from lumo.agents.notification import inject_task_notifications
from lumo.commands.handlers.tasks import create_tasks_command
from lumo.skills.executor import SkillExecutor
from lumo.skills.loader import SkillLoader
from lumo.commands.handlers.skill_register import register_skill_commands
from rich.text import Text as RichText
from textual.theme import Theme
from lumo.tools import ToolRegistry, create_default_registry
from lumo.tools.agent_tool import AgentTool
from lumo.tools.ask_user import AskUserEvent, AskUserTool
from lumo.tools.impl.tool_search import ToolSearchTool
from lumo.tools.install_skill import InstallSkillTool
from lumo.tools.load_skill import LoadSkill
from lumo.worktree.cleanup import start_stale_cleanup_task
from lumo.worktree.manager import WorktreeManager
from lumo.commands.handlers.worktree import create_worktree_command
from lumo.teammate_tree import TeammateTree

import re

MAX_TRUNCATED_LINES = 20
MAX_AT_REF_BYTES = 10240

_AT_REF_RE = re.compile(r"@([\w./_\-]+(?:\.[\w]+)*)")

_SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "__pycache__",
    ".lumo",
    ".mewcode",
    "build",
    ".gradle",
}


def scan_files_for_at(prefix: str, work_dir: str, limit: int = 10) -> list[str]:
    matches: list[str] = []
    base = os.path.join(work_dir, os.path.dirname(prefix)) if "/" in prefix else work_dir
    name_prefix = os.path.basename(prefix).lower()
    if not os.path.isdir(base):
        return matches
    try:
        for entry in sorted(os.listdir(base)):
            if entry in _SKIP_DIRS or entry.startswith("."):
                continue
            if entry.lower().startswith(name_prefix):
                rel = os.path.join(os.path.dirname(prefix), entry) if "/" in prefix else entry
                if os.path.isdir(os.path.join(base, entry)):
                    rel += "/"
                matches.append(rel)
                if len(matches) >= limit:
                    break
    except OSError:
        pass
    return matches


def expand_at_refs(text: str, work_dir: str) -> str:
    def _replace(m: re.Match) -> str:
        rel_path = m.group(1)
        full_path = os.path.join(work_dir, rel_path)
        if not os.path.isfile(full_path):
            return m.group(0)
        try:
            content = open(full_path, encoding="utf-8", errors="replace").read(MAX_AT_REF_BYTES)
            return f"[File: {rel_path}]\n```\n{content}\n```"
        except Exception:
            return m.group(0)
    return _AT_REF_RE.sub(_replace, text)


class ChatInput(TextArea):
    BINDINGS = [
        Binding("enter", "submit", "Submit", priority=True),
        Binding("shift+enter", "newline", "Newline", priority=True),
        Binding("ctrl+j", "newline", "Newline", priority=True),
        Binding("tab", "complete", "Complete", priority=True),
        Binding("shift+tab", "cycle_mode", "Cycle mode", priority=True),
        Binding("escape", "dismiss_popup", "Dismiss", priority=True),
        Binding("up", "nav_up", "Navigate up", priority=True),
        Binding("down", "nav_down", "Navigate down", priority=True),
    ]

    class Submitted(TMessage):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class TabComplete(TMessage):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.cursor_blink = False
        self._history: list[str] = []
        self._history_index: int = -1
        self._history_draft: str = ""
        self._history_file: Path | None = None

    def load_history(self, work_dir: str) -> None:
        self._history_file = Path(work_dir) / ".lumo" / "history"
        if self._history_file.exists():
            try:
                lines = self._history_file.read_text(encoding="utf-8").splitlines()
                self._history = [l for l in lines if l.strip()]
            except Exception:
                pass

    def _persist_entry(self, text: str) -> None:
        if self._history_file is None:
            return
        try:
            self._history_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._history_file, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except Exception:
            pass

    def _popup(self) -> CompletionPopup | None:
        try:
            return self.app.query_one(CompletionPopup)
        except Exception:
            return None

    def action_submit(self) -> None:
        popup = self._popup()
        if popup is not None and popup.is_visible:
            selected = popup.get_selected()
            popup.hide()
            if selected:
                self._history.append(selected)
                self._persist_entry(selected)
                self._history_index = -1
                self._history_draft = ""
                self.post_message(self.Submitted(selected))
                self.clear()
                return
        text = self.text.strip()
        if text:
            self._history.append(text)
            self._persist_entry(text)
            self._history_index = -1
            self._history_draft = ""
            self.post_message(self.Submitted(text))
            self.clear()

    def action_newline(self) -> None:
        self.insert("\n")

    def action_complete(self) -> None:
        popup = self._popup()
        if popup is not None and popup.is_visible:
            selected = popup.get_selected()
            if selected:
                popup.hide()
                self.clear()
                self.insert(selected + " ")
            return
        text = self.text.strip()
        if text.startswith("/"):
            self.post_message(self.TabComplete(text))
        else:
            self.insert("\t")

    def action_cycle_mode(self) -> None:
        action = getattr(self.app, "action_cycle_mode", None)
        if action is not None:
            action()

    def action_dismiss_popup(self) -> None:
        popup = self._popup()
        if popup is not None:
            popup.hide()

    def action_nav_up(self) -> None:
        popup = self._popup()
        if popup is not None and popup.is_visible:
            popup.move_up()
            return
        if not self._history:
            return
        if self._history_index == -1:
            self._history_draft = self.text
            self._history_index = len(self._history) - 1
        elif self._history_index > 0:
            self._history_index -= 1
        else:
            return
        self.clear()
        self.insert(self._history[self._history_index])

    def action_nav_down(self) -> None:
        popup = self._popup()
        if popup is not None and popup.is_visible:
            popup.move_down()
            return
        if self._history_index == -1:
            return
        if self._history_index < len(self._history) - 1:
            self._history_index += 1
            self.clear()
            self.insert(self._history[self._history_index])
        else:
            self._history_index = -1
            self.clear()
            self.insert(self._history_draft)

    class AtFileRequest(TMessage):
        def __init__(self, prefix: str) -> None:
            super().__init__()
            self.prefix = prefix

    class SlashMenuUpdate(TMessage):
        def __init__(self, prefix: str | None) -> None:
            super().__init__()
            self.prefix = prefix

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        text = self.text
        if text.startswith("/") and self._history_index < 0:
            prefix = text[1:]
            if " " not in prefix and "\n" not in prefix:
                self.post_message(self.SlashMenuUpdate(prefix))
            else:
                self.post_message(self.SlashMenuUpdate(None))
        else:
            self.post_message(self.SlashMenuUpdate(None))

        at_idx = text.rfind("@")
        if at_idx < 0:
            return
        after = text[at_idx + 1:]
        if " " in after or "\n" in after:
            return
        if after:
            self.post_message(self.AtFileRequest(after))


COLLAPSIBLE_TOOLS = {"ReadFile", "Glob", "Grep", "ToolSearch"}


def _is_subagent_tool(tool_name: str) -> bool:
    return tool_name == "Agent"


def _tool_title(tool_name: str, arguments: dict[str, Any]) -> str:
    if tool_name == "ReadFile":
        path = os.path.basename(arguments.get("file_path", ""))
        return f"Read {path}" if path else "Read"
    if tool_name == "WriteFile":
        path = os.path.basename(arguments.get("file_path", ""))
        content = arguments.get("content", "")
        lines = content.count("\n") + 1 if content else 0
        return f"Write {path} ({lines} lines)" if path else "Write"
    if tool_name == "EditFile":
        path = os.path.basename(arguments.get("file_path", ""))
        return f"Edit {path}" if path else "Edit"
    if tool_name == "Bash":
        cmd = arguments.get("command", "")
        short = cmd[:50] + "…" if len(cmd) > 50 else cmd
        return f"Bash: {short}" if short else "Bash"
    if tool_name == "Glob":
        return f"Glob: {arguments.get('pattern', '')}"
    if tool_name == "Grep":
        return f"Grep: {arguments.get('pattern', '')}"
    return tool_name


def _format_detail(tool_name: str, arguments: dict[str, Any], output: str) -> str:
    parts: list[str] = []

    if tool_name == "Bash":
        parts.append(f"  IN   {arguments.get('command', '')}")
        parts.append("")
        for line in output.splitlines():
            parts.append(f"  OUT  {line}")
    elif tool_name == "EditFile":
        # EditFile 的 output 是 build_diff() 生成的带行号 diff 文本：
        # "+ " 开头绿色、"- " 开头红色，其余（上下文行/摘要行）走 dim。
        # 转义 Rich markup 特殊字符，避免代码里的方括号被当成标签解析。
        for line in output.splitlines()[:MAX_TRUNCATED_LINES]:
            escaped = escape(line)
            if line.startswith("+ "):
                parts.append(f"  [green]{escaped}[/]")
            elif line.startswith("- "):
                parts.append(f"  [red]{escaped}[/]")
            else:
                parts.append(f"  [dim]{escaped}[/]")
        total = output.count("\n") + 1
        if total > MAX_TRUNCATED_LINES:
            parts.append(f"  [dim]… ({total - MAX_TRUNCATED_LINES} more lines)[/]")
    elif tool_name in ("ReadFile", "WriteFile"):
        parts.append(f"  {arguments.get('file_path', '')}")
        parts.append("")
        for line in output.splitlines()[:MAX_TRUNCATED_LINES]:
            parts.append(f"  {line}")
        total = output.count("\n") + 1
        if total > MAX_TRUNCATED_LINES:
            parts.append(f"  … ({total - MAX_TRUNCATED_LINES} more lines)")
    else:
        for line in output.splitlines()[:MAX_TRUNCATED_LINES]:
            parts.append(f"  {line}")
        total = output.count("\n") + 1
        if total > MAX_TRUNCATED_LINES:
            parts.append(f"  … ({total - MAX_TRUNCATED_LINES} more lines)")

    return "\n".join(parts)


class ToolCallBlock(Static, can_focus=True):

    def __init__(self, tool_name: str, arguments: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.tool_name = tool_name
        self._arguments = arguments
        self._title = _tool_title(tool_name, arguments)
        self._full_output = ""
        self._is_error = False
        self._elapsed = 0.0
        self._collapsed = True
        self._loading = True
        self._render_loading()

    def _render_loading(self) -> None:
        self.update(f"  ● {self._title} …")
        self.add_class("tool-block-loading")

    def set_result(self, output: str, is_error: bool, elapsed: float) -> None:
        self._full_output = output
        self._is_error = is_error
        self._elapsed = elapsed
        self._loading = False
        self.remove_class("tool-block-loading")
        if is_error:
            self.add_class("tool-block-error")
        # EditFile 的 diff 是最高频需要的信息，默认直接展开，不用等用户点
        # 或按 ctrl+o；其余工具仍然默认折叠，避免刷屏。
        if self.tool_name == "EditFile" and not is_error:
            self._collapsed = False
            self._render_expanded()
        else:
            self._collapsed = True
            self._render_collapsed()

    def _render_collapsed(self) -> None:
        if self._is_error:
            self.update(f"  ✗ {self._title} ({self._elapsed:.1f}s)")
        else:
            self.update(f"  ✓ {self._title} ({self._elapsed:.1f}s)")

    def _render_expanded(self) -> None:
        if self._is_error:
            header = f"  ✗ {self._title} ({self._elapsed:.1f}s)"
        else:
            header = f"  ✓ {self._title} ({self._elapsed:.1f}s)"
        detail = _format_detail(self.tool_name, self._arguments, self._full_output)
        self.update(f"{header}\n{detail}")

    def on_click(self) -> None:
        if self._loading:
            return
        self._collapsed = not self._collapsed
        if self._collapsed:
            self._render_collapsed()
        else:
            self._render_expanded()


_MODE_CYCLE = [
    PermissionMode.DEFAULT,
    PermissionMode.ACCEPT_EDITS,
    PermissionMode.PLAN,
    PermissionMode.BYPASS,
]

_MODE_COLORS = {
    PermissionMode.DEFAULT: "dim",
    PermissionMode.ACCEPT_EDITS: "green",
    PermissionMode.PLAN: "yellow",
    PermissionMode.BYPASS: "red",
}

SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _to_past_tense(verb: str) -> str:
    """把现在进行时动词转换为过去式。"""
    if verb.endswith("ing"):
        stem = verb[:-3]
        if stem.endswith("e"):
            return stem + "d"
        if stem and stem[-1] in "atutitet":
            return stem + "ed"
        return stem + "ed"
    return verb + "ed"


THINKING_VERBS = [
    "Accomplishing", "Architecting", "Baking", "Beboppin'", "Befuddling",
    "Bloviating", "Boogieing", "Boondoggling", "Bootstrapping", "Brewing",
    "Calculating", "Canoodling", "Caramelizing", "Cascading", "Cerebrating",
    "Choreographing", "Churning", "Coalescing", "Cogitating", "Combobulating",
    "Composing", "Computing", "Concocting", "Considering", "Contemplating",
    "Cooking", "Crafting", "Creating", "Crunching", "Crystallizing",
    "Cultivating", "Deciphering", "Deliberating", "Dilly-dallying",
    "Discombobulating", "Doodling", "Elucidating", "Enchanting", "Envisioning",
    "Fermenting", "Finagling", "Flambéing", "Flibbertigibbeting", "Flummoxing",
    "Forging", "Frolicking", "Gallivanting", "Garnishing", "Generating",
    "Germinating", "Grooving", "Harmonizing", "Hatching", "Honking",
    "Hullaballooing", "Ideating", "Imagining", "Improvising", "Incubating",
    "Inferring", "Infusing", "Kneading", "Lollygagging", "Manifesting",
    "Marinating", "Meandering", "Metamorphosing", "Mewing", "Moonwalking",
    "Moseying", "Mulling", "Musing", "Noodling", "Orbiting",
    "Orchestrating", "Percolating", "Philosophising", "Pondering",
    "Pontificating", "Pouncing", "Purring", "Puzzling", "Razzle-dazzling",
    "Ruminating", "Scampering", "Simmering", "Sketching", "Spelunking",
    "Spinning", "Sprouting", "Synthesizing", "Thinking", "Tinkering",
    "Transfiguring", "Transmuting", "Undulating", "Unfurling", "Unravelling",
    "Vibing", "Wandering", "Whisking", "Working", "Wrangling", "Zigzagging",
]  # 共 105 个动词，与 Go 版 internal/tui/verbs.go 完全一致


class ToolGroupSummary(Static, can_focus=True):


    def __init__(self, count: int, total_elapsed: float, **kwargs: Any) -> None:
        label = f"● Done ({count} tool uses · {total_elapsed:.1f}s)  (ctrl+o to expand)"
        super().__init__(label, **kwargs)
        self._count = count
        self._total = total_elapsed
        self._expanded = False

    def _refresh_display(self) -> None:
        if self._expanded:
            self.update(f"▼ Done ({self._count} tool uses · {self._total:.1f}s)")
        else:
            self.update(
                f"● Done ({self._count} tool uses · {self._total:.1f}s)"
                "  (ctrl+o to expand)"
            )

    def toggle(self) -> None:
        self._expanded = not self._expanded
        self._refresh_display()


    def on_click(self) -> None:
        self.toggle()


class SubAgentBlock(Static, can_focus=True):

    def __init__(self, agent_type: str, description: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._agent_type = agent_type or "agent"
        self._description = description[:60] if description else ""
        self._done = False
        self._is_error = False
        self._elapsed = 0.0
        self._collapsed = True
        self._result_preview = ""
        self._tool_count = 0
        self._render_running()

    def _render_running(self) -> None:
        desc = f"({self._description})" if self._description else ""
        self.update(f"● {self._agent_type}{desc}\n     Running…")

    def set_result(self, output: str, is_error: bool, elapsed: float) -> None:
        self._done = True
        self._is_error = is_error
        self._elapsed = elapsed
        self._result_preview = output[:300] if output else ""
        self._parse_stats(output)
        self._render_done()

    def _parse_stats(self, output: str) -> None:
        import re
        m = re.search(r"(\d+)\s+tool", output[:200])
        if m:
            self._tool_count = int(m.group(1))

    def _render_done(self) -> None:
        desc = f"({self._description})" if self._description else ""
        tool_info = f"{self._tool_count} tool uses · " if self._tool_count else ""
        if self._collapsed:
            self.update(
                f"● {self._agent_type}{desc}\n"
                f"    ⎿  Done ({tool_info}{self._elapsed:.1f}s)  (ctrl+o to expand)"
            )
        else:
            self.update(
                f"● {self._agent_type}{desc}\n"
                f"    ⎿  Done ({tool_info}{self._elapsed:.1f}s)\n"
                f"  {self._result_preview}"
            )

    def on_click(self) -> None:
        if not self._done:
            return
        self._collapsed = not self._collapsed
        self._render_done()


_LUMO_THEME = Theme(
    name="lumo",
    primary="#9B8AFB",
    secondary="#6FE7C8",
    accent="#79D9FF",
    foreground="#E8ECF3",
    background="#101218",
    surface="#161A22",
    panel="#1C222C",
    boost="#252D39",
    success="#6FE7C8",
    warning="#F2C66D",
    error="#FF6B7A",
    dark=True,
)


class LumoApp(App):
    CSS_PATH = "styles.tcss"
    TITLE = "Lumo"
    INLINE_PADDING = 0
    theme = "lumo"
    BINDINGS = [
        Binding("ctrl+c", "handle_ctrl_c", "Quit", priority=True),
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("shift+tab", "cycle_mode", "Cycle mode", priority=True),
        Binding("ctrl+o", "toggle_tool_blocks", "Toggle tools", priority=True),
    ]


    def __init__(
        self,
        providers: list[ProviderConfig],
        permission_mode: PermissionMode = PermissionMode.DEFAULT,
        mcp_servers: list[MCPServerConfig] | None = None,
        hook_engine: HookEngine | None = None,
        enable_fork: bool = False,
        enable_verification_agent: bool = False,
        worktree_config: Any = None,
        teammate_mode: str = "",
        enable_coordinator_mode: bool = False,
        driver_class: type | None = None,
        sandbox_config: Any = None,
        app_config: AppConfig | None = None,
        scenario_id: str | None = None,
        startup_transition: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(driver_class=driver_class)
        self.providers = providers
        self.app_config = app_config or AppConfig(
            providers=providers,
            permission_mode=permission_mode.value,
            mcp_servers=mcp_servers or [],
            enable_fork=enable_fork,
            enable_verification_agent=enable_verification_agent,
            worktree=worktree_config,
            teammate_mode=teammate_mode,
            enable_coordinator_mode=enable_coordinator_mode,
            sandbox=sandbox_config,
        )
        from lumo.runtime.resolver import ScenarioResolver
        self._scenario_resolver = ScenarioResolver(self.app_config, os.getcwd())
        self._scenario_records = self._scenario_resolver.loader.list()
        self._show_scenario_selector = scenario_id is None
        self._scenario_id = scenario_id or self.app_config.default_scenario
        self._startup_transition = startup_transition
        try:
            self.runtime_spec = self._scenario_resolver.resolve(self._scenario_id)
        except Exception:
            if scenario_id is not None:
                raise
            self._scenario_id = "coding"
            self.runtime_spec = self._scenario_resolver.resolve("coding")
        self._initial_permission_mode = permission_mode
        self._mcp_server_configs = list(self.runtime_spec.mcp_servers)
        self.hook_engine = hook_engine
        self._enable_fork = enable_fork
        self._enable_verification_agent = enable_verification_agent
        self._worktree_config = worktree_config
        self._teammate_mode = teammate_mode
        self._enable_coordinator_mode = enable_coordinator_mode
        from lumo.config import SandboxAppConfig
        self._sandbox_cfg: SandboxAppConfig = sandbox_config or SandboxAppConfig()
        self.client: LLMClient | None = None
        self.conversation = ConversationManager()
        from lumo.runtime.packs import create_registry_for_spec
        self.registry: ToolRegistry = create_registry_for_spec(self.runtime_spec)
        self.agent: Agent | None = None
        self.mcp_manager: MCPManager | None = None
        self.runtime_capabilities = None
        self._mcp_init_task: asyncio.Task[None] | None = None
        self._selected_provider: ProviderConfig | None = None
        self._streaming = False
        self._thinking_start: float = 0.0
        self._thinking_verb: str = ""
        self._spinner_idx: int = 0
        self._spinner_timer = None
        self._spinner_label: Static | None = None
        self._mcp_server_info: str = ""
        self._agent_task: asyncio.Task[None] | None = None
        self._subagent_task: asyncio.Task[None] | None = None
        self._subagent_start_time: float | None = None
        self.session_manager: SessionManager | None = None
        self.session: Session | None = None
        self.memory_manager: MemoryManager | None = None
        self._instructions_content: str = ""
        self.command_registry = CommandRegistry()
        register_all_commands(self.command_registry)
        self.skill_loader: SkillLoader | None = None
        self.skill_executor: SkillExecutor | None = None
        self._load_skill_tool: LoadSkill | None = None
        self.agent_loader: AgentLoader | None = None
        self.task_manager: TaskManager = TaskManager()
        self.trace_manager: TraceManager = TraceManager()
        self._notification_check_task: asyncio.Task[None] | None = None
        self.worktree_manager: WorktreeManager | None = None
        self._stale_cleanup_task: asyncio.Task[None] | None = None
        self._current_streaming_label: Static | None = None
        self._current_ai_row: Vertical | None = None
        self._current_accumulated_text: str = ""
        self._mcp_instructions: str = ""
        self._mcp_instructions_ok: bool = False
        self._mcp_connecting: bool = False
        self._teammate_tree: TeammateTree | None = None
        self._teammate_timer = None
        # 记录本次会话是否曾退出过 Plan Mode，用于重入时注入提示
        self._has_exited_plan_mode: bool = False

    @staticmethod
    def _make_banner(model: str = "", work_dir: str = "") -> RichText:
        t = RichText()
        t.append("    ✦      ", style="bold #9B8AFB")
        t.append("Lumo v0.2.0\n", style="#E8ECF3")
        t.append("  · │ ·    ", style="bold #6FE7C8")
        t.append(f"{model}\n" if model else "\n", style="#8E98AA")
        t.append("    ·      ", style="#79D9FF")
        t.append(work_dir, style="#8E98AA")
        return t

    def compose(self) -> ComposeResult:
        yield Static(self._make_banner(), id="title-bar")

        if self._show_scenario_selector:
            with Vertical(id="scenario-select"):
                yield Static("Select a Scenario", id="scenario-select-label")
                yield OptionList(
                    *[
                        Option(
                            (
                                f"{record.definition.name}  [{record.id}]"
                                if record.definition is not None
                                else f"{record.id}  [broken: {record.error}]"
                            ),
                            id=record.id,
                            disabled=not record.healthy,
                        )
                        for record in self._scenario_records
                    ],
                    id="scenario-list",
                )

        if len(self.providers) > 1:
            with Vertical(id="provider-select"):
                yield Static("Select a Provider", id="select-label")
                yield OptionList(
                    *[
                        Option(f"{p.name}  [{p.model}]", id=p.name)
                        for p in self.providers
                    ],
                    id="provider-list",
                )
        yield VerticalScroll(id="chat-area")
        with Vertical(id="input-area"):
            yield ChatInput(id="chat-input")
            with Horizontal(id="status-bar"):
                yield Static(
                    f" scenario: {self.runtime_spec.scenario.id}",
                    id="scenario-label",
                )
                yield Static("  mode: default", id="mode-label")
                yield Static("", id="teammates-label")
                yield Static("", id="model-label")
            yield CompletionPopup()

    async def on_mount(self) -> None:
        self.register_theme(_LUMO_THEME)
        self.theme = "lumo"
        if self._show_scenario_selector:
            self.query_one("#chat-area").display = False
            self.query_one("#input-area").display = False
            provider_select = self.query("#provider-select")
            if provider_select:
                provider_select.first().display = False
        elif len(self.providers) == 1:
            await self._select_provider(self.providers[0])
        else:
            self.query_one("#chat-area").display = False
            self.query_one("#input-area").display = False

    async def _select_provider(self, provider: ProviderConfig) -> None:
        self._selected_provider = provider
        work_dir = os.getcwd()
        try:
            from lumo.runtime.builder import RuntimeBuilder
            builder = RuntimeBuilder(
                self.app_config,
                work_dir,
                scenario_id=self.runtime_spec.scenario.id,
                permission_mode=self._initial_permission_mode,
            )
            foundation = await builder.build(provider=provider)
        except Exception as e:
            self._show_error(str(e))
            return
        self.runtime_foundation = foundation
        self.runtime_spec = foundation.spec
        self.client = foundation.client
        self.registry = foundation.registry
        checker = foundation.permission_checker
        self.skill_loader = foundation.skill_loader
        self.runtime_capabilities = foundation.capabilities
        self.mcp_manager = foundation.capabilities.mcp_manager

        self._instructions_content = load_instructions(work_dir)
        self.memory_manager = (
            MemoryManager(
                work_dir,
                namespace=self.runtime_spec.scenario.id,
            )
            if self.runtime_spec.scenario.features.memory
            else None
        )
        self.session_manager = SessionManager(work_dir)
        self.session_manager.cleanup()
        self.session = self.session_manager.create(
            scenario_id=self.runtime_spec.scenario.id,
            runtime_fingerprint=self.runtime_spec.fingerprint,
            runtime_snapshot=self.runtime_spec.snapshot(),
            memory_namespace=self.runtime_spec.scenario.id,
        )

        from lumo.filehistory import FileHistory
        self.file_history = FileHistory(work_dir, self.session.session_id)
        for tool in self.registry.list_tools():
            if hasattr(tool, "file_history"):
                tool.file_history = self.file_history

        load_skill_tool = None
        if (
            "core.skills" in self.runtime_spec.scenario.packs
            and self.skill_loader.get_catalog()
        ):
            load_skill_tool = LoadSkill()
            self.registry.register(load_skill_tool)
            self._load_skill_tool = load_skill_tool

        install_skill_tool = None
        if (
            load_skill_tool is not None
            and self.runtime_spec.scenario.features.skill_install
        ):
            install_skill_tool = InstallSkillTool()
            self.registry.register(install_skill_tool)
            self._install_skill_tool = install_skill_tool

        self.registry.register(
            ToolSearchTool(self.registry, protocol=provider.protocol)
        )
        self.registry.register(AskUserTool())

        from lumo.tools.exit_plan_mode import ExitPlanModeTool
        self._exit_plan_tool = ExitPlanModeTool()
        self.registry.register(self._exit_plan_tool)

        self.agent = Agent(
            client=self.client,
            registry=self.registry,
            protocol=provider.protocol,
            work_dir=work_dir,
            permission_checker=checker,
            context_window=provider.get_context_window(),
            instructions_content=self._instructions_content,
            memory_manager=self.memory_manager,
            hook_engine=self.hook_engine,
            runtime_persona=self.runtime_spec.scenario.persona,
            runtime_spec=self.runtime_spec,
            provider_name=provider.name,
            model_name=provider.model,
            surface="tui",
            runtime_diagnostics=foundation.capabilities.diagnostics,
        )
        self.agent.file_history = self.file_history
        self.agent.session_id = self.session.session_id

        self._exit_plan_tool._is_plan_mode = lambda: self.agent.plan_mode
        self._exit_plan_tool._plan_exists = lambda: self.agent._get_plan_path().exists()

        # Layer 2: 在后台异步拉取模型的 context window，不阻塞启动流程。
        # agent 已经有一个同步解析的窗口值（来自配置 / 映射表 / 默认值）；
        # 如果异步拉取成功，就原地升级为更准确的值。
        self.run_worker(
            self._resolve_context_window(provider), exclusive=False
        )

        if load_skill_tool is not None:
            load_skill_tool.set_loader(self.skill_loader)
            load_skill_tool.set_agent(self.agent)

        if install_skill_tool is not None:
            install_skill_tool.set_loader(self.skill_loader)

        self.skill_executor = SkillExecutor(
            agent=self.agent,
            client=self.client,
            protocol=provider.protocol,
        )

        catalog = self.skill_loader.get_catalog() if load_skill_tool is not None else []
        if catalog and load_skill_tool is not None:
            lines = [
                "You can use the following Skills:",
                "",
            ]
            for name, desc in catalog:
                lines.append(f"- {name}: {desc}")
            lines.append("")
            lines.append(
                "If the user's request matches a Skill, call LoadSkill to activate it."
            )
            self.agent.set_skill_catalog("\n".join(lines))

        register_skill_commands(
            self.command_registry, self.skill_loader, self.skill_executor
        )

        # 安装新 skill 后重新注册斜杠命令，让 /<new-skill> 立即可用
        def _on_skill_installed(name: str) -> None:
            register_skill_commands(
                self.command_registry, self.skill_loader, self.skill_executor
            )

        if install_skill_tool is not None:
            install_skill_tool.set_on_installed(_on_skill_installed)

        # --- Worktree 系统初始化 ---
        from lumo.config import WorktreeConfig
        wt_cfg = self._worktree_config or WorktreeConfig()
        self.worktree_manager = WorktreeManager(
            repo_root=work_dir,
            symlink_directories=wt_cfg.symlink_directories,
        )
        restored = self.worktree_manager.restore_session()
        if restored:
            self.agent.work_dir = restored.worktree_path

        wt_command = create_worktree_command(self.worktree_manager)
        self.command_registry.register_sync(wt_command)

        from lumo.tools.enter_worktree import EnterWorktreeTool
        from lumo.tools.exit_worktree import ExitWorktreeTool
        if self.runtime_spec.scenario.features.worktree:
            self.registry.register(EnterWorktreeTool(worktree_manager=self.worktree_manager))
            self.registry.register(ExitWorktreeTool(worktree_manager=self.worktree_manager))

        if self.runtime_spec.scenario.features.worktree:
            self._stale_cleanup_task = asyncio.create_task(
                start_stale_cleanup_task(
                    self.worktree_manager,
                    wt_cfg.stale_cleanup_interval,
                    wt_cfg.stale_cutoff_hours,
                )
            )

        # --- 子 agent 系统初始化 ---
        self.agent_loader = AgentLoader(
            work_dir, enable_verification=self._enable_verification_agent
        )
        self.agent_loader.load_all()

        # --- Agent 团队系统初始化 ---
        from lumo.teams.manager import TeamManager
        from lumo.tools.team_create import TeamCreateTool
        from lumo.tools.team_delete import TeamDeleteTool

        self.team_manager = TeamManager(worktree_manager=self.worktree_manager, trace_manager=self.trace_manager)

        agent_tool = AgentTool(
            agent_loader=self.agent_loader,
            task_manager=self.task_manager,
            trace_manager=self.trace_manager,
            parent_agent=self.agent,
            enable_fork=self._enable_fork,
            provider_config=provider,
            worktree_manager=self.worktree_manager,
            team_manager=self.team_manager,
        )
        if self.runtime_spec.scenario.features.subagents:
            self.registry.register(agent_tool)

        team_create_tool = TeamCreateTool(
            team_manager=self.team_manager,
            parent_agent=self.agent,
            teammate_mode=self._teammate_mode,
            is_interactive=True,
            enable_coordinator_mode=self._enable_coordinator_mode,
        )
        if self.runtime_spec.scenario.features.teams:
            self.registry.register(team_create_tool)

        team_delete_tool = TeamDeleteTool(
            team_manager=self.team_manager,
            parent_agent=self.agent,
        )
        if self.runtime_spec.scenario.features.teams:
            self.registry.register(team_delete_tool)

        agent_catalog = (
            self.agent_loader.list_agents()
            if self.runtime_spec.scenario.features.subagents
            else []
        )
        if agent_catalog:
            lines = [
                "## Available Sub-Agent Types",
                "",
                "Use the Agent tool with subagent_type parameter to delegate tasks:",
                "",
            ]
            for agent_type, when_to_use in agent_catalog:
                lines.append(f"- **{agent_type}**: {when_to_use}")
            if self._enable_fork:
                lines.append("")
                lines.append(
                    "Leave subagent_type empty to fork the current conversation "
                    "(inherits full dialog history)."
                )
            lines.append("")
            lines.append(
                "IMPORTANT: Sub-agents run in the background. "
                "After calling the Agent tool, you will get a task ID immediately. "
                "Do NOT wait, sleep, or poll for the result. "
                "Simply report the task ID to the user and end your turn. "
                "The system will automatically notify when the task completes."
            )
            self.agent.set_agent_catalog("\n".join(lines), catalog_list=agent_catalog)

        tasks_cmd = create_tasks_command(self.task_manager)
        self.command_registry.register_sync(tasks_cmd)

        from lumo.commands.handlers.trace import create_trace_command
        trace_cmd = create_trace_command(self.trace_manager, self.agent.agent_id)
        self.command_registry.register_sync(trace_cmd)

        # --- 协调者模式初始化（工具已注册，激活推迟到 TeamCreate 时） ---
        from lumo.tools.synthetic_output import SyntheticOutputTool

        self.registry.register(SyntheticOutputTool())
        self.agent._team_manager = self.team_manager

        self.session.meta.prompt_fingerprint = self.agent.prompt_fingerprint()
        self.session.meta.save(
            self.session._sessions_dir / f"{self.session.session_id}.meta"
        )

        if self.hook_engine:
            asyncio.ensure_future(
                self.hook_engine.run_hooks(
                    "startup", HookContext(event_name="startup")
                )
            )

        connect_result = foundation.capabilities.mcp_result
        if connect_result is not None:
            mcp_tools = sum(
                1
                for tool in self.registry.list_tools()
                if (
                    self.registry.origin(tool.name) is not None
                    and self.registry.origin(tool.name).kind == "mcp"
                )
            )
            if connect_result.servers:
                self._mcp_server_info = (
                    f"Connected to {len(connect_result.servers)} MCP server(s), "
                    f"{mcp_tools} tools registered"
                )
                parts = [
                    f"## {info.name}\n{info.instructions}"
                    for info in connect_result.servers
                ]
                self._mcp_instructions = (
                    "# MCP Server Instructions\n\n"
                    "The following MCP servers have provided instructions "
                    "for how to use their tools and resources:\n\n"
                    + "\n\n".join(parts)
                )

        self.query_one("#model-label", Static).update(provider.model)
        work_dir = os.getcwd()
        self.query_one("#title-bar", Static).update(
            self._make_banner(provider.model, work_dir)
        )
        self._update_mode_label()

        select = self.query("#provider-select")
        if select:
            select.first().display = False
        self.query_one("#chat-area").display = True
        self.query_one("#input-area").display = True
        chat_input = self.query_one("#chat-input", ChatInput)
        chat_input.placeholder = "Send a message..."
        chat_input.load_history(work_dir)
        chat_input.focus()

        if self._startup_transition:
            self._show_runtime_ready(self._startup_transition)
            self._startup_transition = None
        else:
            self._show_runtime_overview()

        for diagnostic in foundation.capabilities.diagnostics:
            if diagnostic.severity != "info":
                self._show_system_message(
                    f"{diagnostic.severity.upper()} {diagnostic.code}: "
                    f"{diagnostic.message}"
                )

        self._notification_check_task = asyncio.create_task(
            self._start_notification_polling()
        )

    async def _resolve_context_window(self, provider: ProviderConfig) -> None:
        """Layer 2 后台 worker：异步拉取模型的 context window，
        拉到就原地升级 agent 的窗口值。

        尽力而为 — resolve_context_window 不会抛异常；如果拉不到，
        agent 继续使用同步解析得到的窗口值。
        """
        await resolve_context_window(provider)
        if self.agent is not None:
            self.agent.context_window = provider.get_context_window()

    async def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "scenario-list":
            scenario_id = str(event.option.id)
            self._scenario_id = scenario_id
            self.runtime_spec = self._scenario_resolver.resolve(scenario_id)
            self._mcp_server_configs = list(self.runtime_spec.mcp_servers)
            from lumo.runtime.packs import create_registry_for_spec
            self.registry = create_registry_for_spec(self.runtime_spec)
            self.query_one("#scenario-select").display = False
            self.query_one("#scenario-label", Static).update(
                f" scenario: {scenario_id}"
            )
            if len(self.providers) == 1:
                await self._select_provider(self.providers[0])
            else:
                self.query_one("#provider-select").display = True
        elif event.option_list.id == "provider-list":
            provider = self.providers[event.option_index]
            await self._select_provider(provider)

    # -----------------------------------------------------------------
    # UIController 协议实现
    # -----------------------------------------------------------------

    def add_system_message(self, text: str) -> None:
        self._show_system_message(text)

    def send_user_message(self, text: str) -> None:
        if self._streaming or self.agent is None:
            return
        self._agent_task = asyncio.create_task(self._send_message(text))

    def set_plan_mode(self, enabled: bool) -> None:
        if self.agent is None:
            return
        previous = self.agent.permission_mode
        if enabled:
            self._pre_plan_mode = self.agent.permission_mode
            self.agent.set_permission_mode(PermissionMode.PLAN)
        else:
            restore = getattr(self, "_pre_plan_mode", PermissionMode.DEFAULT)
            self.agent.set_permission_mode(restore)
        self._update_mode_label()
        if previous != self.agent.permission_mode:
            self._show_mode_transition(previous, self.agent.permission_mode)

    def get_token_count(self) -> tuple[int, int]:
        if self.agent:
            return self.agent.total_input_tokens, self.agent.total_output_tokens
        return 0, 0

    def refresh_status(self) -> None:
        self._update_mode_label()

    # -----------------------------------------------------------------
    # 命令分发
    # -----------------------------------------------------------------


    def _build_command_context(self, args: str) -> CommandContext:
        return CommandContext(
            args=args,
            agent=self.agent,
            conversation=self.conversation,
            session=self.session,
            session_manager=self.session_manager,
            memory_manager=self.memory_manager,
            ui=self,
            config={
                "registry": self.command_registry,
                "set_session": self._set_session,
                "set_conversation": self._set_conversation,
                "clear_chat": self._clear_chat,
                "render_restored": self._render_restored_messages,
                "skill_loader": self.skill_loader,
                "skill_executor": self.skill_executor,
                "scenario_loader": self._scenario_resolver.loader,
                "scenario_resolver": self._scenario_resolver,
                "runtime_spec": self.runtime_spec,
                "runtime_diagnostics": (
                    self.runtime_capabilities.diagnostics
                    if self.runtime_capabilities is not None
                    else self.runtime_spec.diagnostics
                ),
                "show_scenario_manager": self._show_scenario_manager,
                "switch_scenario": self._switch_scenario,
                "edit_scenario": self._show_scenario_editor,
                "start_scenario_designer": self._start_scenario_designer,
                "refine_scenario": self._start_scenario_refiner,
                "test_scenario": self._test_scenario,
            },
        )

    def _show_scenario_manager(self) -> None:
        from lumo.scenario_dialog import InlineScenarioWidget
        chat = self.query_one("#chat-area", VerticalScroll)
        input_widget = self.query_one("#chat-input", ChatInput)
        input_widget.disabled = True
        widget = InlineScenarioWidget(
            self._scenario_resolver.loader.list(),
            self.runtime_spec.scenario.id,
        )
        chat.mount(widget)
        self.call_after_refresh(chat.scroll_end, animate=False)

    async def on_inline_scenario_widget_selected(self, event) -> None:
        widget = event.control if hasattr(event, "control") else self.query_one("#scenario-inline")
        try:
            await widget.remove()
        except Exception:
            pass
        input_widget = self.query_one("#chat-input", ChatInput)
        input_widget.disabled = False
        input_widget.focus()
        if event.scenario_id and event.scenario_id != self.runtime_spec.scenario.id:
            self._show_scenario_switch(event.scenario_id)

    def _show_scenario_switch(self, scenario_id: str) -> None:
        from lumo.scenario_dialog import InlineScenarioSwitchWidget

        try:
            target = self._scenario_resolver.resolve(scenario_id)
        except Exception as exc:
            self._show_system_message(f"Cannot switch scenario: {exc}")
            return
        chat = self.query_one("#chat-area", VerticalScroll)
        input_widget = self.query_one("#chat-input", ChatInput)
        input_widget.disabled = True
        chat.mount(InlineScenarioSwitchWidget(self.runtime_spec, target))
        self.call_after_refresh(chat.scroll_end, animate=False)

    async def on_inline_scenario_switch_widget_responded(self, event) -> None:
        widget = self.query_one("#scenario-switch-inline")
        try:
            await widget.remove()
        except Exception:
            pass
        input_widget = self.query_one("#chat-input", ChatInput)
        input_widget.disabled = False
        input_widget.focus()
        if event.confirmed:
            await self._switch_scenario(event.scenario_id)

    async def _switch_scenario(self, scenario_id: str) -> None:
        try:
            target = self._scenario_resolver.resolve(scenario_id)
        except Exception as exc:
            self._show_system_message(f"Cannot switch scenario: {exc}")
            return
        if self._selected_provider is None:
            self._show_system_message("Cannot switch scenario without an active provider.")
            return
        from lumo.runtime.builder import RuntimeBuilder
        self._show_system_message(
            "[bold]Validating candidate Runtime[/]\n"
            f"[dim]Building {target.scenario.name} before leaving the current session…[/]"
        )
        try:
            candidate = await RuntimeBuilder(
                self.app_config,
                os.getcwd(),
                scenario_id=scenario_id,
                permission_mode=self.agent.permission_mode if self.agent else None,
            ).build(provider=self._selected_provider)
        except Exception as exc:
            self._show_system_message(
                "[bold][red]Candidate Runtime failed[/red][/bold]\n"
                f"{exc}\n"
                "[dim]The current Runtime and Session are unchanged.[/]"
            )
            return
        await candidate.aclose()
        await self._shutdown_mcp()
        if self._stale_cleanup_task and not self._stale_cleanup_task.done():
            self._stale_cleanup_task.cancel()
        if self.session:
            self.session.close()
        self.exit({
            "scenario_id": scenario_id,
            "transition": {
                "from_id": self.runtime_spec.scenario.id,
                "from_name": self.runtime_spec.scenario.name,
                "to_id": target.scenario.id,
                "to_name": target.scenario.name,
                "packs": len(target.scenario.packs),
                "mcp": len(target.mcp_servers),
                "cli": len(target.cli_definitions),
                "plugins": len(target.plugin_descriptors),
            },
        })

    def _show_scenario_editor(
        self,
        definition,
        scope: str = "user",
        overwrite: bool = False,
    ) -> None:
        from lumo.runtime.catalog import CapabilityCatalog
        from lumo.scenario_editor import ScenarioEditorWidget

        self._scenario_edit_scope = scope
        self._scenario_edit_overwrite = overwrite
        catalog = CapabilityCatalog(self.app_config, os.getcwd()).list()
        chat = self.query_one("#chat-area", VerticalScroll)
        input_widget = self.query_one("#chat-input", ChatInput)
        input_widget.disabled = True
        chat.mount(ScenarioEditorWidget(definition, catalog))
        self.call_after_refresh(chat.scroll_end, animate=False)

    async def on_scenario_editor_widget_saved(self, event) -> None:
        widget = self.query_one("#scenario-editor-inline")
        try:
            await widget.remove()
        except Exception:
            pass
        input_widget = self.query_one("#chat-input", ChatInput)
        input_widget.disabled = False
        input_widget.focus()
        if event.definition is None:
            return
        try:
            path = self._scenario_resolver.loader.save(
                event.definition,
                scope=self._scenario_edit_scope,
                overwrite=self._scenario_edit_overwrite,
            )
        except Exception as exc:
            self._show_system_message(f"Could not save scenario: {exc}")
            return
        self._show_system_message(
            f"Scenario saved: {path}\n"
            f"Use /scenario use {event.definition.id} to start it."
        )

    async def _start_scenario_designer(self, description: str) -> None:
        if self.client is None or self.agent is None:
            self._show_system_message("Scenario Designer requires an active model.")
            return
        if self._streaming:
            self._show_system_message(
                "Wait for the current response before opening Scenario Designer."
            )
            return
        from dataclasses import replace
        from lumo.runtime.catalog import CapabilityCatalog
        from lumo.scenario_designer.proposal_compiler import ProposalCompiler
        from lumo.scenario_designer.service import ScenarioDesignerService
        from lumo.scenario_designer.validation import validate_draft
        from lumo.scenario_designer_dialog import ScenarioDraftReviewWidget

        input_widget = self.query_one("#chat-input", ChatInput)
        input_widget.disabled = True
        self._show_system_message(
            "[bold]Scenario Designer[/]\n"
            "[dim]Matching your request against installed capabilities…[/]"
        )
        capabilities = CapabilityCatalog(self.app_config, os.getcwd()).list()
        self._scenario_designer_scope = "user"
        self._scenario_designer_overwrite = False
        try:
            proposal = await ScenarioDesignerService(capabilities).propose(
                description,
                self.client,
                self.agent.protocol,
            )
            if proposal.unresolved_questions:
                from lumo.askuser_dialog import InlineAskUserWidget
                questions = [
                    {
                        "header": f"Q{index}",
                        "question": question,
                        "options": [],
                        "multiSelect": False,
                    }
                    for index, question in enumerate(
                        proposal.unresolved_questions, 1
                    )
                ]
                self._show_system_message(
                    "[bold]Scenario Designer needs more information[/]\n"
                    "[dim]Answer the questions below to continue composing.[/]"
                )
                self._scenario_designer_clarification = {
                    "description": description,
                }
                chat = self.query_one("#chat-area", VerticalScroll)
                await chat.mount(InlineAskUserWidget(questions))
                self.call_after_refresh(chat.scroll_end, animate=False)
                return
            draft = ProposalCompiler(capabilities).compile(proposal)
            diagnostics = validate_draft(draft)
            draft = replace(draft, diagnostics=diagnostics)
            self._scenario_designer_draft = draft
            chat = self.query_one("#chat-area", VerticalScroll)
            chat.mount(ScenarioDraftReviewWidget(draft))
            self.call_after_refresh(chat.scroll_end, animate=False)
        except Exception as exc:
            self._show_system_message(f"Scenario Designer failed: {exc}")
            input_widget.disabled = False
            input_widget.focus()
        finally:
            if (
                not hasattr(self, "_scenario_designer_draft")
                and not hasattr(self, "_scenario_designer_clarification")
            ):
                input_widget.disabled = False
                input_widget.focus()

    async def _start_scenario_refiner(
        self,
        scenario_id: str,
        request: str,
    ) -> None:
        if self.client is None or self.agent is None:
            self._show_system_message("Scenario Designer requires an active model.")
            return
        record = self._scenario_resolver.loader.get(scenario_id)
        if record is None or record.definition is None or not record.healthy:
            self._show_system_message(f"Scenario not found or broken: {scenario_id}")
            return
        if record.source == "builtin":
            self._show_system_message(
                f"Built-in scenario '{scenario_id}' is read-only. "
                f"Copy it before refining."
            )
            return
        if record.path is None or record.path.name != "scenario.yaml":
            self._show_system_message(
                "Model-assisted refine requires a directory-style scenario package. "
                "Create a new package or convert this legacy YAML first."
            )
            return
        from dataclasses import replace
        from lumo.runtime.catalog import CapabilityCatalog
        from lumo.scenario_designer.proposal_compiler import ProposalCompiler
        from lumo.scenario_designer.service import ScenarioDesignerService
        from lumo.scenario_designer.validation import validate_draft
        from lumo.scenario_designer_dialog import ScenarioDraftReviewWidget

        input_widget = self.query_one("#chat-input", ChatInput)
        input_widget.disabled = True
        self._show_system_message(
            f"[bold]Refining {record.definition.name}[/]\n"
            "[dim]Generating a minimal structured patch…[/]"
        )
        capabilities = CapabilityCatalog(self.app_config, os.getcwd()).list()
        try:
            patch = await ScenarioDesignerService(capabilities).refine(
                record.definition,
                request,
                self.client,
                self.agent.protocol,
            )
            if patch.unresolved_questions:
                questions = "\n".join(
                    f"- {item}" for item in patch.unresolved_questions
                )
                self._show_system_message(
                    "[bold]Refinement needs more information[/]\n" + questions
                )
                return
            draft = ProposalCompiler(capabilities).apply_patch(
                record.definition,
                patch,
            )
            draft = replace(draft, diagnostics=validate_draft(draft))
            self._scenario_designer_draft = draft
            self._scenario_designer_scope = record.source
            self._scenario_designer_overwrite = True
            chat = self.query_one("#chat-area", VerticalScroll)
            chat.mount(ScenarioDraftReviewWidget(draft))
            self.call_after_refresh(chat.scroll_end, animate=False)
        except Exception as exc:
            self._show_system_message(f"Scenario refinement failed: {exc}")
            input_widget.disabled = False
            input_widget.focus()
        finally:
            if not hasattr(self, "_scenario_designer_draft"):
                input_widget.disabled = False
                input_widget.focus()

    async def _test_scenario(self, scenario_id: str) -> None:
        if self._selected_provider is None:
            self._show_system_message("Scenario test requires an active provider.")
            return
        from lumo.runtime.builder import RuntimeBuilder

        self._show_system_message(
            f"[bold]Testing scenario {scenario_id}[/]\n"
            "[dim]Resolving capabilities and building a disposable candidate Runtime…[/]"
        )
        try:
            candidate = await RuntimeBuilder(
                self.app_config,
                os.getcwd(),
                scenario_id=scenario_id,
                permission_mode=self.agent.permission_mode if self.agent else None,
            ).build(provider=self._selected_provider)
            tools = [
                tool.name for tool in candidate.registry.list_tools()
                if candidate.registry.is_enabled(tool.name)
            ]
            diagnostics = candidate.capabilities.diagnostics
            await candidate.aclose()
        except Exception as exc:
            self._show_system_message(
                f"[bold][red]Scenario test failed[/red][/bold]\n{exc}"
            )
            return
        warnings = sum(
            1 for item in diagnostics if item.severity == "warning"
        )
        self._show_system_message(
            "[bold][green]Scenario test passed[/green][/bold]\n"
            f"Tools: {len(tools)}\n"
            f"Warnings: {warnings}\n"
            f"[dim]{', '.join(tools) if tools else 'No model-callable tools'}[/]"
        )
    async def on_scenario_draft_review_widget_responded(self, event) -> None:
        widget = self.query_one("#scenario-draft-review")
        try:
            await widget.remove()
        except Exception:
            pass
        input_widget = self.query_one("#chat-input", ChatInput)
        input_widget.disabled = False
        input_widget.focus()
        draft = getattr(self, "_scenario_designer_draft", None)
        if hasattr(self, "_scenario_designer_draft"):
            del self._scenario_designer_draft
        if event.action == "cancel" or draft is None:
            self._show_system_message("Scenario creation cancelled.")
            return
        from lumo.runtime.scenario_package import ScenarioPackageWriter

        try:
            path = ScenarioPackageWriter(os.getcwd()).write(
                draft.definition,
                draft.prompt_text,
                scope=getattr(self, "_scenario_designer_scope", "user"),
                overwrite=getattr(self, "_scenario_designer_overwrite", False),
            )
        except Exception as exc:
            self._show_system_message(f"Could not save scenario package: {exc}")
            return
        self._show_system_message(f"Scenario package saved: {path}")
        if event.action == "save-start":
            await self._switch_scenario(draft.definition.id)

    def _set_session(self, session: Session) -> None:
        self.session = session
        if self.agent:
            self.agent.session_id = session.session_id

    def _persist_compact_boundary(self, notification: CompactNotification) -> None:
        """Layer-2 compact 后写入 compact_boundary 记录。

        将摘要 + 原样保留的尾部内联到一条记录中，resume 时只需这一条
        就能重建压缩后的状态。之前已写入磁盘的原始前缀不会被重放。
        没有活跃 session 或 compact 未产出 boundary 时直接跳过。
        """
        if not self.session or notification.boundary is None:
            return
        record = make_compact_boundary(
            notification.boundary.summary,
            notification.boundary.keep,
        )
        self.session.append_record(record)

    def _set_conversation(self, conv: ConversationManager) -> None:
        self.conversation = conv

    def _clear_chat(self) -> None:
        chat = self.query_one("#chat-area", VerticalScroll)
        chat.remove_children()

    async def _dispatch_command(self, text: str) -> None:
        name, args, is_command = parse_command(text)

        if not is_command:
            if self._streaming or self.agent is None:
                return
            self._agent_task = asyncio.create_task(self._send_message(text))
            return

        if name == "":
            commands = self.command_registry.list_commands()
            lines = ["可用命令："]
            for cmd in commands:
                aliases_str = ", ".join(f"/{a}" for a in cmd.aliases)
                name_part = f"/{cmd.name}"
                if aliases_str:
                    name_part += f", {aliases_str}"
                lines.append(f"  {name_part:<24} {cmd.description}")
            self._show_system_message("\n".join(lines))
            return

        cmd = self.command_registry.find(name)
        if cmd is None:
            self._show_system_message(f"未知命令：/{name}，输入 /help 查看可用命令")
            return

        if not args and cmd.arg_prompt:
            self._show_system_message(cmd.arg_prompt)
            return

        ctx = self._build_command_context(args)
        try:
            await cmd.handler(ctx)
        except Exception as e:
            self._show_error(f"命令执行失败: {e}")

    # -----------------------------------------------------------------
    # 输入处理
    # -----------------------------------------------------------------

    async def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        text = event.text.strip()
        if self._streaming and not text.startswith("/"):
            if self._agent_task and not self._agent_task.done():
                self._agent_task.cancel()
                try:
                    await self._agent_task
                except (asyncio.CancelledError, Exception):
                    pass
            self._finish_streaming()
            self._show_system_message("(response interrupted)")
        await self._dispatch_command(text)

    def on_chat_input_tab_complete(self, event: ChatInput.TabComplete) -> None:
        matches = complete(self.command_registry, event.text)
        if not matches:
            return
        popup = self.query_one(CompletionPopup)
        if len(matches) == 1:
            input_widget = self.query_one("#chat-input", ChatInput)
            input_widget.clear()
            input_widget.insert(matches[0][1] + " ")
        else:
            popup.show_pairs(matches)

    def on_chat_input_slash_menu_update(self, event: ChatInput.SlashMenuUpdate) -> None:
        popup = self.query_one(CompletionPopup)
        if event.prefix is None:
            popup.hide()
            return
        matches = complete(self.command_registry, event.prefix)
        if not matches:
            popup.hide()
            return
        popup.show_pairs(matches)

    def on_chat_input_at_file_request(self, event: ChatInput.AtFileRequest) -> None:
        work_dir = self.agent.work_dir if self.agent else os.getcwd()
        matches = scan_files_for_at(event.prefix, work_dir)
        if matches:
            popup = self.query_one(CompletionPopup)
            popup.show([f"@{m}" for m in matches])

    def on_completion_popup_selected(self, event: CompletionPopup.Selected) -> None:
        input_widget = self.query_one("#chat-input", ChatInput)
        selected = event.value
        text = input_widget.text
        if selected.startswith("@"):
            at_idx = text.rfind("@")
            if at_idx >= 0:
                input_widget.clear()
                input_widget.insert(text[:at_idx] + selected + " ")
                input_widget.focus()
                return
        input_widget.clear()
        input_widget.insert(selected + " ")
        input_widget.focus()

    def action_cycle_mode(self) -> None:
        if self.agent is None:
            return
        current = self.agent.permission_mode
        try:
            idx = _MODE_CYCLE.index(current)
        except ValueError:
            idx = 0
        next_mode = _MODE_CYCLE[(idx + 1) % len(_MODE_CYCLE)]
        self.agent.set_permission_mode(next_mode)
        self._update_mode_label()
        self._show_mode_transition(current, next_mode)

    def show_mode_transition(
        self,
        previous: PermissionMode,
        current: PermissionMode,
    ) -> None:
        self._show_mode_transition(previous, current)

    def action_toggle_tool_blocks(self) -> None:
        for block in self.query(ToolCallBlock):
            if block._loading:
                continue
            block._collapsed = not block._collapsed
            if block._collapsed:
                block._render_collapsed()
            else:
                block._render_expanded()

        for summary in self.query(ToolGroupSummary):
            was_expanded = summary._expanded
            summary.toggle()
            parent = summary.parent
            if parent:
                for child in parent.children:
                    if isinstance(child, ToolCallBlock) and child.tool_name in COLLAPSIBLE_TOOLS:
                        child.display = summary._expanded

        for block in self.query(SubAgentBlock):
            if block._done:
                block._collapsed = not block._collapsed
                block._render_done()

    def action_cancel(self) -> None:
        popup = self.query_one(CompletionPopup)
        if popup.is_visible:
            popup.hide()
            self.query_one("#chat-input", ChatInput).focus()
            return
        if self._agent_task and not self._agent_task.done():
            if self._subagent_task and not self._subagent_task.done():
                task_id = self.task_manager.adopt_running(
                    self._subagent_task, "background task"
                ) if hasattr(self.task_manager, 'adopt_running') else None
                if task_id:
                    self._show_system_message(
                        f"Task moved to background (id: {task_id})"
                    )
                    return
            self._agent_task.cancel()

    async def _prefetch_relevant_memories(self, query: str) -> str:
        """Run the recall selector as a side-query with an 8s timeout.

        Creates a fresh LLM client so the selector's system prompt is
        independent of the main conversation's system prompt. Returns the
        rendered system-reminder body, or "" on any failure / timeout.
        """
        if self.memory_manager is None or self._selected_provider is None:
            return ""

        provider = self._selected_provider
        user_dir = self.memory_manager.user_mem_dir
        project_dir = self.memory_manager.project_mem_dir

        async def selector(system_prompt: str, user_message: str) -> str:
            from lumo.tools.base import StreamEnd, TextDelta

            side_client = create_client(provider)
            mini_conv = ConversationManager()
            mini_conv.history = [Message(role="user", content=user_message)]
            collected = ""
            async for event in side_client.stream(mini_conv, system=system_prompt):
                if isinstance(event, TextDelta):
                    collected += event.text
                elif isinstance(event, StreamEnd):
                    pass
            return collected

        try:
            results = await asyncio.wait_for(
                find_relevant_memories(
                    query=query,
                    user_mem_dir=user_dir,
                    project_mem_dir=project_dir,
                    recent_tools=None,
                    already_surfaced=None,
                    selector=selector,
                ),
                timeout=8.0,
            )
            return render_reminder(results)
        except (asyncio.TimeoutError, Exception):
            return ""

    def _refresh_skills_if_needed(self) -> None:
        """每轮对话前检查 skill 目录 modtime，有变化则自动 reload。"""
        if self.skill_loader is None or self.agent is None:
            return
        if not self.skill_loader.needs_reload():
            return
        self.skill_loader.reload()
        if self.command_registry is not None:
            from lumo.commands.handlers.skill_register import register_skill_commands
            register_skill_commands(
                self.command_registry, self.skill_loader, self.skill_executor
            )
        catalog = self.skill_loader.get_catalog()
        if catalog:
            lines = ["You can use the following Skills:", ""]
            for name, desc in catalog:
                lines.append(f"- {name}: {desc}")
            lines.append("")
            lines.append(
                "If the user's request matches a Skill, call LoadSkill to activate it."
            )
            self.agent.set_skill_catalog("\n".join(lines))
        else:
            self.agent.set_skill_catalog("")

    async def _send_message(self, text: str, is_notification: bool = False) -> None:
        assert self.agent is not None
        self._refresh_skills_if_needed()

        if self._mcp_init_task and not self._mcp_init_task.done():
            self._show_system_message("Waiting for MCP servers to connect...")
            await self._mcp_init_task

        self._streaming = True
        chat = self.query_one("#chat-area", VerticalScroll)
        input_widget = self.query_one("#chat-input", ChatInput)

        if text and "@" in text:
            text = expand_at_refs(text, self.agent.work_dir)

        # Start memory recall prefetch before UI work.
        prefetch_task = asyncio.create_task(
            self._prefetch_relevant_memories(text)
        ) if text else None

        if text:
            user_row = Vertical(classes="user-row")
            await chat.mount(user_row)
            from rich.text import Text as RichText
            user_rich = RichText()
            user_rich.append("❯ ", style="bold color(80)")
            user_rich.append(text, style="bold color(255)")
            user_bubble = Static(user_rich, classes="message user-message")
            await user_row.mount(user_bubble)
            self.call_after_refresh(chat.scroll_end, animate=False)

            self.conversation.add_user_message(text)
            if self.session:
                self.session.append(Message(role="user", content=text))

        if self._mcp_instructions and not self._mcp_instructions_ok:
            self.conversation.add_system_reminder(self._mcp_instructions)
            self._mcp_instructions_ok = True

        # 非阻塞 memory recall：传给 agent，工具执行后注入
        if prefetch_task is not None:
            self.agent.memory_recall_task = prefetch_task
            self.agent._memory_recall_consumed = False

        history_cursor = len(self.conversation.history)

        # 准备 AI 回复区域
        ai_row = Vertical(classes="ai-row")
        await chat.mount(ai_row)
        streaming_label = Static("", classes="message ai-message")
        await ai_row.mount(streaming_label)

        accumulated_text = ""
        tool_blocks: dict[str, ToolCallBlock] = {}

        # 在聊天区底部启动持续旋转的加载动画
        self._thinking_start = _time.monotonic()
        self._thinking_verb = random.choice(THINKING_VERBS)
        self._spinner_idx = 0
        self._spinner_label = Static(
            f"  {SPINNER_FRAMES[0]} {self._thinking_verb}…",
            id="spinner-live",
        )
        await chat.mount(self._spinner_label)

        # Mount teammate tree (initially hidden) below the spinner
        self._teammate_tree = TeammateTree(id="teammate-tree")
        self._teammate_tree.display = False
        await chat.mount(self._teammate_tree)
        self._start_teammate_polling()

        self.call_after_refresh(chat.scroll_end, animate=False)
        self._start_spinner()

        await asyncio.sleep(0)

        try:
            async for event in self.agent.run(self.conversation):
                if isinstance(event, ThinkingText):
                    self.call_after_refresh(chat.scroll_end, animate=False)

                elif isinstance(event, StreamText):
                    if streaming_label is not None and not accumulated_text:
                        await streaming_label.remove()
                        streaming_label = Static("", classes="message ai-message")
                        await ai_row.mount(streaming_label)
                    accumulated_text += event.text
                    from rich.text import Text as RichText
                    t = RichText()
                    t.append("● ", style="bold #9B8AFB")
                    t.append(accumulated_text)
                    streaming_label.update(t)
                    self.call_after_refresh(chat.scroll_end, animate=False)

                elif isinstance(event, RetryEvent):
                    self._show_system_message(f"↻ Retrying: {event.reason}")

                elif isinstance(event, ToolUseEvent):
                    if accumulated_text:
                        if streaming_label is not None:
                            await streaming_label.remove()
                        from rich.text import Text as RichText
                        prefix = Static(RichText("●  ", style="bold #9B8AFB"), classes="message")
                        await ai_row.mount(prefix)
                        md = Markdown(accumulated_text, classes="message ai-message")
                        await ai_row.mount(md)
                        streaming_label = None
                        accumulated_text = ""
                    elif streaming_label is not None:
                        await streaming_label.remove()
                        streaming_label = None

                    if _is_subagent_tool(event.tool_name):
                        agent_type = event.arguments.get("subagent_type", "")
                        desc = event.arguments.get("description", "")
                        block = SubAgentBlock(
                            agent_type or "agent",
                            desc,
                            classes="tool-block subagent-block",
                        )
                    else:
                        block = ToolCallBlock(
                            event.tool_name, event.arguments, classes="tool-block"
                        )
                    await ai_row.mount(block)
                    tool_blocks[event.tool_id] = block
                    self.call_after_refresh(chat.scroll_end, animate=False)

                elif isinstance(event, PermissionRequest):
                    await self._handle_permission_request(event)

                elif isinstance(event, ToolResultEvent):
                    block = tool_blocks.get(event.tool_id)
                    if block:
                        block.set_result(event.output, event.is_error, event.elapsed)
                    self.call_after_refresh(chat.scroll_end, animate=False)

                    ask_tool = self.registry.get("AskUserQuestion")
                    if ask_tool and isinstance(ask_tool, AskUserTool) and ask_tool._pending_event:
                        await self._handle_askuser(ask_tool._pending_event)

                elif isinstance(event, TurnComplete):
                    if self.session:
                        for msg in self.conversation.history[history_cursor:]:
                            self.session.append(msg)
                        history_cursor = len(self.conversation.history)

                    collapsible = [
                        (tid, blk) for tid, blk in tool_blocks.items()
                        if isinstance(blk, ToolCallBlock)
                        and blk.tool_name in COLLAPSIBLE_TOOLS
                        and not blk._loading
                    ]
                    if len(collapsible) >= 2:
                        total_elapsed = sum(b._elapsed for _, b in collapsible)
                        summary = ToolGroupSummary(
                            len(collapsible), total_elapsed,
                            classes="tool-block tool-group-summary",
                        )
                        for _, blk in collapsible:
                            blk.display = False
                        await ai_row.mount(summary)

                    tool_blocks.clear()
                    ai_row = Vertical(classes="ai-row")
                    await chat.mount(ai_row)
                    streaming_label = Static("", classes="message ai-message")
                    await ai_row.mount(streaming_label)
                    accumulated_text = ""
                    self.call_after_refresh(chat.scroll_end, animate=False)

                elif isinstance(event, UsageEvent):
                    pass  # token 展示已移除

                elif isinstance(event, HookEvent):
                    status = "✓" if event.success else "✗"
                    self._show_system_message(
                        f"Hook [{event.hook_id}] {status} {event.output}"
                    )

                elif isinstance(event, CompactNotification):
                    self._show_system_message(event.message)
                    # auto_compact 已重写 conversation.history（摘要 +
                    # boundary + 保留尾部）。先持久化 boundary 记录，然后
                    # 将游标推进到重建后的历史末尾，这样 TurnComplete/LoopComplete
                    # 刷盘时只追加 boundary 之后的新消息，不会把已压缩的
                    # 前缀作为普通记录重复写入。
                    self._persist_compact_boundary(event)
                    history_cursor = len(self.conversation.history)

                elif isinstance(event, ErrorEvent):
                    # 保留错误前已输出的流式文本
                    if accumulated_text and streaming_label is not None:
                        await streaming_label.remove()
                        md = Markdown(accumulated_text, classes="message ai-message")
                        await ai_row.mount(md)
                        streaming_label = None
                        accumulated_text = ""
                    self._show_error(event.message)

                elif isinstance(event, LoopComplete):
                    total_time = _time.monotonic() - self._thinking_start
                    done_label = Static(
                        f"✻ {_to_past_tense(self._thinking_verb)} for {total_time:.1f}s",
                        classes="message thinking-done",
                    )
                    await ai_row.mount(done_label)
                    if self.session:
                        for msg in self.conversation.history[history_cursor:]:
                            self.session.append(msg)
                        history_cursor = len(self.conversation.history)
                        self.session.meta.total_tokens = (
                            self.agent.total_input_tokens
                            + self.agent.total_output_tokens
                        )
                        asyncio.ensure_future(
                            self._update_session_summary()
                        )
                    if self.agent.plan_mode:
                        asyncio.ensure_future(
                            self._show_plan_approval()
                        )

            # 收尾：渲染剩余的累积文本
            if accumulated_text and streaming_label is not None:
                await streaming_label.remove()
                md = Markdown(accumulated_text, classes="message ai-message")
                await ai_row.mount(md)
            elif streaming_label is not None:
                await streaming_label.remove()

            self.call_after_refresh(chat.scroll_end, animate=False)

        except asyncio.CancelledError:
            if accumulated_text:
                if streaming_label is not None:
                    await streaming_label.remove()
                md = Markdown(
                    accumulated_text + "\n\n*[cancelled]*",
                    classes="message ai-message",
                )
                await ai_row.mount(md)
            self._show_system_message("Operation cancelled")
        except LLMError as e:
            self._show_error(str(e))
        finally:
            self._finish_streaming()
            input_widget.focus()

            await self._process_task_notifications()

    async def _process_task_notifications(self) -> None:
        completed = self.task_manager.poll_completed()
        if not completed or self.agent is None:
            return

        inject_task_notifications(self.conversation, completed)

        for task in completed:
            status_icon = "✓" if task.status == "completed" else "✗"
            self._show_system_message(
                f"{status_icon} 后台任务完成: [{task.id}] {task.name} — {task.status}"
            )

            if hasattr(self, 'team_manager'):
                self.team_manager.on_teammate_completed(task.agent.agent_id)

        self._agent_task = asyncio.create_task(
            self._send_message("", is_notification=True)
        )

    async def _start_notification_polling(self) -> None:
        while True:
            await asyncio.sleep(2)
            if not self._streaming and self.agent is not None:
                await self._process_task_notifications()
                await self._process_mailbox_notifications()

    async def _process_mailbox_notifications(self) -> None:
        if not hasattr(self, "team_manager") or self.team_manager is None:
            return
        if self._streaming or self.agent is None:
            return
        notes = self.team_manager.drain_lead_mailbox()
        if not notes:
            return
        for note in notes:
            self.conversation.add_system_reminder(note)
        self._agent_task = asyncio.create_task(
            self._send_message("", is_notification=True)
        )

    async def _show_plan_approval(self) -> None:
        from lumo.plan_dialog import InlinePlanWidget

        chat = self.query_one("#chat-area", VerticalScroll)
        widget = InlinePlanWidget()
        await chat.mount(widget)
        self.call_after_refresh(chat.scroll_end, animate=False)
        try:
            self.query_one("#chat-input").disabled = True
        except Exception:
            pass

    def on_inline_plan_widget_responded(
        self, event: "InlinePlanWidget.Responded"
    ) -> None:
        from lumo.plan_dialog import InlinePlanWidget, PlanChoice
        from lumo.prompts import build_plan_mode_exit_reminder

        try:
            self.query_one("#plan-inline", InlinePlanWidget).remove()
        except Exception:
            pass
        try:
            self.query_one("#chat-input").disabled = False
            self.query_one("#chat-input").focus()
        except Exception:
            pass

        if self.agent is None:
            return

        choice = event.choice
        feedback = event.feedback
        plan_path = self.agent._get_plan_path()
        plan_exists = plan_path.exists()
        plan_content = ""
        if plan_exists:
            try:
                plan_content = plan_path.read_text(encoding="utf-8")
            except Exception:
                pass

        pre = getattr(self, "_pre_plan_mode", PermissionMode.DEFAULT)
        if choice == PlanChoice.YOLO:
            self.agent.set_permission_mode(PermissionMode.BYPASS)
            self._update_mode_label()
            # 构建退出提示并标记已退出 Plan Mode
            exit_msg = build_plan_mode_exit_reminder(str(plan_path), plan_exists)
            self._has_exited_plan_mode = True
            execute_text = exit_msg + "\n\nUser has approved your plan. You can now start coding."
            if plan_content:
                execute_text += "\n\nApproved Plan:\n" + plan_content
            self.send_user_message(execute_text)
        elif choice == PlanChoice.MANUAL:
            self.agent.set_permission_mode(pre)
            self._update_mode_label()
            # 构建退出提示并标记已退出 Plan Mode
            exit_msg = build_plan_mode_exit_reminder(str(plan_path), plan_exists)
            self._has_exited_plan_mode = True
            execute_text = exit_msg + "\n\nUser has approved your plan. You can now start coding."
            if plan_content:
                execute_text += "\n\nApproved Plan:\n" + plan_content
            self.send_user_message(execute_text)
        elif choice == PlanChoice.FEEDBACK:
            if feedback:
                self.send_user_message(feedback)
            else:
                self._show_system_message("Type your feedback and send.")

    async def _handle_askuser(self, event: AskUserEvent) -> None:
        from lumo.askuser_dialog import InlineAskUserWidget

        chat = self.query_one("#chat-area", VerticalScroll)
        widget = InlineAskUserWidget(event.questions)
        self._pending_askuser_event = event
        await chat.mount(widget)
        self.call_after_refresh(chat.scroll_end, animate=False)
        try:
            self.query_one("#chat-input").disabled = True
        except Exception:
            pass

    def on_inline_ask_user_widget_responded(
        self, event: "InlineAskUserWidget.Responded"
    ) -> None:
        from lumo.askuser_dialog import InlineAskUserWidget

        clarification = getattr(
            self, "_scenario_designer_clarification", None
        )
        if clarification is not None:
            del self._scenario_designer_clarification
            try:
                self.query_one(
                    "#askuser-inline", InlineAskUserWidget
                ).remove()
            except Exception:
                pass
            answers = event.answers or {}
            answer_text = "\n".join(
                f"{question}: {answer}"
                for question, answer in answers.items()
                if answer
            )
            description = clarification["description"]
            if answer_text:
                description += "\n\nClarifications:\n" + answer_text
                asyncio.create_task(
                    self._start_scenario_designer(description)
                )
            else:
                self._show_system_message(
                    "Scenario creation cancelled because clarification was not provided."
                )
                self.query_one("#chat-input").disabled = False
                self.query_one("#chat-input").focus()
            return

        req = getattr(self, "_pending_askuser_event", None)
        if req is not None and not req.future.done():
            req.future.set_result(event.answers if event.answers else {})
            self._pending_askuser_event = None
        try:
            self.query_one("#askuser-inline", InlineAskUserWidget).remove()
        except Exception:
            pass
        try:
            self.query_one("#chat-input").disabled = False
            self.query_one("#chat-input").focus()
        except Exception:
            pass

    def _start_spinner(self) -> None:
        """启动 braille spinner 动画（每帧 80ms）。"""
        if self._spinner_timer is not None:
            return
        self._spinner_timer = self.set_interval(0.08, self._tick_spinner)

    def _stop_spinner(self) -> None:
        """停止 spinner 动画。"""
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None

    def _finish_streaming(self) -> None:
        """清理所有 streaming 状态（取消或完成时调用）。"""
        self._streaming = False
        self._stop_spinner()
        self._stop_teammate_polling()
        self._agent_task = None
        if self._teammate_tree is not None:
            self._teammate_tree.remove()
            self._teammate_tree = None
        if self._spinner_label is not None:
            self._spinner_label.remove()
            self._spinner_label = None

    def _tick_spinner(self) -> None:
        """推进持久 spinner 标签上的动画帧。"""
        self._spinner_idx += 1
        frame = SPINNER_FRAMES[self._spinner_idx % len(SPINNER_FRAMES)]
        elapsed = _time.monotonic() - self._thinking_start
        if self._spinner_label is not None:
            self._spinner_label.update(
                f"  {frame} {self._thinking_verb}…  ({elapsed:.0f}s)"
            )
            if self._spinner_idx % 5 == 0:
                try:
                    self.query_one("#chat-area", VerticalScroll).scroll_end(animate=False)
                except Exception:
                    pass

    def _start_teammate_polling(self) -> None:
        """Start polling teammate progress every 0.5s."""
        if self._teammate_timer is not None:
            return
        self._teammate_timer = self.set_interval(0.5, self._tick_teammate_tree)

    def _stop_teammate_polling(self) -> None:
        """Stop the teammate progress polling timer."""
        if self._teammate_timer is not None:
            self._teammate_timer.stop()
            self._teammate_timer = None

    def _tick_teammate_tree(self) -> None:
        """Poll team_manager for teammate progress and update the tree widget."""
        if not hasattr(self, "team_manager") or self.team_manager is None:
            return
        if self._teammate_tree is None:
            return

        progress_list = self.team_manager.get_all_teammate_progress()

        if not progress_list:
            self._teammate_tree.display = False
            self._update_teammates_label(0)
            return

        # Update the reactive properties via mutate_reactive for list
        self._teammate_tree.teammates = list(progress_list)

        # Update leader tokens from main agent
        if self.agent:
            self._teammate_tree.leader_tokens = (
                self.agent.total_input_tokens + self.agent.total_output_tokens
            )

        self._teammate_tree.display = True
        active_count = sum(1 for p in progress_list if p.status == "running")
        self._update_teammates_label(active_count)

    def _update_teammates_label(self, count: int) -> None:
        """Update the teammates count in the status bar."""
        try:
            label = self.query_one("#teammates-label", Static)
            if count > 0:
                label.update(f"[cyan]● {count} teammate{'s' if count != 1 else ''}[/cyan]  ")
            else:
                label.update("")
        except Exception:
            pass

    async def _handle_permission_request(self, request: PermissionRequest) -> None:
        from lumo.permission_dialog import InlinePermissionWidget

        chat = self.query_one("#chat-area", VerticalScroll)
        widget = InlinePermissionWidget(request.tool_name, request.description)
        self._pending_perm_request = request
        await chat.mount(widget)
        self.call_after_refresh(chat.scroll_end, animate=False)
        # 权限提示弹窗期间禁用输入框
        try:
            self.query_one("#chat-input").disabled = True
        except Exception:
            pass

    def on_inline_permission_widget_responded(
        self, event: "InlinePermissionWidget.Responded"
    ) -> None:
        from lumo.permission_dialog import InlinePermissionWidget

        req = getattr(self, "_pending_perm_request", None)
        if req is not None:
            req.future.set_result(event.response)
            self._pending_perm_request = None
        # 从聊天区移除权限弹窗组件
        try:
            widget = self.query_one("#perm-inline", InlinePermissionWidget)
            widget.remove()
        except Exception:
            pass
        # 重新启用输入框
        try:
            self.query_one("#chat-input").disabled = False
            self.query_one("#chat-input").focus()
        except Exception:
            pass

    # -----------------------------------------------------------------
    # 恢复 session 的消息渲染
    # -----------------------------------------------------------------

    async def _render_restored_messages(self, messages: list[Message]) -> None:
        chat = self.query_one("#chat-area", VerticalScroll)
        await chat.remove_children()

        for msg in messages:
            if msg.tool_results or not msg.content:
                continue
            if msg.role == "user":
                row = Vertical(classes="user-row")
                await chat.mount(row)
                user_rich = RichText()
                user_rich.append("❯ ", style="bold color(80)")
                user_rich.append(msg.content, style="bold color(255)")
                bubble = Static(user_rich, classes="message user-message")
                await row.mount(bubble)
            elif msg.role == "assistant":
                row = Vertical(classes="ai-row")
                await chat.mount(row)
                md = Markdown(msg.content, classes="message ai-message")
                await row.mount(md)

        self.call_after_refresh(chat.scroll_end, animate=False)

    # -----------------------------------------------------------------
    # Session 摘要（异步后台生成）
    # -----------------------------------------------------------------

    async def _update_session_summary(self) -> None:
        if not self.session or not self.client or not self.agent:
            return
        try:
            summary = await generate_session_summary(
                self.client, self.conversation, self.agent.protocol
            )
            if summary:
                self.session.meta.summary = summary
                self.session.meta.save(
                    self.session._sessions_dir / f"{self.session.session_id}.meta"
                )
        except Exception:
            pass

    # -----------------------------------------------------------------
    # MCP
    # -----------------------------------------------------------------

    async def _init_mcp(self) -> None:
        self._mcp_connecting = True
        self._update_mode_label()
        tools_before = len(self.registry.list_tools())
        from lumo.runtime.capabilities import activate_external_capabilities
        try:
            active = await activate_external_capabilities(
                self.runtime_spec,
                self.registry,
                self.agent.work_dir if self.agent else os.getcwd(),
            )
        except Exception as exc:
            self._mcp_connecting = False
            self._update_mode_label()
            self._show_system_message(f"Runtime capability error: {exc}")
            return
        self.runtime_capabilities = active
        self.mcp_manager = active.mcp_manager
        connect_result = active.mcp_result or ConnectResult()
        self._mcp_connecting = False
        self._update_mode_label()
        for diagnostic in active.diagnostics:
            if diagnostic.severity != "info":
                self._show_system_message(
                    f"{diagnostic.severity.upper()} {diagnostic.code}: "
                    f"{diagnostic.message}"
                )
        for err in connect_result.errors:
            self._show_system_message(f"MCP warning: {err}")
        tools_after = len(self.registry.list_tools())
        mcp_tools = tools_after - tools_before
        server_count = len(connect_result.servers)
        if server_count > 0:
            self._mcp_server_info = (
                f"Connected to {server_count} MCP server(s), {mcp_tools} tools registered"
            )
        if server_count > 0 and mcp_tools > 0:
            # 构建 MCP 指令：对齐 Go 版，从 InitializeResult 提取 instructions
            parts = []
            for srv_info in connect_result.servers:
                section = f"## {srv_info.name}\n"
                # 优先使用服务器返回的 instructions
                if srv_info.instructions:
                    section += srv_info.instructions
                else:
                    # 回退：列出该服务器注册的工具名
                    tool_names = [
                        t.name for t in self.registry.list_tools()
                        if (
                            self.registry.origin(t.name) is not None
                            and self.registry.origin(t.name).kind == "mcp"
                            and self.registry.origin(t.name).provider_id == srv_info.name
                        )
                    ]
                    if tool_names:
                        section += "Available tools: " + ", ".join(tool_names)
                parts.append(section)
            self._mcp_instructions = (
                "# MCP Server Instructions\n\n"
                "The following MCP servers have provided instructions "
                "for how to use their tools and resources:\n\n"
                + "\n\n".join(parts)
            )

    async def _shutdown_mcp(self) -> None:
        if self._mcp_init_task is not None:
            self._mcp_init_task.cancel()
            try:
                await self._mcp_init_task
            except (asyncio.CancelledError, Exception):
                pass
            self._mcp_init_task = None
        if self.runtime_capabilities is not None:
            await self.runtime_capabilities.aclose()
            self.runtime_capabilities = None
            self.mcp_manager = None
        elif self.mcp_manager is not None:
            await self.mcp_manager.shutdown()
            self.mcp_manager = None

    # -----------------------------------------------------------------
    # 退出
    # -----------------------------------------------------------------

    async def action_handle_ctrl_c(self) -> None:
        if self._streaming:
            if self._agent_task and not self._agent_task.done():
                self._agent_task.cancel()
            self._show_system_message("(response interrupted)")
            self._finish_streaming()
            try:
                inp = self.query_one("#chat-input", ChatInput)
                inp.disabled = False
                inp.focus()
            except Exception:
                pass
            return

        if getattr(self, "_exit_requested", False):
            self.exit()
            return
        self._exit_requested = True

        async def _cleanup() -> None:
            tasks: list[asyncio.Task] = []

            if self.agent and self.agent.memory_manager:
                tasks.append(asyncio.create_task(
                    self.agent._extract_memories(self.conversation)
                ))
            if self.hook_engine:
                tasks.append(asyncio.create_task(
                    self.hook_engine.run_hooks(
                        "shutdown", HookContext(event_name="shutdown")
                    )
                ))
            tasks.append(asyncio.create_task(self._shutdown_mcp()))

            if tasks:
                await asyncio.wait(tasks, timeout=3.0)
                for t in tasks:
                    if not t.done():
                        t.cancel()

            if self._stale_cleanup_task and not self._stale_cleanup_task.done():
                self._stale_cleanup_task.cancel()

            if hasattr(self, 'team_manager'):
                for name in list(self.team_manager._teams):
                    try:
                        team = self.team_manager._teams[name]
                        for m in team.members:
                            team.set_member_active(m.name, False)
                        self.team_manager.delete_team(name)
                    except Exception:
                        pass

            if self.session:
                self.session.close()

        try:
            await _cleanup()
        except Exception:
            pass
        self.exit()

    def _show_error(self, text: str) -> None:
        chat = self.query_one("#chat-area", VerticalScroll)
        error_widget = Static(f"✖ {text}", classes="message error-message")
        chat.mount(error_widget)
        self.call_after_refresh(chat.scroll_end, animate=False)

    def _show_system_message(self, text: str) -> None:
        chat = self.query_one("#chat-area", VerticalScroll)
        msg = Static(f"  {text}", classes="message system-message")
        chat.mount(msg)
        self.call_after_refresh(chat.scroll_end, animate=False)

    def _show_mode_transition(
        self,
        previous: PermissionMode,
        current: PermissionMode,
    ) -> None:
        details = {
            PermissionMode.DEFAULT: (
                "Reads run automatically; edits and commands request approval."
            ),
            PermissionMode.ACCEPT_EDITS: (
                "File edits are approved; commands still request approval."
            ),
            PermissionMode.PLAN: (
                "Planning is read-only; only the active plan file may be changed."
            ),
            PermissionMode.BYPASS: (
                "Approvals are bypassed; dangerous-command guards remain active."
            ),
        }
        color = _MODE_COLORS.get(current, "white")
        self._show_system_message(
            "[bold]Permission mode changed[/]\n"
            f"[dim]{self._MODE_DISPLAY.get(previous, previous.value)}[/]  "
            f"→  [{color}]{self._MODE_DISPLAY.get(current, current.value)}[/{color}]\n"
            f"[dim]{details[current]}[/]"
        )

    def _show_runtime_ready(self, transition: dict[str, Any]) -> None:
        counts = (
            f"{transition.get('packs', 0)} packs  ·  "
            f"{transition.get('mcp', 0)} MCP  ·  "
            f"{transition.get('cli', 0)} CLI  ·  "
            f"{transition.get('plugins', 0)} plugins"
        )
        self._show_system_message(
            "[bold green]Agent Runtime ready[/]\n"
            f"[dim]{transition.get('from_name', transition.get('from_id', ''))}[/]  "
            f"→  [bold green]{transition.get('to_name', transition.get('to_id', ''))}[/]\n"
            f"[dim]{counts}[/]\n"
            "[dim]A new session is active; the previous session remains saved.[/]"
        )

    def _show_runtime_overview(self) -> None:
        scenario = self.runtime_spec.scenario
        counts = (
            f"{len(scenario.packs)} packs  ·  "
            f"{len(self.runtime_spec.mcp_servers)} MCP  ·  "
            f"{len(self.runtime_spec.cli_definitions)} CLI  ·  "
            f"{len(self.runtime_spec.plugin_descriptors)} plugins"
        )
        self._show_system_message(
            "[bold green]Agent Runtime ready[/]\n"
            f"[bold]{scenario.name}[/]  [dim]({scenario.id})[/]\n"
            f"[dim]{counts}[/]\n"
            f"[dim]Permission mode: {self.agent.permission_mode.value}[/]"
        )

    _MODE_DISPLAY = {
        PermissionMode.DEFAULT: "default",
        PermissionMode.ACCEPT_EDITS: "accept-edits",
        PermissionMode.PLAN: "plan",
        PermissionMode.BYPASS: "YOLO",
    }

    def _update_mode_label(self) -> None:
        if self.agent:
            perm = self.agent.permission_mode
            display = self._MODE_DISPLAY.get(perm, perm.value)
            color = _MODE_COLORS.get(perm, "dim")
            label = self.query_one("#mode-label", Static)
            label.update(
                f"[dim]mode:[/dim] [{color}]{display}[/{color}]"
            )
        try:
            model_label = self.query_one("#model-label", Static)
            model_text = self._selected_provider.model if self._selected_provider else ""
            if self._mcp_connecting:
                model_label.update(f"[yellow]MCP connecting…[/yellow]  {model_text}")
            else:
                model_label.update(model_text)
        except Exception:
            pass

    def _update_token_label(self, input_tokens: int, output_tokens: int) -> None:
        pass  # token 标签已从 UI 中移除
