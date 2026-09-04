# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

from lumo.config import ConfigError, load_config
from lumo.hooks import HookConfigError, HookEngine, load_hooks
from lumo.paths import APP_DIR_NAME, migrate_legacy_data
from lumo.permissions import PermissionMode


def main() -> None:
    # Preserve the legacy directory, copy its missing data once, then write only
    # to .lumo from this point onward.
    migrate_legacy_data(Path.cwd())
    Path(APP_DIR_NAME).mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(message)s",
        filename=f"{APP_DIR_NAME}/debug.log",
        filemode="w",
    )

    parser = argparse.ArgumentParser(prog="lumo", description="Lumo AI coding assistant")
    parser.add_argument(
        "--mode",
        choices=[m.value for m in PermissionMode],
        default=None,
        help="Permission mode (overrides config.yaml)",
    )
    parser.add_argument(
        "-p",
        metavar="PROMPT",
        default=None,
        help="Run non-interactively: execute the prompt and print the result to stdout",
    )
    parser.add_argument(
        "--output-format",
        choices=["text", "stream-json"],
        default="text",
        help="Output format for -p mode: 'text' (default) prints final text, 'stream-json' emits NDJSON events",
    )
    parser.add_argument(
        "--remote",
        action="store_true",
        default=False,
        help="Start in remote mode: WebSocket server on 0.0.0.0:18888 with browser UI",
    )
    parser.add_argument(
        "--scenario",
        default=None,
        help="Scenario runtime to use (defaults to config default_scenario)",
    )
    args = parser.parse_args()

    try:
        config = load_config()
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    mode_str = args.mode if args.mode else config.permission_mode
    permission_mode = PermissionMode(mode_str)

    try:
        hooks = load_hooks(config.raw_hooks)
    except HookConfigError as e:
        print(f"Hook config error: {e}", file=sys.stderr)
        sys.exit(1)

    hook_engine = HookEngine(hooks) if hooks else None

    if args.p is not None:
        output_format = getattr(args, "output_format", "text")
        try:
            asyncio.run(_run_prompt(
                config,
                permission_mode,
                hook_engine,
                args.p,
                output_format,
                scenario_id=args.scenario,
            ))
        except Exception as exc:
            print(f"Runtime startup error: {exc}", file=sys.stderr)
            sys.exit(1)
        return

    # Remote 模式：启动 WebSocket 服务器，浏览器访问 http://localhost:18888
    if args.remote:
        from lumo.remote import RemoteServer

        server = RemoteServer(
            providers=config.providers,
            mcp_servers=config.mcp_servers,
            hook_engine=hook_engine,
            app_config=config,
            scenario_id=args.scenario,
        )
        try:
            asyncio.run(server.run())
        except Exception as exc:
            print(f"Runtime startup error: {exc}", file=sys.stderr)
            sys.exit(1)
        return

    from lumo.app import LumoApp
    from lumo.driver import NoAltScreenDriver

    selected_scenario = args.scenario
    startup_transition = None
    while True:
        try:
            app = LumoApp(
                providers=config.providers,
                permission_mode=permission_mode,
                mcp_servers=config.mcp_servers,
                hook_engine=hook_engine,
                enable_fork=config.enable_fork,
                enable_verification_agent=config.enable_verification_agent,
                worktree_config=config.worktree,
                teammate_mode=config.teammate_mode,
                enable_coordinator_mode=config.enable_coordinator_mode,
                driver_class=NoAltScreenDriver,
                sandbox_config=config.sandbox,
                app_config=config,
                scenario_id=selected_scenario,
                startup_transition=startup_transition,
            )
        except Exception as exc:
            print(f"Runtime startup error: {exc}", file=sys.stderr)
            sys.exit(1)
        result = app.run()
        if isinstance(result, dict) and isinstance(result.get("scenario_id"), str):
            selected_scenario = result["scenario_id"]
            startup_transition = result.get("transition")
            continue
        break


