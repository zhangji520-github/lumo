from __future__ import annotations

from lumo.runtime.models import (
    ScenarioDefinition,
    ScenarioExperience,
    ScenarioFeatures,
    ScenarioSecurity,
    SkillSelection,
)


CODING_SCENARIO = ScenarioDefinition(
    schema_version=1,
    id="coding",
    name="Coding",
    description="Lumo default coding agent runtime",
    experience=ScenarioExperience(
        role="AI programming assistant",
        mission=(
            "Help users complete software engineering work such as writing, "
            "debugging, refactoring, explaining, and validating code."
        ),
        default_tasks=(
            "Solve bugs and implement features",
            "Read and edit project files",
            "Run tests and development commands",
            "Explain code and architecture",
        ),
        output_contract=(
            "Verify implementation work before reporting completion",
            "Reference concrete files when discussing code",
        ),
    ),
    packs=(
        "core.session",
        "core.interaction",
        "core.skills",
        "coding.files",
        "coding.shell",
        "coding.worktree",
        "coding.agents",
        "coding.teams",
    ),
    skills=SkillSelection(include=("*",)),
    features=ScenarioFeatures(),
    security=ScenarioSecurity(),
    use_all_configured_mcp=True,
)


EMPTY_SCENARIO = ScenarioDefinition(
    schema_version=1,
    id="empty",
    name="Empty",
    description="Minimal Lumo runtime without coding capabilities",
    packs=("core.session", "core.interaction", "core.skills"),
    skills=SkillSelection(include=()),
    features=ScenarioFeatures(
        memory=True,
        worktree=False,
        subagents=False,
        teams=False,
        skill_install=False,
    ),
)


OFFICE_SCENARIO = ScenarioDefinition(
    schema_version=1,
    id="office",
    name="Office Base",
    description="Office assistant base runtime for MCP, CLI, plugins, and skills",
    persona=(
        "You are a careful office assistant. Explain the impact and obtain "
        "confirmation before sending, deleting, sharing, or creating external resources."
    ),
    experience=ScenarioExperience(
        role="office assistant",
        mission=(
            "Help users organize, analyze, and prepare office work using only "
            "the capabilities actually connected to this runtime."
        ),
        default_tasks=(
            "Organize and summarize user-provided information",
            "Prepare clear drafts, checklists, and structured reports",
        ),
        output_contract=(
            "Do not claim document, mail, calendar, or knowledge-base access "
            "unless Runtime Facts lists that capability",
            "Explain the impact before external writes or shared actions",
        ),
    ),
    packs=("core.session", "core.interaction", "core.skills"),
    skills=SkillSelection(include=()),
    features=ScenarioFeatures(
        memory=True,
        worktree=False,
        subagents=False,
        teams=False,
        skill_install=False,
    ),
)


BUILTIN_SCENARIOS = {
    scenario.id: scenario
    for scenario in (CODING_SCENARIO, OFFICE_SCENARIO, EMPTY_SCENARIO)
}
