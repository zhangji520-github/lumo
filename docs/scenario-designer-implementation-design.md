# Lumo Scenario Designer 实现设计

状态：实现设计提案  
范围：场景包格式、Prompt Assembly、模型辅助创作、TUI、验证、安全、持久化与迁移  
产品依据：[专属场景设计器：产品与架构方案](scenario-designer-product-architecture.md)  
基础设施依据：[可组合场景 Runtime 设计](scenario-runtime-design.md)

## 1. 实现目标

在现有 ScenarioResolver、RuntimeSpec、RuntimeBuilder、CapabilityCatalog 和 TUI 场景管理能力上增加完整的 Experience Composition 与模型辅助 Scenario Designer。

完成后应支持：

- 用户通过自然语言创建专属场景。
- 模型提出必要的澄清问题。
- 模型基于真实 Capability Catalog 生成结构化 Proposal。
- Lumo 确定性生成 `scenario.yaml` 和 `prompt.md`。
- 用户在保存前查看有效 Runtime、风险和文件 Diff。
- 场景 Prompt、Tool Schema、能力自述和 TUI 保持一致。
- 当前 Runtime 在 Candidate Runtime 验证成功前保持运行。

## 2. 当前实现与差距

当前已经具备：

- `ScenarioDefinition`、`ScenarioRecord`、`RuntimeSpec`。
- 内置、用户级和项目级场景发现。
- Capability Pack、MCP、CLI、Plugin、Skill 解析。
- Runtime fingerprint 和 Session 快照。
- TUI 场景选择、复制、能力勾选和切换确认。
- Headless、Remote、TUI 共用 RuntimeBuilder 基础装配。

当前主要差距：

1. `IDENTITY_SECTION` 固定声明 Lumo 是 programming assistant。
2. `DOING_TASKS_SECTION` 固定假设用户主要提出软件工程任务。
3. `USING_TOOLS_SECTION` 固定描述 Coding 工具，即使当前 Runtime 不具备这些工具。
4. Scenario 只有一段 `persona`，无法结构化表达身份、工作流和输出契约。
5. 场景仍以单个 YAML 为主，Prompt 不能独立审阅和维护。
6. TUI 编辑器只能勾选能力，不能自然语言生成和修改场景。
7. 模型看不到可信的 Provider、Model 和有效能力事实。
8. Memory 未定义场景 namespace。

## 3. 目标组件架构

```text
                               CapabilityCatalog
                                      │
User request ──> ScenarioDesigner ─────┤
                    │                  │
                    ↓                  │
             ScenarioProposal          │
                    ↓                  │
             ProposalCompiler <────────┘
                    ↓
              ScenarioDraft
                    ↓
          ScenarioValidationPipeline
                    ↓
             EffectivePreview
                    ↓ user approval
           ScenarioPackageWriter
                    ↓
         ScenarioLoader / Resolver
                    ↓
                RuntimeSpec
                    ↓
              RuntimeBuilder
                    ↓
        PromptAssembler + ToolRegistry
                    ↓
                   Agent
```

新增模块建议：

```text
lumo/runtime/
├── prompt_models.py
├── prompt_registry.py
├── prompt_assembler.py
├── runtime_facts.py
└── scenario_package.py

lumo/scenario_designer/
├── __init__.py
├── models.py
├── service.py
├── authoring_runtime.py
├── proposal_compiler.py
├── validation.py
├── prompt_lint.py
├── diff.py
└── writer.py

lumo/tui/
├── scenario_designer_screen.py
├── scenario_review_view.py
└── scenario_diagnostics_view.py
```

如果暂不重组现有 TUI 文件，可以先将三个 Screen 放在 `lumo/`，但领域逻辑不得放入 Textual Widget。

## 4. Scenario Package 格式

### 4.1 目录结构

新场景采用目录形式：

```text
<scenario-root>/<scenario-id>/
├── scenario.yaml
├── prompt.md
└── examples/             # 可选，后续用于测试和说明
```

发现位置：

```text
<project>/.lumo/scenarios/<id>/scenario.yaml
~/.lumo/scenarios/<id>/scenario.yaml
lumo/scenarios/builtin/<id>/scenario.yaml
```