async def _run_prompt(
    config,
    permission_mode,
    hook_engine,
    prompt: str,
    output_format: str = "text",
    scenario_id: str | None = None,
) -> None:
    from lumo.agent import (
        Agent,
        CompactNotification,
        ErrorEvent,
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
    from lumo.client import resolve_context_window
    from lumo.conversation import ConversationManager
    from lumo.memory.instructions import load_instructions
    from lumo.runtime.builder import RuntimeBuilder
    from lumo.agents.loader import AgentLoader
    from lumo.agents.task_manager import TaskManager
    from lumo.agents.trace import TraceManager
    from lumo.tools.agent_tool import AgentTool
    from lumo.tools.impl.tool_search import ToolSearchTool
    from lumo.teams.manager import TeamManager
    from lumo.teams.models import BackendType
    from lumo.tools.team_create import TeamCreateTool
    from lumo.tools.team_delete import TeamDeleteTool
    from lumo.worktree import WorktreeManager
    from lumo.config import WorktreeConfig

    is_json = output_format == "stream-json"

    def emit_json(obj: dict) -> None:
        """输出一行 NDJSON 到 stdout"""
        print(json.dumps(obj, ensure_ascii=False), flush=True)

    runtime_builder = RuntimeBuilder(
        config,
        os.getcwd(),
        scenario_id=scenario_id,
        permission_mode=permission_mode,
    )
    spec = runtime_builder.resolve()
    provider = runtime_builder.select_provider(spec)
    # 第 2 层：尽力从 provider 自动拉取模型的 context window（缓存在 provider 上）。
    # 不会抛异常或阻塞启动；失败则退化到映射表。
    await resolve_context_window(provider)
    work_dir = os.getcwd()
    instructions = load_instructions(work_dir)
    foundation = await runtime_builder.build(provider=provider)
    client = foundation.client
    checker = foundation.permission_checker
    registry = foundation.registry
    registry.register(ToolSearchTool(registry, protocol=provider.protocol))
    load_skill_tool = None
    if (
        "core.skills" in foundation.spec.scenario.packs
        and foundation.skill_loader.get_catalog()
    ):
        from lumo.tools.load_skill import LoadSkill
        load_skill_tool = LoadSkill()
        registry.register(load_skill_tool)

    agent = Agent(
        client=client,
        registry=registry,
        protocol=provider.protocol,
        work_dir=work_dir,
        permission_checker=checker,
        context_window=provider.get_context_window(),
        instructions_content=instructions,
        hook_engine=hook_engine,
        runtime_persona=foundation.spec.scenario.persona,
        runtime_spec=foundation.spec,
        provider_name=provider.name,
        model_name=provider.model,
        surface="headless",
        runtime_diagnostics=foundation.capabilities.diagnostics,
    )
    if load_skill_tool is not None:
        load_skill_tool.set_loader(foundation.skill_loader)
        load_skill_tool.set_agent(agent)
        catalog = foundation.skill_loader.get_catalog()
        if catalog:
            lines = ["You can use the following Skills:", ""]
            lines.extend(f"- {name}: {description}" for name, description in catalog)
            lines.extend(["", "If the user's request matches a Skill, call LoadSkill to activate it."])
            agent.set_skill_catalog("\n".join(lines))

    wt_cfg = config.worktree or WorktreeConfig()
    wt_manager = WorktreeManager(
        repo_root=work_dir,
        symlink_directories=wt_cfg.symlink_directories,
    )
    trace_manager = TraceManager()
    task_manager = TaskManager()
    agent_loader = AgentLoader(work_dir, enable_verification=config.enable_verification_agent)
    agent_loader.load_all()
    team_manager = TeamManager(worktree_manager=wt_manager, trace_manager=trace_manager)

    agent_tool = AgentTool(
        agent_loader=agent_loader,
        task_manager=task_manager,
        trace_manager=trace_manager,
        parent_agent=agent,
        enable_fork=config.enable_fork,
        provider_config=provider,
        worktree_manager=wt_manager,
        team_manager=team_manager,
    )
    if foundation.spec.scenario.features.subagents:
        registry.register(agent_tool)
    if foundation.spec.scenario.features.teams:
        registry.register(TeamCreateTool(
            team_manager=team_manager,
            parent_agent=agent,
            teammate_mode="in-process",
            is_interactive=False,
            enable_coordinator_mode=config.enable_coordinator_mode,
        ))
        registry.register(TeamDeleteTool(team_manager=team_manager, parent_agent=agent))

    def drain_notifications() -> list[str]:
        notes: list[str] = []
        for t in task_manager.poll_completed():
            notes.append(
                f"<task-notification>\n<task_id>{t.id}</task_id>\n"
                f"<status>{t.status}</status>\n<result>{t.result}</result>\n"
                f"</task-notification>"
            )
        notes.extend(team_manager.drain_lead_mailbox())
        return notes

    def drain_mailbox_only() -> list[str]:
        return team_manager.drain_lead_mailbox()

    agent.notification_fn = drain_mailbox_only

    # 使用事件驱动的 agent.run()，支持 text 和 stream-json 两种输出格式
    conv = ConversationManager()
    if foundation.capabilities.mcp_result:
        instructions_parts = [
            f"## {item.name}\n{item.instructions}"
            for item in foundation.capabilities.mcp_result.servers
            if item.instructions
        ]
        if instructions_parts:
            conv.add_system_reminder(
                "# MCP Server Instructions\n\n" + "\n\n".join(instructions_parts)
            )
    conv.add_user_message(prompt)

    start = time.monotonic()
    text_buf = ""
    total_input = 0
    total_output = 0
    tool_calls: list[dict] = []

    async for event in agent.run(conv):
        if isinstance(event, StreamText):
            text_buf += event.text
            if is_json:
                emit_json({"type": "assistant", "text": event.text})

        elif isinstance(event, ThinkingText):
            if is_json:
                emit_json({"type": "thinking", "text": event.text})

        elif isinstance(event, ToolUseEvent):
            tool_calls.append({"name": event.tool_name, "is_error": False})
            if is_json:
                emit_json({
                    "type": "tool_use",
                    "tool_name": event.tool_name,
                    "tool_id": event.tool_id,
                    "args": event.arguments,
                })

        elif isinstance(event, ToolResultEvent):
            # 回填最后一个同名 tool_call 的 is_error
            if tool_calls:
                tool_calls[-1]["is_error"] = event.is_error
            if is_json:
                emit_json({
                    "type": "tool_result",
                    "tool_name": event.tool_name,
                    "tool_id": event.tool_id,
                    "output": event.output,
                    "is_error": event.is_error,
                    "elapsed": round(event.elapsed, 3),
                })

        elif isinstance(event, UsageEvent):
            total_input = event.input_tokens
            total_output = event.output_tokens
            if is_json:
                emit_json({
                    "type": "usage",
                    "input_tokens": event.input_tokens,
                    "output_tokens": event.output_tokens,
                })

        elif isinstance(event, TurnComplete):
            if is_json:
                emit_json({"type": "turn_complete", "turn": event.turn})

        elif isinstance(event, LoopComplete):
            # 最终结果：stream-json 输出 result 行，text 模式直接打印文本
            elapsed_ms = int((time.monotonic() - start) * 1000)
            if is_json:
                emit_json({
                    "type": "result",
                    "result": text_buf,
                    "duration_ms": elapsed_ms,
                    "num_turns": event.total_turns,
                    "tool_calls": tool_calls,
                    "usage": {
                        "input_tokens": total_input,
                        "output_tokens": total_output,
                    },
                    "stop_reason": "end_turn",
                })
            else:
                print(text_buf, end="", flush=True)
            break

        elif isinstance(event, ErrorEvent):
            if is_json:
                emit_json({"type": "error", "message": event.message})
            else:
                print(f"Error: {event.message}", file=sys.stderr, flush=True)

        elif isinstance(event, CompactNotification):
            if is_json:
                emit_json({"type": "compact", "message": event.message})

        elif isinstance(event, RetryEvent):
            if is_json:
                emit_json({"type": "retry", "reason": event.reason})

        elif isinstance(event, PermissionRequest):
            # -p 非交互模式：自动批准所有权限请求
            event.future.set_result(PermissionResponse.ALLOW)

    # 如果有 team 在运行，轮询等待 teammate 完成
    if not team_manager._teams:
        await foundation.aclose()
        return

    for i in range(90):
        await asyncio.sleep(2)
        running = {k: not t.done() for k, t in task_manager._async_tasks.items()}
        completed_ids = [t.id for t in task_manager._tasks.values() if t.status != "running"]
        print(f"[poll {i}] running={running} completed={completed_ids} teams={list(team_manager._teams.keys())} queue_size={task_manager._notify_queue.qsize()}", file=sys.stderr, flush=True)
        notes = drain_notifications()
        if not notes:
            has_running = any(v for v in running.values())
            if not has_running:
                print(f"[poll {i}] no running tasks, breaking", file=sys.stderr, flush=True)
                break
            continue
        for note in notes:
            conv.add_system_reminder(note)
        # 后续 team 轮询仍用 run_to_completion，避免重复事件循环
        last_result = await agent.run_to_completion(
            "Teammate notifications received. Process them and continue.", conv
        )
        if is_json:
            emit_json({"type": "assistant", "text": last_result})
        else:
            print(last_result, flush=True)

    await foundation.aclose()


if __name__ == "__main__":
    main()
