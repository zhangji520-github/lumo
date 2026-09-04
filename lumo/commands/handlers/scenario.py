from __future__ import annotations

from dataclasses import replace

from lumo.commands.registry import Command, CommandContext, CommandType
from lumo.runtime.builtins import EMPTY_SCENARIO
from lumo.runtime.scenario_loader import ScenarioLoader


async def handle_scenario(ctx: CommandContext) -> None:
    loader: ScenarioLoader | None = ctx.config.get("scenario_loader")
    resolver = ctx.config.get("scenario_resolver")
    if loader is None or resolver is None:
        ctx.ui.add_system_message("Scenario system is not initialized.")
        return
    parts = ctx.args.strip().split()
    if not parts:
        show = ctx.config.get("show_scenario_manager")
        if show is not None:
            show()
        return
    sub = parts[0].lower()
    scenario_id = parts[1] if len(parts) > 1 else ""

    if sub == "create":
        description = ctx.args[len(parts[0]):].strip()
        if not description:
            ctx.ui.add_system_message(
                "用法: /scenario create <描述你需要的专属 Agent>"
            )
            return
        start_designer = ctx.config.get("start_scenario_designer")
        if start_designer is None:
            ctx.ui.add_system_message(
                "当前界面不支持交互式 Scenario Designer。"
            )
            return
        await start_designer(description)
        return

    if sub == "refine":
        if len(parts) < 3:
            ctx.ui.add_system_message(
                "用法: /scenario refine <id> <描述需要修改的内容>"
            )
            return
        refine = ctx.config.get("refine_scenario")
        if refine is None:
            ctx.ui.add_system_message("当前界面不支持 Scenario refinement。")
            return
        request = ctx.args.split(None, 2)[2]
        await refine(parts[1], request)
        return

    if sub == "list":
        lines = ["Scenarios:"]
        for record in loader.list():
            state = "healthy" if record.healthy else f"broken: {record.error}"
            name = record.definition.name if record.definition else record.id
            lines.append(f"  {record.id:<20} {name} [{record.source}, {state}]")
        ctx.ui.add_system_message("\n".join(lines))
        return

    if sub in {"show", "explain", "validate"}:
        if not scenario_id:
            ctx.ui.add_system_message(f"Usage: /scenario {sub} <id>")
            return
        try:
            spec = resolver.resolve(scenario_id)
        except Exception as exc:
            ctx.ui.add_system_message(f"Scenario invalid: {exc}")
            return
        if sub == "validate":
            ctx.ui.add_system_message(
                f"Scenario '{scenario_id}' is valid.\n"
                f"Fingerprint: {spec.fingerprint[:16]}\n"
                f"Diagnostics: {len(spec.diagnostics)}"
            )
        else:
            scenario = spec.scenario
            ctx.ui.add_system_message(
                f"{scenario.name} ({scenario.id})\n"
                f"Source: {spec.source}\n"
                f"Packs: {', '.join(scenario.packs) or 'none'}\n"
                f"MCP: {', '.join(item.name for item in spec.mcp_servers) or 'none'}\n"
                f"CLI: {', '.join(item.id for item in spec.cli_definitions) or 'none'}\n"
                f"Plugins: {', '.join(item.id for item in spec.plugin_descriptors) or 'none'}\n"
                f"Skills: {', '.join(scenario.skills.include) or 'none'}\n"
                f"Fingerprint: {spec.fingerprint[:16]}"
            )
        return

    if sub == "test":
        if not scenario_id:
            ctx.ui.add_system_message("Usage: /scenario test <id>")
            return
        test_scenario = ctx.config.get("test_scenario")
        if test_scenario is None:
            try:
                resolver.resolve(scenario_id)
                ctx.ui.add_system_message(
                    f"Scenario '{scenario_id}' passed static validation."
                )
            except Exception as exc:
                ctx.ui.add_system_message(f"Scenario test failed: {exc}")
        else:
            await test_scenario(scenario_id)
        return

    if sub == "use":
        if not scenario_id:
            ctx.ui.add_system_message("Usage: /scenario use <id>")
            return
        try:
            resolver.resolve(scenario_id)
        except Exception as exc:
            ctx.ui.add_system_message(f"Cannot use scenario: {exc}")
            return
        switch = ctx.config.get("switch_scenario")
        if switch is None:
            ctx.ui.add_system_message(
                f"Restart with: lumo --scenario {scenario_id}"
            )
        else:
            await switch(scenario_id)
        return

    if sub in {"new", "copy"}:
        if sub == "new":
            new_id = scenario_id
            source = EMPTY_SCENARIO
        else:
            if len(parts) < 3:
                ctx.ui.add_system_message("Usage: /scenario copy <source> <new-id>")
                return
            source_record = loader.get(parts[1])
            if source_record is None or source_record.definition is None:
                ctx.ui.add_system_message(f"Scenario not found: {parts[1]}")
                return
            source = source_record.definition
            new_id = parts[2]
        if not new_id:
            ctx.ui.add_system_message("Usage: /scenario new <id>")
            return
        definition = replace(source, id=new_id, name=new_id)
        edit = ctx.config.get("edit_scenario")
        if edit is not None:
            edit(definition, "user", False)
            return
        try:
            path = loader.save(definition)
        except Exception as exc:
            ctx.ui.add_system_message(f"Could not create scenario: {exc}")
            return
        ctx.ui.add_system_message(f"Scenario created: {path}")
        return

    if sub == "delete":
        if not scenario_id:
            ctx.ui.add_system_message("Usage: /scenario delete <id>")
        elif loader.delete(scenario_id):
            ctx.ui.add_system_message(f"Scenario deleted: {scenario_id}")
        else:
            ctx.ui.add_system_message(
                "Scenario was not found or is built-in and cannot be deleted."
            )
        return

    if sub == "edit":
        record = loader.get(scenario_id)
        if record is None:
            ctx.ui.add_system_message(f"Scenario not found: {scenario_id}")
        elif record.source == "builtin":
            ctx.ui.add_system_message(
                f"Built-in scenarios are read-only. Use /scenario copy {scenario_id} <new-id>."
            )
        else:
            edit = ctx.config.get("edit_scenario")
            if edit is not None and record.definition is not None:
                edit(record.definition, record.source, True)
            else:
                ctx.ui.add_system_message(f"Edit scenario file: {record.path}")
        return

    if sub == "reload":
        ctx.ui.add_system_message(f"Reloaded {len(loader.list())} scenarios.")
        return

    ctx.ui.add_system_message(
        "Usage: /scenario [create <description> | refine <id> <changes> | test <id> | explain <id> | list | show <id> | use <id> | new <id> | "
        "copy <source> <new-id> | edit <id> | validate <id> | delete <id> | reload]"
    )


SCENARIO_COMMAND = Command(
    name="scenario",
    description="Manage composable runtime scenarios",
    usage="/scenario [create | refine | test | explain | list | show | use | new | copy | edit | validate | delete | reload]",
    type=CommandType.LOCAL_UI,
    handler=handle_scenario,
)