优先级继续保持：项目级 > 用户级 > 内置。

同一个 root 中同时存在 `<id>.yaml` 和 `<id>/scenario.yaml` 时，将该 ID 标记为 broken，拒绝静默选择，避免用户编辑一个文件而运行另一个文件。

### 4.2 兼容现有单文件场景

现有 `<id>.yaml` 继续读取：

- `persona` 转换为一个兼容的 Scenario Experience section。
- `prompt_file` 为空。
- 保存已有单文件场景时默认保持原格式，除非用户明确执行“转换为场景包”。
- 新建场景默认使用目录格式。

### 4.3 Manifest 示例

```yaml
schema_version: 2
id: contract-review
name: 合同审核
description: 只读审核合同并输出风险和修改建议
prompt_file: prompt.md

provider: inherit

experience:
  role: 企业合同审核助理
  mission: 识别合同风险并给出可追溯的修改建议
  audience: 法务、采购和业务负责人
  default_tasks:
    - 审核合同条款
    - 比较合同版本
    - 生成风险清单
  output_contract:
    - 引用原文位置
    - 按高、中、低风险排序
    - 区分事实、判断和建议

capabilities:
  packs:
    - core.session
    - core.interaction
    - core.skills
  mcp:
    - id: company-knowledge
      required: true
      tools:
        include: [search_*, get_*]
        exclude: []
      risk_overrides:
        search_*: read
        get_*: read
  cli: []
  plugins: []
  skills:
    include:
      - contract-review
    exclude: []

features:
  memory: true
  memory_scope: scenario
  worktree: false
  subagents: false
  teams: false
  skill_install: false

security:
  permission_mode: default
  sandbox: inherit
  confirm_tools: []
  deny_tools:
    - "*delete*"
    - "*update*"

startup:
  optional_capability_failure: warn
```

### 4.4 `prompt.md`

`prompt.md` 只保存领域体验，不复制 Lumo 核心协议和安全规则：

```markdown
# Workflow

1. 确认合同主体、类型和用户关注点。
2. 检查责任、付款、违约、终止、知识产权和争议解决条款。
3. 对每项风险引用原文并说明判断依据。
4. 提供建议文本，但不得修改原合同。

# Domain constraints

- 不把一般建议表述为正式法律意见。
- 缺少上下文时明确说明假设。
- 不虚构法规、公司制度或合同条款。
```

约束：

- 文件必须位于场景目录内。
- 默认最大 20 KiB。
- UTF-8。
- 不允许软链接逃逸场景目录。
- Prompt hash 进入 Runtime fingerprint。

## 5. 数据模型

### 5.1 Scenario Experience

```python
@dataclass(frozen=True)
class ScenarioExperience:
    role: str
    mission: str
    audience: str = ""
    default_tasks: tuple[str, ...] = ()
    output_contract: tuple[str, ...] = ()
    prompt_file: str | None = None
    prompt_text: str = ""
```

`ScenarioDefinition` 增加 `experience`，旧 `persona` 保留读取兼容，但解析后统一映射到 Experience。

### 5.2 Memory Scope

```python
MemoryScope = Literal["scenario", "workspace", "shared"]

@dataclass(frozen=True)
class ScenarioFeatures:
    memory: bool = True
    memory_scope: MemoryScope = "scenario"
    ...
```

默认 `scenario`。`shared` 必须由用户显式选择。

### 5.3 Scenario Proposal

```python
@dataclass(frozen=True)
class ScenarioIntent:
    summary: str
    audience: str = ""
    requested_actions: tuple[str, ...] = ()
    forbidden_actions: tuple[str, ...] = ()
    data_sources: tuple[str, ...] = ()
    output_preferences: tuple[str, ...] = ()

@dataclass(frozen=True)
class ProposedCapability:
    id: str
    kind: CapabilityKind
    reason: str
    required: bool

@dataclass(frozen=True)
class ScenarioProposal:
    scenario_id: str
    name: str
    description: str
    experience: ScenarioExperience
    capabilities: tuple[ProposedCapability, ...]
    deny_actions: tuple[str, ...]
    confirm_actions: tuple[str, ...]
    feature_preferences: Mapping[str, bool | str]
    prompt_draft: str
    unresolved_questions: tuple[str, ...] = ()
```

