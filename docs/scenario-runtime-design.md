# MewCode 可组合场景 Runtime 设计

状态：设计提案  
范围：后端 Runtime 装配、扩展能力接入、TUI 场景管理  
目标：在不改变 MewCode 现有五层 Harness 主架构和 Agent Loop 的前提下，让用户按场景组合内置工具、MCP、CLI、Skill 和 Python 插件。

关联设计：

- [专属场景设计器：产品与架构方案](scenario-designer-product-architecture.md)
- [Scenario Designer 实现设计](scenario-designer-implementation-design.md)

## 1. 背景

MewCode 当前已经具备模型客户端、Agent Loop、ToolRegistry、MCP、Skill、Hook、权限检查、路径约束、OS Sandbox、Session、子 Agent 和 TUI 等能力，但这些能力主要由 TUI、非交互 CLI 和 Remote 三个入口分别初始化。默认工具集合也固定为 Coding Agent 所需的文件读写、搜索和 Bash 工具。

这使 MewCode 可以作为 Coding Agent 工作，却不便于用户创建一套仅包含文档、知识库、邮件、日程等能力的办公 Agent，也不便于按项目裁剪工具、Prompt 和安全策略。

本设计增加一个启动期的“场景装配面”。场景只决定一套 Runtime 包含哪些能力，运行期间仍使用现有任务编排、模型交互、工具执行和安全控制流程。

```text
Runtime = MewCode 固定内核 + 用户选择的场景能力
```

这里的“可组合”不表示把所有模块改写成插件，也不表示可以在一次模型调用中随意替换 Runtime。它表示用户可以在启动会话前声明并选择能力集合，由统一装配器构造一套确定的 Agent Runtime。

## 2. 目标与非目标

### 2.1 目标

- 用户可以创建、复制、编辑、校验、选择和删除自定义场景。
- 场景可以选择内置能力包、MCP 服务、声明式 CLI 工具、Skill 和已安装 Python 插件。
- Coding、办公或其他场景共用现有 Agent Loop 和工具执行链。
- TUI、非交互 CLI 和 Remote 使用同一个 RuntimeBuilder，消除重复初始化。
- 所有模型可调用能力都必须进入 ToolRegistry，并经过 Hook、PermissionChecker、Sandbox 和结果处理。
- Runtime 在第一次模型请求前完成解析和装配；配置错误应在启动阶段明确报告。
- Session 记录实际使用的场景快照，恢复会话时不静默切换能力集合。
- TUI 提供可发现、可检查的场景管理体验，用户无需手写 YAML 也能完成主要操作。

### 2.2 非目标

- 不引入 Cordis 或其他依赖注入框架。
- 不重写 Agent Loop、Conversation、模型协议适配器或现有工具执行流程。
- 不允许场景 YAML 直接指定任意 Python 文件、`module:object` 或 Shell 代码。
- 不允许在已有工具调用历史的会话中原地热切换场景。
- 不提供同一 Runtime 内的插件热卸载；更换场景通过构造新 Runtime 完成。
- 不把 MCP、Skill 和插件混为同一种实现。它们具有统一的目录描述和装配入口，但保留各自运行语义。

## 3. 设计原则

1. **组合发生在 Agent Loop 之外。** Agent 只接收最终的模型客户端、ToolRegistry、安全对象和 Prompt 内容。
2. **Scenario 引用能力，不承载代码。** 场景文件只能引用 Capability Catalog 中已知的能力 ID。
3. **解析结果不可变。** 用户配置先解析成 `RuntimeSpec`，后续构建过程不再读取会变化的场景文件。
4. **安全链不可绕过。** 内置工具、MCP、CLI 和插件工具使用同一执行入口。
5. **默认行为兼容。** 未选择场景时使用内置 `coding` 场景，其能力与当前默认 Runtime 等价。
6. **失败要可定位。** 未安装插件、CLI 不存在、MCP 连接失败、工具重名和依赖循环都应指出场景、能力和原因。
7. **生命周期由 Runtime 所有。** Runtime 创建的 MCP 客户端、Session、后台任务和插件资源由一个句柄统一关闭。
8. **场景可审计。** TUI 和命令行均能显示实际启用的工具、外部进程、权限等级和能力来源。

## 4. 总体架构