Proposal 不能携带原始 API Key、Python import path、Shell command 或最终 Tool risk category。

### 5.4 Scenario Patch

Refine 使用领域 Patch，而不是任意 JSON Patch：

```python
@dataclass(frozen=True)
class ScenarioPatch:
    identity_changes: Mapping[str, str]
    add_capabilities: tuple[ProposedCapability, ...]
    remove_capability_ids: tuple[str, ...]
    add_confirm_actions: tuple[str, ...]
    add_deny_actions: tuple[str, ...]
    remove_policy_actions: tuple[str, ...]
    prompt_replacement: str | None = None
```

Patch 由 ProposalCompiler 应用到当前解析模型，模型不能直接修改 YAML 路径。

## 6. Prompt Assembly 重构

### 6.1 目标

最终 Prompt 必须从有效 RuntimeSpec 生成，并与 ToolRegistry 同步。不能再无条件添加 Coding 身份和工具说明。

### 6.2 Prompt Contribution

```python
PromptTrust = Literal["kernel", "runtime", "scenario", "capability", "project"]

@dataclass(frozen=True)
class PromptContribution:
    id: str
    order: int
    text: str
    trust: PromptTrust
    source: str
```

注册规则：

- 同一 Runtime 中 ID 唯一。
- Kernel contribution 不能被 Scenario 覆盖。
- 空文本不输出。
- Capability contribution 只有在对应能力有效注册后才能输出。
- Prompt fingerprint 使用规范化后的 contribution ID、顺序和文本。

### 6.3 顺序与责任

```text
-100  kernel:identity          中性的 Lumo 身份
 -90  kernel:safety            不可覆盖的通用安全规则
 -80  kernel:tool-protocol     Tool call、结果与 Prompt injection 规则
 -50  runtime:facts            Scenario、Provider、Model、能力事实
   0  scenario:identity        Role、Mission、Audience
  10  scenario:workflow        prompt.md 和默认任务
 100  capability:<id>          仅实际启用能力的指导
 200  project:instructions     项目指令
 300  skills:catalog           有效 Skill catalog
```

### 6.4 Neutral Kernel 迁移

现有 Prompt 的处理方式：

| 当前 section | 目标归属 |
|---|---|
| Identity | 改写为中性 `kernel:identity` |
| System | 保留为 Kernel，删除场景假设 |
| DoingTasks | Coding 场景 Experience |
| ExecutingActions | 保留为 `kernel:safety` |
| UsingTools | 拆分给各 Capability Pack |
| ToneStyle | 保留为 Kernel |
| TextOutput | 保留为 Kernel |
| Environment | 改为中性 Runtime context |

中性身份示例：

```text
You are Lumo, an AI agent running in the current Lumo Runtime.
Follow the active scenario, use only capabilities present in this runtime,
and do not claim capabilities that are not listed in Runtime Facts.
```

### 6.5 Runtime Facts

```python
@dataclass(frozen=True)
class RuntimeFacts:
    scenario_id: str
    scenario_name: str
    provider_name: str
    model_name: str
    protocol: str
    capabilities: tuple[RuntimeCapabilityFact, ...]
    degraded_capabilities: tuple[str, ...]
    work_dir: str
    surface: Literal["tui", "headless", "remote"]
```

模型名称必须表述为“configured model”，因为代理 Provider 可能在服务端改写模型。模型被问及身份时应回答：

```text
我是 Lumo 当前场景中的 <role>，配置 Provider 为 <provider_name>，
配置模型为 <model_name>。
```

禁止根据模型训练先验猜测 Anthropic、OpenAI 或其他供应商。

### 6.6 Capability Guidance

Capability Pack 和外部 Provider 可以提供 Prompt contribution：

```python
class CapabilityContribution(Protocol):
    def tools(self) -> Iterable[Tool]: ...
    def prompt_sections(self) -> Iterable[PromptContribution]: ...
```

装配顺序：

1. Provider 生成候选 Tool 与 Prompt。
2. Scenario include/exclude 过滤 Tool。
3. Tool 成功注册到 ToolRegistry。
4. 只保留与有效 Tool 关联的 Prompt contribution。
5. PromptAssembler 与 Tool Schema 使用同一个 resolved tool view。

Prompt 不得通过工具名字符串猜测能力状态。

## 7. Scenario Designer Authoring Runtime

### 7.1 独立于目标场景

Designer 使用 Lumo 内置的受限 Authoring Runtime。即使当前场景是 Empty 或 Office，Designer 仍然可用，但它不能继承当前场景的 Bash、Plugin 工具或外部写权限。

### 7.2 模型可见工具

```text
ListCapabilities
GetCapabilityDetails
ReadScenarioDraft
ReadScenarioEffectiveSpec
SubmitScenarioProposal
SubmitScenarioPatch
RequestScenarioClarification
```

模型不可见：

- WriteFile。
- Bash。
- Plugin 安装。
- MCP 认证。
- 场景保存。
- Runtime 启动。

保存、探测和启动由宿主根据用户确认执行。

### 7.3 Structured submission

模型必须通过 `SubmitScenarioProposal` 或 `SubmitScenarioPatch` Tool 提交结构化结果。普通文本只用于向用户解释和提问，不能直接成为配置来源。

Tool Schema 使用 Pydantic 生成并验证：

- 字段长度。
- ID 格式。
- 列表数量。
- Prompt 大小。
- Capability kind。
- 禁止字段。

### 7.4 Authoring 状态机

```text
IDLE
  ↓ begin
DESCRIBING
  ↓
CLARIFYING ←────────────┐
  ↓ answers             │
COMPOSING               │
  ↓ proposal            │
VALIDATING ──needs info─┘
  ↓
REVIEWING
  ├── revise ──> COMPOSING
  ├── cancel ──> CANCELLED
  └── approve
          ↓
SAVING
  ↓
BUILDING_CANDIDATE
  ├── failed ──> REVIEWING
  └── ready ──> COMPLETE
```

每个状态都有结构化数据，不从 TUI 文本反向解析状态。

## 8. Proposal Compiler

ProposalCompiler 是模型输出与 ScenarioDefinition 之间的确定性边界。

职责：

1. 规范化 Scenario ID。
2. 将业务描述映射到真实 CapabilityDescriptor。
3. 拒绝未知或不可用能力作为“已启用”。
4. 补齐 Pack 与 Plugin 依赖。
5. 保留 required/optional 语义。
6. 将自然语言动作映射为 Tool pattern。
7. 采用 Provider 声明的最低风险分类。
8. 生成 ScenarioExperience。
9. 生成 Prompt draft。
10. 输出诊断和未解决问题。

编译结果：

```python
@dataclass(frozen=True)
class ScenarioDraft:
    definition: ScenarioDefinition
    prompt_text: str
    matched_capabilities: tuple[CapabilityDescriptor, ...]
    missing_capabilities: tuple[RequestedCapability, ...]
    diagnostics: tuple[RuntimeDiagnostic, ...]
```

## 9. Validation Pipeline

### 9.1 层级

```text
Schema validation
    ↓
Path and containment validation
    ↓
Capability resolution
    ↓
Dependency validation
    ↓
Security policy validation
    ↓
Prompt lint
    ↓
Secret scan
    ↓
Optional environment probe
    ↓
Candidate Runtime build
```

### 9.2 Schema 与路径

- ID 满足 `[a-z0-9][a-z0-9-]*`。
- `prompt_file` 必须是场景目录内相对路径。
- 禁止 `..`、绝对路径和 symlink escape。
- 同 root 重复 ID 失败。
- 文件大小和列表数量有上限。

### 9.3 安全策略

- Scenario 不能启用 bypass。
- 普通 Scenario 只能收紧 Sandbox。
- Tool risk 不能从 write/command 降为 read。
- 外部写能力必须能映射到 confirm 或明确 allow。
- Python Plugin 必须显示安装来源和版本。

### 9.4 Prompt lint

Prompt lint 不尝试理解全部自然语言，只检查可验证事实：

- 提及未启用的已知 Tool 名称。
- 声称拥有缺失 Capability。
- 包含 Provider Secret。
- 包含覆盖 Kernel 的典型指令。
- 超过大小限制。
- 引用了不存在的 Prompt variable。