```text
内置能力       用户/项目配置       Python entry points
   │                 │                    │
   ├── Capability Pack│                    │
   ├── MCP Definition ├── Capability Catalog ── Plugin Descriptor
   ├── CLI Definition │                    │
   └── Skill          │                    │
                     ↓
              Scenario Catalog
                     ↓ 选择 scenario_id
              Scenario Resolver
                     ↓
          immutable RuntimeSpec
                     ↓
               RuntimeBuilder
      ┌──────────────┼────────────────┐
      ↓              ↓                ↓
  LLM Client     ToolRegistry    Permission/Sandbox
                      ↓
                 existing Agent
                      ↓
              TUI / Headless / Remote
```

新增的装配面位于配置与现有 Runtime 之间，不成为新的逐轮执行层。

## 5. 核心概念

### 5.1 Scenario

Scenario 是用户可选择的场景定义，描述 Persona、能力集合、功能开关和安全要求。它不包含可执行实现。

建议的发现位置和优先级为：

1. 项目级：`<project>/.mewcode/scenarios/*.yaml`
2. 用户级：`~/.mewcode/scenarios/*.yaml`
3. 内置：`mewcode/scenarios/builtin/*.yaml`

同 ID 场景按以上顺序覆盖。TUI 必须显示来源，避免用户误以为项目场景是全局场景。内置场景只读；用户可以复制后编辑。

### 5.2 Capability

Capability Catalog 使用统一描述符展示可选能力：

```python
class CapabilityKind(str, Enum):
    PACK = "pack"
    MCP = "mcp"
    CLI = "cli"
    SKILL = "skill"
    PLUGIN = "plugin"

@dataclass(frozen=True)
class CapabilityDescriptor:
    id: str
    kind: CapabilityKind
    title: str
    description: str
    source: str
    risk: str
    available: bool
    unavailable_reason: str = ""
```

统一描述符只服务于发现、选择和审计。各类能力仍由各自 Provider 装配。

### 5.3 RuntimeSpec

`RuntimeSpec` 是 ScenarioResolver 输出的完整、已验证、不可变结构。它包含：

- 场景 ID、名称、来源和内容摘要哈希。
- 已解析的模型 Provider 选择。
- 有序 Capability Pack 列表。
- MCP 配置及工具过滤、风险覆盖。
- CLI Tool 定义。
- 已安装插件及其配置和版本。
- Skill 白名单。
- Persona 和额外 Prompt Section。
- 功能开关、安全策略和启动失败策略。

RuntimeBuilder 只接受 `RuntimeSpec`，不接受原始 YAML。

### 5.4 BuiltRuntime

```python
@dataclass
class BuiltRuntime:
    spec: RuntimeSpec
    agent: Agent
    registry: ToolRegistry
    conversation: ConversationManager
    session: Session
    services: RuntimeServices

    async def aclose(self) -> None: ...
```

`aclose()` 按创建顺序的逆序清理插件句柄、MCP、后台任务和 Session。构建中途失败也使用同一清理栈回滚已创建资源。

## 6. 场景配置格式

场景文件采用独立 YAML，避免继续膨胀现有 `config.yaml`。`config.yaml` 只增加默认场景和能力定义入口。

```yaml
schema_version: 1
id: my-office
name: 我的办公助手
description: 文档审核、知识检索、邮件和日程管理

persona: |
  你是一名严谨的办公助理。涉及发送、删除、共享或创建外部资源时，
  必须先向用户说明影响并取得确认。

provider: inherit

capabilities:
  packs:
    - core.skills

  mcp:
    - id: office-docs
      required: true
      tools:
        include: ["*"]
        exclude: ["delete_*"]
      risk_overrides:
        "search_*": read
        "get_*": read
        "update_*": write
        "share_*": command

    - id: company-knowledge
      required: true

    - id: mail
      required: true
      risk_overrides:
        "search_*": read
        "get_*": read
        "send_*": command
        "delete_*": command

    - id: calendar
      required: false

  cli:
    - id: pandoc.convert
      required: false

  plugins:
    - id: document-review
      required: true
      config:
        default_language: zh-CN

  skills:
    include:
      - contract-review
      - meeting-summary
    exclude: []

features:
  memory: true
  worktree: false
  subagents: false
  teams: false
  skill_install: false

security:
  permission_mode: default
  sandbox: inherit
  confirm_tools:
    - "mcp_mail_send_*"
    - "mcp_calendar_create_*"
  deny_tools:
    - "mcp_office-docs_delete_*"

startup:
  optional_capability_failure: warn
```