覆盖 Kernel 的文本默认 warning；明显的 Secret 和路径逃逸是 error。

### 9.5 Probe 等级

```python
ProbeLevel = Literal["static", "local", "external"]
```

- `static`：不产生外部效果，只解析文件和依赖。
- `local`：检查 executable、Plugin import 和本地路径。
- `external`：连接 MCP 或远程服务，必须经用户确认。

## 10. 持久化与事务

### 10.1 Writer

`ScenarioPackageWriter` 是唯一正式写入入口：

```python
class ScenarioPackageWriter:
    def prepare(self, draft: ScenarioDraft, scope: ScenarioScope) -> PreparedWrite: ...
    def commit(self, prepared: PreparedWrite) -> ScenarioPackage: ...
```

`prepare()`：

- 生成规范化 YAML。
- 生成 `prompt.md`。
- 计算 Diff。
- 再次校验 containment。
- 写入同目录临时目录。
- fsync 文件和目录。

`commit()`：

- 新建不覆盖现有目录。
- 更新使用原子目录替换或逐文件原子替换。
- 失败删除临时目录。
- Windows 上使用同卷临时目录，避免跨卷替换失败。

### 10.2 模型不能调用 commit

模型提交 Proposal 后，宿主展示 Review。只有用户确认事件可以调用 `commit()`。

### 10.3 Diff

Review 展示：

- Manifest 字段变化。
- Capability 增删。
- 权限变化。
- Prompt unified diff。
- Runtime fingerprint 变化。
- Session 与 Memory 影响。

## 11. TUI 实现

### 11.1 Scenario Designer Screen

使用独立全屏 Screen，不嵌套在聊天消息卡片中：

```text
┌ Scenario Designer ───────────────────────────────────────────────┐
│ Describe │ Clarify │ Compose │ Validate │ Review                │
├───────────────────────────────┬──────────────────────────────────┤
│ Conversation                  │ Effective Runtime                │
│                               │                                  │
│ 创建一个合同审核助手……       │ Identity                         │
│                               │   企业合同审核助理               │
│ Designer: 是否允许生成报告？  │                                  │
│                               │ Capabilities                     │
│ > 允许新建报告，不改原合同    │   ✓ document.read                │
│                               │   ✓ company-knowledge            │
│                               │   ✗ document.update              │
│                               │                                  │
│                               │ Risk: read-only + report write   │
├───────────────────────────────┴──────────────────────────────────┤
│ Esc Cancel   Tab Next   Ctrl+V Validate   Ctrl+S Review          │
└──────────────────────────────────────────────────────────────────┘
```

布局原则：

- 左侧负责自然语言交互。
- 右侧始终显示确定性解析结果。
- Error、warning、missing capability 使用不同颜色和固定位置。
- 最长 ID 和错误文本支持换行，不撑破布局。
- 动态状态不改变工具栏尺寸。

### 11.2 Review Screen

Review 至少包含：

- Identity 与 Mission。
- Effective Capability 列表。
- Missing Capability。
- 外部进程和连接。
- Read、write、external 权限摘要。
- Prompt 预览。
- 文件 Diff。
- 保存范围。
- Probe 等级。

主操作：

```text
Save and start
Save only
Revise
Cancel
```

### 11.3 现有命令扩展

```text
/scenario create
/scenario create --from office
/scenario refine <id>
/scenario explain <id>
/scenario test <id> [--probe static|local|external]
/scenario convert <legacy-id>
```

现有 `new`、`copy`、`edit` 保留，适合高级用户的确定性操作。

### 11.4 Headless

```text
lumo scenario create --description "..." --output json
lumo scenario validate <id> --probe static
lumo scenario explain <id> --output json
```

Headless 创建如果需要澄清，返回结构化 `needs_input`，不在非交互终端中猜测答案。

## 12. Candidate Runtime

正式切换前构建 Candidate Runtime：

```python
candidate = await RuntimeBuilder(...).build_candidate(spec)
```

Candidate 特性：