规则：

- `id` 必须满足 `[a-z0-9][a-z0-9-]*`。
- 所有引用必须能在 Capability Catalog 中解析。
- `required: true` 的能力不可用时，Runtime 构建失败。
- 可选能力失败时根据 `startup.optional_capability_failure` 报警并以 degraded 状态启动。
- include/exclude 使用能力内部名称匹配；exclude 最终优先。
- `permission_mode: bypassPermissions` 不能从场景文件启用，只能由现有显式 CLI 参数或全局受控配置启用。
- 环境变量在构建时解析，但解析后的 Secret 不进入 `RuntimeSpec` 快照、日志或 TUI。

现有 `config.yaml` 增加：

```yaml
default_scenario: coding

mcp_servers:
  - name: office-docs
    command: office-docs-mcp
    env:
      OFFICE_TOKEN: "${OFFICE_TOKEN}"

cli_tools:
  - file: "~/.mewcode/cli-tools/pandoc.yaml"
```

## 7. 后端设计

### 7.1 建议模块结构

```text
mewcode/runtime/
├── models.py             # Scenario、CapabilityDescriptor、RuntimeSpec
├── scenario_loader.py    # 内置/用户/项目场景发现和原子保存
├── catalog.py            # 能力目录聚合
├── resolver.py           # 引用解析、验证、哈希和错误报告
├── builder.py            # 唯一 Runtime 装配入口
├── lifecycle.py          # 清理栈、BuiltRuntime
├── packs.py              # 内置能力包
├── cli_provider.py       # 声明式 CLI Tool
├── plugin_provider.py    # entry point 插件发现和生命周期
└── errors.py             # 结构化诊断
```

TUI、`-p` 和 Remote 不再自行创建默认 Registry、PermissionChecker、SkillLoader 和 MCPManager，而是传入 Surface 特征后调用同一个 Builder。

### 7.2 内置 Capability Pack

将当前 `create_default_registry()` 拆为有明确依赖的内置包：

| Pack ID | 内容 |
|---|---|
| `core.skills` | SkillLoader、LoadSkill、可选 InstallSkill、Skill catalog Prompt |
| `core.interaction` | AskUser、ToolSearch、计划模式相关工具 |
| `coding.files` | ReadFile、WriteFile、EditFile、Glob、Grep，共享 FileStateCache |
| `coding.shell` | Bash、OS Sandbox 挂载 |
| `coding.worktree` | WorktreeManager、EnterWorktree、ExitWorktree |
| `coding.agents` | AgentLoader、AgentTool、TaskManager |
| `coding.teams` | TeamManager、TeamCreate、TeamDelete、SendMessage 和任务工具 |
| `core.session` | Session、Memory、FileHistory 及其绑定 |

Pack 不是通用动态插件。它是 MewCode 内部的注册函数，用来保存当前工具之间的依赖关系。例如文件工具必须共享同一个 `FileStateCache`，不能由 YAML 逐个实例化。

```python
class CapabilityPack(Protocol):
    id: str
    requires: tuple[str, ...]

    async def contribute(self, ctx: RuntimeBuildContext) -> None: ...
```

Resolver 对 Pack 做稳定的拓扑排序；缺失依赖和循环依赖在启动前失败。相同优先级按 ID 排序，保证工具 Schema 顺序稳定。

### 7.3 RuntimeBuilder 顺序

RuntimeBuilder 使用确定顺序：

1. 接收并再次断言已验证的 `RuntimeSpec`。
2. 创建 LLM Client、SessionManager、MemoryManager、FileHistory 和 PermissionChecker。
3. 创建空 ToolRegistry、Prompt contribution 集合和生命周期清理栈。
4. 按依赖顺序装配内置 Pack。
5. 创建声明式 CLI Tool。
6. 加载并 setup 已安装 Python 插件。
7. 连接所选 MCP，过滤工具并应用风险分类后注册。
8. 加载场景允许的 Skill，并生成 Skill catalog。
9. 检查工具名、命令名和 Prompt section 冲突。
10. 创建现有 Agent，并完成需要 parent Agent 的工具绑定。
11. 注册 ToolSearch 等依赖最终 Registry 的工具。
12. 写入 Session Runtime 快照并发布 `BuiltRuntime`。

任何步骤失败都清理此前资源。Runtime 未完整构建前不能被 TUI、Remote 或模型请求观察到。

#### 工具注册元数据

ToolRegistry 不能再依赖工具名称前缀推断来源。每次注册同时记录来源：

```python
@dataclass(frozen=True)
class ToolOrigin:
    kind: Literal["builtin", "pack", "mcp", "cli", "plugin"]
    provider_id: str

@dataclass(frozen=True)
class RegisteredTool:
    tool: Tool
    origin: ToolOrigin
```

`register()` 遇到重名立即失败，并在诊断中同时列出已有来源和新来源。TUI 统计、场景审计、子 Agent 过滤和 Session Runtime 快照都读取 `ToolOrigin`，不使用 `mcp_` 等命名约定。

这也消除当前 MCP Wrapper 使用 `mcp_<server>_<tool>`、而部分过滤代码用其他前缀识别 MCP 的不一致。现有 MCP 工具名保持兼容；变化的是内部来源判断机制。

### 7.4 Prompt 组合

现有 `build_system_prompt()` 保留。新增场景输入：

```python
build_system_prompt(
    ...,
    persona=runtime.spec.persona,
    runtime_sections=runtime.prompt_sections,
)
```

Prompt Section 使用稳定名称和优先级：

- 场景 Persona：`scenario:persona`
- Capability Pack：`pack:<id>`
- MCP instructions：`mcp:<server-id>`
- 插件：`plugin:<plugin-id>:<section-name>`

同名 Section 冲突应失败，不能以后注册覆盖先注册。系统提示和可见工具集合必须来自同一 `RuntimeSpec`，防止 Prompt 声称存在一个实际未注册的工具。

### 7.5 MCP 接入

复用现有 MCPClient 和 MCPManager，增加以下装配能力：

- `MCPManager` 只加载场景选择的服务。
- MCP 工具在注册前执行 include/exclude 过滤。
- Wrapper 接收解析后的风险类别，而不是统一写死为 `command`。
- Wrapper 注册时携带 `ToolOrigin(kind="mcp", provider_id=<server-id>)`。
- 工具名冲突明确失败并显示服务来源。
- required MCP 连接失败导致构建失败；optional MCP 失败记录 degraded 状态。
- MCP instructions 只为成功连接且至少暴露一个工具的服务注入。
- Runtime 关闭时统一调用 `MCPManager.shutdown()`。

风险覆盖按“精确名称优先、后按最具体 glob、最后使用 `command`”解析。场景只能将默认风险收紧；将写操作标为 read 必须由 MCP 定义或受信任插件声明，不能由普通场景降级。

### 7.6 声明式 CLI 工具

CLI 工具定义独立存放于：

- 项目级：`<project>/.mewcode/cli-tools/*.yaml`
- 用户级：`~/.mewcode/cli-tools/*.yaml`

示例：

```yaml
schema_version: 1
id: pandoc.convert
name: ConvertDocument
description: 使用 pandoc 转换本地文档格式
executable: pandoc
category: write
timeout_seconds: 60
working_directory: workspace

parameters:
  type: object
  properties:
    input_file:
      type: string
    output_file:
      type: string
  required: [input_file, output_file]

argv:
  - "{input_file}"
  - "-o"
  - "{output_file}"

permission_targets:
  - parameter: input_file
    access: read
  - parameter: output_file
    access: write

environment:
  allow: ["PATH", "PANDOC_DATA_DIR"]
```

执行约束：

- 使用 `asyncio.create_subprocess_exec()`，禁止 `shell=True`。
- `executable` 是单个程序名或管理员允许的绝对路径，不接受管道、重定向和命令连接符。
- `argv` 中的占位符只能引用已校验参数，每个元素生成一个 argv 项，不做字符串拼接后 Shell 解析。
- 环境变量采用白名单；Secret 只允许通过 `${ENV_VAR}` 引用。
- 工作目录只能为 workspace、临时目录或明确允许路径。
- 超时后终止整个子进程树。
- stdout/stderr 继续使用现有结果长度和持久化策略。
- `permission_targets` 为权限层提供结构化资源，不依赖当前 `_CONTENT_FIELDS` 硬编码。

为此，Tool 增加可覆盖的权限描述接口：