- 不发布给当前 UI 或 Agent。
- 拥有独立生命周期清理栈。
- 可以完成 Prompt Assembly 和 Tool Schema 生成。
- 根据 Probe 等级决定是否连接外部能力。
- 成功后才能替换当前 Runtime。
- 失败时完整关闭，当前 Runtime 不受影响。

Candidate Preview 输出：

```python
@dataclass(frozen=True)
class RuntimePreview:
    prompt_sections: tuple[AssembledPromptSection, ...]
    tools: tuple[ToolPreview, ...]
    external_processes: tuple[ProcessPreview, ...]
    permissions: PermissionSummary
    diagnostics: tuple[RuntimeDiagnostic, ...]
    fingerprint: str
```

## 13. Session 与 Memory

### 13.1 Session

SessionMeta 继续保存：

- `scenario_id`。
- `runtime_fingerprint`。
- `runtime_snapshot_file`。

增加：

- `prompt_fingerprint`。
- `memory_namespace`。
- `scenario_package_source`。

### 13.2 Memory namespace

```text
.lumo/memory/
├── shared/
├── workspace/
└── scenarios/
    ├── coding/
    └── contract-review/
```

读取顺序由 `memory_scope` 决定。默认 Scenario 只读取：

- shared 中明确标记为共享的事实。
- 当前 scenario namespace。

不能无条件读取其他场景的 Persona、偏好和任务摘要。

### 13.3 恢复

恢复顺序：

1. 读取 Session Runtime snapshot。
2. 定位对应 Scenario Package 或 snapshot 内联体验。
3. 校验 Prompt fingerprint。
4. 构建 Candidate Runtime。
5. 缺少 required 能力时拒绝恢复并显示修复建议。

## 14. Built-in 场景迁移

### 14.1 Coding

- 当前 Identity 与 DoingTasks 移入 Coding Experience。
- 文件工具指导移入 `coding.files`。
- Bash 指导移入 `coding.shell`。
- Agent/Team 指导移入相应 Pack。

### 14.2 Office

- 显示名称建议改为 `Office Base`，直到接入真实办公能力。
- Prompt 明确“不宣称未连接的文档、邮件和日历能力”。
- 默认不包含 Coding Skill `*`，改为显式选择或空列表。
- 默认 Memory scope 为 scenario。

### 14.3 Empty

- 使用 Neutral Kernel。
- 不贡献领域任务预期。
- 能力自述只显示通用交互和实际 Skill。

## 15. 错误模型

扩展 RuntimeDiagnostic：

```python
@dataclass(frozen=True)
class RuntimeDiagnostic:
    severity: Literal["info", "warning", "error"]
    code: str
    message: str
    source: DiagnosticSource
    path: str | None = None
    capability_id: str | None = None
    hint: str = ""
```

Designer 常见 code：

```text
scenario-question-required
scenario-id-conflict
capability-not-found
capability-unavailable
capability-risk-downgrade
plugin-install-required
prompt-mentions-disabled-tool
prompt-secret-detected
prompt-too-large
memory-scope-expansion
candidate-build-failed
```

错误信息不得包含 Secret、完整 Header 或插件内部敏感配置。

## 16. 测试设计

### 16.1 Prompt Assembly

- Neutral Kernel 不包含 programming assistant。
- Office Prompt 不包含 Coding 默认任务。
- 未启用 Bash 时 Prompt 不出现 Bash 指导。
- Coding Runtime 继续获得完整 Coding 指导。
- Runtime Facts 使用配置 Provider 与 Model。
- ToolRegistry 与 Capability Guidance 一致。
- 重复 section ID 失败。
- Prompt fingerprint 稳定。

### 16.2 Proposal Compiler

- 自然语言 Proposal 只能引用 Catalog 能力。
- 未知能力进入 missing，而非 enabled。
- Pack/Plugin 依赖正确补齐。
- 写能力不能降级为 read。
- bypass 被拒绝。
- ScenarioPatch 不删除未请求字段。

### 16.3 Package

- 目录场景发现优先级正确。
- 同 root 单文件/目录冲突标记 broken。
- Prompt containment、大小、编码和 symlink 校验。
- 原子创建、更新和失败回滚。
- Secret 不写入 YAML、Prompt、Diff 或 snapshot。