```python
@dataclass(frozen=True)
class PermissionTarget:
    resource: str
    access: Literal["read", "write", "execute", "external"]

class Tool:
    def permission_targets(self, arguments: dict[str, Any]) -> list[PermissionTarget]: ...
```

现有工具保持默认兼容实现；CLI、MCP 和插件工具必须提供准确目标。PermissionChecker 的决策顺序不变，只把内容提取从工具名硬编码改为 Tool 自描述。

### 7.7 Python 插件

Python 插件通过标准 entry point 发现：

```toml
[project.entry-points."mewcode.plugins"]
document-review = "mewcode_document_review:plugin"
```

插件协议：

```python
class MewCodePlugin(Protocol):
    id: str
    version: str
    requires: tuple[str, ...]

    def describe(self) -> PluginDescriptor: ...
    async def setup(
        self,
        ctx: PluginContext,
        config: Mapping[str, Any],
    ) -> PluginHandle: ...

class PluginHandle(Protocol):
    async def aclose(self) -> None: ...
```

`PluginContext` 是窄接口，只提供：

- 注册 Tool、Prompt Section 和本地命令。
- 读取 workspace、场景 ID 和经白名单解析的插件配置。
- 注册清理回调。
- 获取声明过的公共 Runtime Service。

它不暴露 TUI 对象、Agent 内部循环或 PermissionChecker 的可变状态。插件工具仍由 ToolRegistry 执行，不能获得绕过权限的专用调用入口。

安全约束：

- 安装 Python 插件等价于安装并执行 Python 包，TUI 必须明确显示这一信任级别。
- 场景只能引用已安装 entry point ID，不能填写导入路径。
- setup 返回句柄；无句柄或 setup 异常视为插件构建失败。
- 插件依赖按 ID 拓扑排序，缺失、版本不满足和循环均失败。
- 工具或命令重名失败，不允许静默覆盖。
- 插件配置使用其 Descriptor 提供的 JSON Schema 校验。

### 7.8 Skill 选择

SkillLoader 继续负责项目、用户和内置 Skill 发现，但增加视图过滤：

- `include: ["*"]` 表示全部已发现 Skill。
- 显式 include 只向模型和斜杠命令暴露指定 Skill。
- exclude 始终优先。
- 场景禁用 `skill_install` 时不注册 InstallSkillTool。
- Skill 不在场景允许列表时，不能通过直接工具参数绕过 catalog 过滤加载。

### 7.9 安全策略

安全控制仍由现有层负责，场景只提供额外约束：

```text
危险命令检测
    ↓
OS Sandbox / 路径边界
    ↓
用户与项目权限规则
    ↓
场景 deny / confirm 规则
    ↓
会话级授权
    ↓
permission mode
    ↓
人工确认
```

场景不能：

- 关闭 DangerousCommandDetector。
- 扩大 PathSandbox 允许根目录。
- 开启 bypassPermissions。
- 将受信任 Provider 声明的 write/command 风险降为 read。
- 向 CLI 子进程透传完整宿主环境。

场景可以：

- 禁止某些工具。
- 要求某些工具每次确认。
- 缩小可写路径或禁用网络。
- 禁用 Skill 安装、子 Agent、团队或 Worktree。

### 7.10 Session 一致性

SessionMeta 增加：

```python
scenario_id: str
runtime_fingerprint: str
runtime_snapshot_file: str
```

创建 Session 时保存规范化 `RuntimeSpec` 快照，敏感值只保存环境变量引用，不保存解析后的 Secret。Fingerprint 覆盖工具 ID、插件版本、MCP/CLI 定义摘要、Prompt 和安全策略。

恢复会话时：

1. 优先读取 Session 的 Runtime 快照。
2. 校验所需插件、CLI 和 MCP 定义仍可用。
3. 使用快照构造 Runtime，不自动采用同名场景文件的新内容。
4. 缺少 required 能力时拒绝恢复，并列出缺失项。
5. 用户希望采用已修改场景时，创建新 Session；不修改原 Session 的 Runtime 身份。

这样可以避免历史里存在某工具调用，而恢复后的 Runtime 已经没有该工具或改变了工具语义。

### 7.11 三个运行入口统一

- TUI：Launcher 选择场景和模型后调用 RuntimeBuilder。
- Headless：增加 `--scenario <id>`；未指定时使用 `default_scenario`。
- Remote：启动参数接收 scenario ID，并在 connected/status 消息中返回 Runtime 摘要。

三者不得再分别调用 `create_default_registry()`。Surface 差异通过 `RuntimeSurface` 描述，例如是否支持 AskUser UI、是否允许交互授权、是否启用 Remote 命令，而不是复制整段初始化代码。

## 8. TUI 设计

### 8.1 启动流程

启动状态由当前“直接选择 Provider”调整为统一 Launcher：

```text
┌ MewCode ───────────────────────────────────────────────────┐
│ Scenario                                                   │
│ ❯ Coding                                                  │
│   My Office                                               │
│   Research                                                │
│                                                           │
│ My Office                                                 │
│ 4 MCP · 1 CLI · 1 Plugin · 2 Skills                      │
│ External writes require confirmation                      │
│                                                           │
│ Model: anthropic-official / claude-...                    │
│                                                           │
│ Enter Start   e Edit   n New   v Validate   Esc Quit      │
└────────────────────────────────────────────────────────────┘
```

行为：

- 单 Provider 时不额外打断用户，直接在详情区显示。
- 多 Provider 时 Tab 在场景列表和 Provider 选择间移动。
- 内置 `coding` 为默认场景，保持当前直接启动体验。
- 场景不存在、不可解析或 required 能力缺失时仍显示在列表中，但标记 `Broken` 并禁止启动。
- 启动前展示外部进程数、插件数和最高风险级别。
- RuntimeBuilder 工作期间显示逐项状态：Resolve、Plugins、MCP、Tools、Ready。
- required 能力失败返回 Launcher 并保留选择；optional 能力失败允许以 `Degraded` 状态进入聊天。

### 8.2 场景管理入口

提供两种入口：

- Launcher 中的快捷键。
- 聊天中的 `/scenario` 命令。

命令设计：

```text
/scenario                    打开场景管理器
/scenario list               列出场景和健康状态
/scenario show <id>          显示解析后的能力与安全摘要
/scenario use <id>           使用场景创建新 Session
/scenario new [template]     创建场景
/scenario edit <id>          编辑用户或项目场景
/scenario copy <id> <new-id> 复制为可编辑场景
/scenario validate <id>      校验定义、依赖和本机可用性
/scenario delete <id>        删除用户/项目场景
/scenario reload             重新扫描目录和插件入口
```

`/status` 增加场景 ID、Runtime fingerprint、健康状态和各类能力数量。状态栏在 permission mode 左侧持续显示场景：

```text
 office  ·  default  ·  4 MCP  ·  claude-sonnet
```

### 8.3 场景管理器

Bare `/scenario` 打开全屏 Textual Screen，而不是把多步骤编辑塞进聊天区：

```text
┌ Scenarios ───────────┬ Runtime Composition ────────────────┐
│ ❯ coding      Builtin│ Coding                              │
│   my-office   User   │ Healthy                             │
│   project-qa  Project│                                     │
│   broken-demo Broken │ Packs     4                         │
│                      │ MCP       github, context7           │
│                      │ CLI       —                         │
│                      │ Plugins   —                         │
│                      │ Skills    *                         │
│                      │ Risk      command                   │
│                      │                                     │
│                      │ Tools     18 visible / 5 deferred   │
├──────────────────────┴─────────────────────────────────────┤
│ Enter Use  n New  c Copy  e Edit  v Validate  d Delete    │
└────────────────────────────────────────────────────────────┘
```

列表支持输入搜索、上下导航和来源筛选。右侧显示 Resolver 结果，而不是仅显示原始 YAML，因此用户能看到最终工具数量、依赖补全、被过滤能力和风险分类。

删除只允许用户级和项目级场景，并使用现有内联确认交互。内置场景只能复制。

### 8.4 场景编辑器

编辑器使用四个 Tab：

1. **Basic**：ID、名称、描述、Persona、保存范围。
2. **Capabilities**：按 Pack、MCP、CLI、Plugin、Skill 分类选择。
3. **Security**：权限模式、确认/拒绝规则、Sandbox 和功能开关。
4. **Review**：最终 Runtime 摘要、警告和保存。

Capabilities 页：

- 每项显示来源、可用性、风险、描述和依赖。
- Space 勾选，Enter 查看详情，`/` 搜索。
- 选择插件时显示“插件运行本地 Python 代码”的信任提示。
- 选择 MCP 时可配置 required、工具 include/exclude 和风险覆盖。
- 选择 CLI 时显示最终 executable、argv 结构、环境白名单和权限目标。
- 缺失能力可以保留在草稿中，但不能保存为默认场景，也不能启动。