### 16.4 Designer

- 缺少关键回答时进入 CLARIFYING。
- 模型普通文本不能触发保存。
- 只有 typed Proposal 被编译。
- 用户取消不产生文件。
- 用户确认后才 commit。
- Candidate 失败回到 REVIEWING。

### 16.5 TUI

- 五步状态导航。
- 长 ID、长诊断和窄终端布局。
- Capability 状态与风险颜色。
- Review Diff。
- Save and start 切换到新 Session。
- 切换完成后身份、状态栏和模型自述一致。

### 16.6 关键端到端用例

1. 创建只读合同审核场景。
2. 缺少知识库 MCP 时保存为有 warning 的场景。
3. 配置 MCP 后重新验证并启动。
4. 询问“你是谁、你能做什么”。
5. 验证回答只声明实际能力与配置模型。
6. 切回 Coding，验证身份与工具指导改变。
7. 恢复旧 Office Session，验证身份不串场。

## 17. 代码改动清单

### 17.1 新增

- `lumo/scenario_designer/*`
- `lumo/runtime/prompt_models.py`
- `lumo/runtime/prompt_registry.py`
- `lumo/runtime/prompt_assembler.py`
- `lumo/runtime/runtime_facts.py`
- `lumo/runtime/scenario_package.py`
- `lumo/tui/scenario_designer_screen.py`
- `lumo/tui/scenario_review_view.py`

### 17.2 修改

- `lumo/prompts.py`：迁移为 PromptAssembler 兼容层。
- `lumo/runtime/models.py`：Experience、Memory scope、Prompt fingerprint。
- `lumo/runtime/scenario_loader.py`：目录场景包发现。
- `lumo/runtime/resolver.py`：Prompt 和 Experience 解析。
- `lumo/runtime/builder.py`：Prompt contribution 与 Candidate Runtime。
- `lumo/runtime/packs.py`：Capability Guidance。
- `lumo/agent.py`：接收 PromptAssembly，不感知场景文件。
- `lumo/memory/*`：Memory namespace。
- `lumo/commands/handlers/scenario.py`：Designer 命令。
- `lumo/app.py`：Designer Screen、Review、Runtime 交接。
- `lumo/remote.py`：Runtime Facts 与 Designer Remote 状态。

## 18. 实现顺序

### 18.1 Experience 基础

- 引入 Neutral Kernel。
- 引入 PromptContribution 和 PromptAssembler。
- 将 Coding 文本移动到 Coding 场景与 Pack。
- 注入 Runtime Facts。
- 补齐 Prompt/Tool 一致性测试。

### 18.2 Scenario Package

- 支持目录格式与 `prompt.md`。
- 增加 Experience 与 Memory scope。
- 实现兼容读取和原子 Writer。

### 18.3 Designer 领域层

- Proposal、Patch、Compiler、Validation。
- 受限 Authoring Runtime。
- Structured submission tools。

### 18.4 TUI 与命令

- Create、Refine、Explain、Test。
- Compose、Validate、Review 视图。
- Diff、Probe 和 Candidate Preview。

### 18.5 状态隔离与完整验证

- Memory namespace。
- Session Prompt fingerprint。
- Office/Coding 端到端身份一致性。
- Headless 与 Remote 对齐。

每一步都保持默认 Coding Runtime 可运行，并在进入下一步前通过全量测试。

## 19. 完成标准

实现完成必须同时满足：

1. 用户能通过自然语言创建并保存场景包。
2. 模型不能直接写最终配置。
3. 最终配置只引用真实 Capability。
4. Neutral Kernel 不包含 Coding 身份。
5. Office 与 Coding 获得不同且一致的 Experience。
6. Provider、Model 和 Capability 自述来自 Runtime Facts。
7. Prompt 中不出现未启用工具指导。
8. 保存前展示有效 Runtime 和 Diff。
9. Candidate Runtime 失败不影响当前 Runtime。
10. Session 与 Memory 不在场景间静默串用。
11. 所有配置、Prompt、Diff 和快照不泄漏 Secret。
12. TUI、Headless、Remote 对同一场景产生相同 Prompt fingerprint 和工具集合。