Review 页必须呈现：

- 模型可见工具总数和 deferred 数量。
- 将启动的本地进程和网络 MCP。
- 可写本地路径、网络能力和外部写操作。
- 插件包名及版本。
- 所有 warning/error。

保存使用临时文件加原子替换。项目场景写入 `.mewcode/scenarios/`，用户场景写入 `~/.mewcode/scenarios/`。ID 冲突不覆盖，要求用户明确选择新 ID 或编辑已有文件。

### 8.5 新建场景

新建流程首先选择模板：

- Empty：仅核心交互和 Session。
- Coding：复制内置 Coding 组合。
- Office：核心 Skill，加空的外部办公能力选择。
- Copy current：复制当前 Runtime 对应场景。

模板只用于生成完整场景文件，运行时不存在继承链，从而避免基础模板变化导致用户场景在未编辑时改变。

### 8.6 场景切换

场景切换是 Runtime 替换，不是修改当前 Agent：

- 当前 Session 没有消息时，可以构建新 Runtime 并替换空 Runtime。
- 当前 Session 已有消息时，TUI 提示“使用该场景创建新会话”。
- 先在后台完整构建候选 Runtime；成功后再关闭旧 Runtime 并切换。
- 构建失败时旧 Runtime 和当前会话保持可用。
- 切换后清空聊天显示，状态栏立即更新场景名称。

### 8.7 错误与降级显示

诊断采用结构化模型：

```python
@dataclass(frozen=True)
class RuntimeDiagnostic:
    severity: Literal["info", "warning", "error"]
    code: str
    capability_id: str | None
    message: str
    hint: str = ""
```

TUI 示例：

```text
ERROR plugin-missing · document-review
Plugin "document-review" is not installed.
Install the package that provides entry point mewcode.plugins/document-review.

WARNING mcp-unavailable · calendar
Connection failed: executable not found. This capability is optional.
```

错误信息不得显示 API Key、Header 值或完整 Secret 环境变量。

## 9. 默认场景

内置 `coding` 场景必须等价于当前默认行为：

```yaml
schema_version: 1
id: coding
name: Coding
description: MewCode 默认 Coding Agent Runtime

provider: inherit

capabilities:
  packs:
    - core.session
    - core.interaction
    - core.skills
    - coding.files
    - coding.shell
    - coding.worktree
    - coding.agents
    - coding.teams
  mcp: configured
  cli: []
  plugins: []
  skills:
    include: ["*"]
    exclude: []

features:
  memory: true
  worktree: true
  subagents: true
  teams: true
  skill_install: true

security:
  permission_mode: inherit
  sandbox: inherit
```

`mcp: configured` 仅允许内置兼容场景使用，表示维持当前加载全部 `config.yaml` MCP 的行为。用户新建场景时必须显式选择 MCP，避免意外扩张能力。

## 10. 配置与兼容迁移

- 现有用户没有 `default_scenario` 时自动选择 `coding`。
- 现有 `mcp_servers` 定义保持不变，由 Scenario 通过名称引用。
- `create_default_registry()` 保留为兼容包装，内部调用 `coding` Pack 装配；三个正式入口不再直接使用它。
- 现有 Agent 定义中的 tools/disallowedTools 继续作为场景工具集合之上的子 Agent 过滤。
- MCP 不再“对子 Agent 永远放行”；子 Agent 只能继承父 Runtime 已选择的 MCP，并继续受 Agent allow/deny 列表约束。
- 旧 Session 没有 scenario 信息时标记为 `legacy-coding`，使用内置 Coding 兼容快照恢复。
- 现有权限文件、Hook、Skill 目录和 Provider 配置格式继续生效。

## 11. 代码改动范围

主要改动：

- 新增 `mewcode/runtime/` 装配模块。
- 扩展 `config.py` 和 `validator.py`：默认场景、CLI 定义索引和新结构校验。
- 调整 `tools/base.py`：结构化 permission targets。
- 调整 `permissions/checker.py` 和 `permissions/rules.py`：支持外部工具的权限描述与场景规则。
- 调整 `mcp/tool_wrapper.py` 和 `mcp/manager.py`：筛选、风险分类、required/optional 状态。
- 调整 `skills/loader.py`：场景视图过滤。
- 扩展 `prompts.py`：Persona 和 Runtime Prompt Sections。
- 扩展 `memory/session.py`：场景 ID、Runtime fingerprint 和快照。
- 修改 `app.py`：Launcher、Runtime 交接和状态栏。
- 新增 `scenario_screen.py`、`scenario_editor.py` 和 `/scenario` handler。
- 修改 `__main__.py`、`remote.py`：统一调用 RuntimeBuilder。

不需要修改 Agent 主循环的模型请求、Tool call 分批、PermissionRequest、结果追加和压缩语义。

## 12. 测试设计

### 12.1 后端单元测试

- 场景发现优先级、ID 校验、原子保存和只读内置场景。
- Capability Catalog 聚合及不可用原因。
- Resolver 对未知引用、required/optional、include/exclude 和风险覆盖的处理。
- Pack 依赖排序、缺失依赖和循环依赖。
- Tool/Command/Prompt section 重名检测。
- CLI 参数 Schema、argv 无 Shell 解析、环境白名单、超时和进程树终止。
- CLI 路径权限目标进入 PermissionChecker。
- MCP 工具过滤、风险分类、部分失败和 shutdown。
- 插件 entry point 发现、配置 Schema、依赖、setup 回滚和逆序关闭。
- Skill include/exclude 不能被直接 LoadSkill 绕过。
- Runtime fingerprint 对非敏感配置变化稳定，对能力变化敏感。
- Session 快照不包含 Secret，恢复时不采用同名场景的新版本。

### 12.2 集成测试

- TUI、headless 和 Remote 对同一 Scenario 产生相同工具集合与 Prompt 摘要。
- 默认 Coding 场景与当前默认工具和功能等价。
- Office 场景不包含 Bash、文件写入、Worktree 或团队工具。
- 所有来源的工具调用都经过 Hook 和 PermissionChecker。
- required MCP/Plugin 失败不会发布半构建 Runtime。
- optional 能力失败生成 degraded Runtime 和可见诊断。
- 候选 Runtime 构建失败不会关闭当前 Runtime。
- Runtime 关闭不遗留 MCP 连接、子进程或后台任务。

### 12.3 TUI 测试

使用 Textual Pilot 覆盖：

- Launcher 搜索、场景/Provider 选择和 Broken 状态。
- 场景管理器列表、详情和快捷键。
- 编辑器 Tab、能力勾选、校验和 Review 摘要。
- 内置场景不可编辑但可复制。
- 非空会话切换场景时创建新 Session。
- 构建失败后仍可继续使用原会话。
- 状态栏和 `/status` 显示正确 Runtime 信息。
- Secret 不出现在诊断、详情和快照预览中。

## 13. 验收标准

以下条件全部满足时设计落地完成：

- 用户可以仅通过 TUI 创建一个自定义场景并选择 MCP、CLI、插件和 Skill。
- 用户也可以通过 YAML 创建同等场景，并用命令校验和启动。
- `mewcode --scenario my-office`、TUI 和 Remote 使用同一装配结果。
- 默认启动行为对现有用户保持为 Coding Agent。
- 场景中未选择的工具不会出现在模型 Schema、ToolSearch 或子 Agent 中。
- MCP、CLI 和插件工具都经过现有 Hook、权限和 Sandbox 控制。
- 场景 YAML 不能直接执行 Python 导入路径或 Shell 模板。
- Runtime 构建具备事务性：成功后发布，失败时完全回滚。
- Session 可以确定其 Runtime 身份，场景修改不会静默改变旧会话。
- TUI 能在启动前解释一套场景将获得什么能力、启动什么外部进程以及承担什么风险。

## 14. 最终边界

本设计借鉴 DeepSeek Harness 的是“运行时由能力组合产生”，但不复制 Cordis 的完整插件树。MewCode 保持固定内核：

```text
固定：任务编排、模型交互、Agent Loop、工具执行、安全控制、Session 语义
可组合：工具、MCP、CLI、Skill、插件、Persona、功能开关和附加安全约束
```

因此，优化后的 MewCode 不是“everything is plugin”，而是：

> 用户通过 Scenario 选择受控能力，MewCode 在启动时把它们组合成一套可审计、可恢复、受统一安全链保护的 Agent Runtime。
