# Lumo 专属场景设计器：产品与架构方案

状态：产品与架构提案  
范围：专属场景创建、模型辅助配置、场景体验组合、信任与安全边界  
关联文档：[可组合场景 Runtime 设计](scenario-runtime-design.md)、[Scenario Designer 实现设计](scenario-designer-implementation-design.md)

## 1. 背景与问题

Lumo 已经可以通过 Scenario 选择 Capability Pack、MCP、CLI、Plugin、Skill、安全策略和功能开关，并在启动时构造不同的 Agent Runtime。但从用户体验看，当前“场景”仍主要是执行能力组合，而不是完整的产品体验组合。

典型问题是：用户切换到 Office 场景后，状态栏显示 `scenario: office`，模型仍然自称编程助手、假设用户主要提出软件工程任务，并介绍 Bash、文件编辑、调试和代码重构能力。模型还可能猜测自己使用了错误的模型供应商。

这说明一个 Runtime 即使正确裁剪了工具，也可能在模型身份、任务预期、能力自述、Memory 和 Prompt 上继续表现为另一个场景。

对用户而言，选择场景不是选择一份 Tool 白名单，而是在选择一份可理解、可预测、可审计的产品行为契约。

## 2. 产品愿景

用户应当可以用自然语言描述自己需要的 Agent，由 Lumo 帮助完成能力匹配、Prompt 草拟、安全约束、配置生成、验证和启动，而不要求用户先理解 YAML、MCP、Python entry point、工具风险分类和 Prompt 优先级。

产品目标可以概括为：

> Describe the agent you need; Lumo composes the runtime you can trust.

中文表达：

> 用户描述目标，Lumo 组合一套真实可用、行为一致、边界清楚的专属 Agent Runtime。

## 3. 完整的 Composable Runtime

完整 Runtime 不只是能力组合：

```text
Agent Runtime
├── Neutral Kernel       固定 Agent Loop、模型协议、工具管线、Session 语义
├── Capability           Tool、MCP、CLI、Plugin、Skill
├── Experience           身份、任务预期、工作流、输出方式、领域约束
├── Policy               权限、确认、拒绝、Sandbox、外部写操作规则
├── State                Session、Memory、Runtime 快照、场景隔离
└── Presentation         TUI 名称、摘要、健康状态、风险和能力说明
```

因此，产品原则升级为：

> Runtime composition must compose both capability and experience.

场景切换成功的判断标准，不是状态栏 ID 改变，而是以下内容同时一致：

- UI 显示的场景。
- 模型对自身角色的描述。
- 模型默认理解的用户任务。
- 模型收到的工具说明。
- 模型实际可调用的工具。
- 权限与审批行为。
- Session 与 Memory 所属范围。
- Provider 和 Model 的事实描述。

## 4. 目标用户与核心任务

### 4.1 不熟悉 Agent 配置的业务用户

用户知道自己想完成什么，但不知道需要哪些 MCP、Skill 或权限规则。

核心任务：

- 用自然语言描述一个办公、研究、运营或行业 Agent。
- 根据引导回答少量关键问题。
- 看懂最终能力与风险。
- 确认后直接使用。

### 4.2 熟悉 Lumo 的高级用户

用户希望快速复制、调整和版本化现有场景。

核心任务：

- 从内置模板或当前 Runtime 创建场景。
- 用自然语言增删能力和修改行为。
- 查看结构化 Diff。
- 手工编辑 YAML 或 Prompt。
- 验证配置并查看 Runtime fingerprint。

### 4.3 团队或项目维护者

维护者希望把可靠场景随项目分发。

核心任务：

- 在项目中提交不含 Secret 的场景包。
- 约束项目 Agent 的工具和写操作。
- 为团队提供统一 Persona、工作流和输出格式。
- 在外部依赖缺失时得到清晰诊断。

## 5. 核心产品概念

### 5.1 Scenario Package

Scenario Package 是一套声明式、可复制、可审查的场景定义。它包括：

- 显示元数据。
- 场景身份与使命。
- 默认任务和领域工作流。
- Capability 引用。
- 安全与审批规则。
- Memory 范围。
- 场景 Prompt。
- 可选的样例与资产。

它不包含任意 Python 导入路径、Shell 脚本或明文 Secret。

### 5.2 Capability Catalog

Capability Catalog 是本机能力事实来源，包括：

- 内置 Capability Pack。
- 已配置 MCP。
- 已发现 CLI 定义。
- 已安装 Python Plugin。
- 已加载 Skill。

模型可以推荐 Catalog 中的能力，但不能凭空创建“已经可用”的能力。

### 5.3 Scenario Proposal

Scenario Proposal 是模型生成的结构化提案，不是最终配置文件。

它表达：

- 用户目标。
- 建议身份。
- 建议工作流。
- 请求的能力。
- 需要禁止或确认的操作。
- 缺失能力。
- 尚未回答的问题。

Proposal 必须经过确定性 Resolver 和安全校验，才能转换为 Scenario Package。

### 5.4 Runtime Facts

Runtime Facts 是 Lumo 在启动时根据真实配置生成的模型可见事实：

- 当前 Scenario ID 与名称。
- 配置的 Provider 名称。
- 配置的 Model 名称。
- 实际启用的能力摘要。
- 降级或不可用能力。
- 当前工作目录和 Surface。

模型不得猜测 Provider、Model 或未启用能力。

## 6. Scenario Designer 产品形态

Scenario Designer 是 Lumo 内置的受控创作流程，不依赖当前场景拥有 Bash 或文件写工具。

主要入口：

```text
/scenario create
/scenario refine <id>
/scenario explain <id>
/scenario test <id>
```

### 6.1 Create

用户通过自然语言创建场景：

```text
帮我创建一个合同审核助手，可以读取本地合同和查询公司知识库，
不能修改原合同，输出风险清单和修改建议。
```

Designer 只追问会改变 Runtime 设计的关键问题：

1. 场景保存到当前项目还是用户全局？
2. 数据来自本地文件、哪个 MCP，还是两者都有？
3. 场景完全只读，还是可以生成新的报告文件？
4. 哪些外部操作必须确认？
5. 输出面向谁，采用什么格式？
6. 是否需要共享 Memory，还是按场景隔离？

Designer 不应逐字段询问 YAML 配置，也不应要求用户理解内部 Pack ID。

### 6.2 Refine

用户可以自然语言修改已有场景：

```text
给 my-office 增加日程查询，但创建会议必须每次确认。
```

Designer 生成结构化 Patch，TUI 展示修改前后差异。它不重写整个配置文件，避免误删用户已有设置。

### 6.3 Explain

用户可以询问：

- 这个场景为什么需要某个能力？
- 哪些操作会写入外部系统？
- 为什么某个 MCP 显示不可用？
- 这个场景和 Coding 有什么区别？

解释必须来自解析后的 RuntimeSpec，而不是只复述 YAML。

### 6.4 Test

Test 在不启动正式会话前完成：

- Schema 校验。
- Capability 解析。
- Plugin 与 Pack 依赖检查。
- CLI executable 探测。
- 可选 MCP 连接探测。
- Prompt 与工具一致性检查。
- Secret 泄漏检查。
- Runtime 构建回滚验证。

涉及网络、认证或启动外部进程的探测必须在 TUI 中明确展示并取得用户同意。

## 7. 用户创建流程

```text
Describe
   ↓
Clarify
   ↓
Match installed capabilities
   ↓
Draft identity, workflow and policy
   ↓
Preview effective runtime
   ↓
Validate and optionally probe
   ↓
Approve
   ↓
Atomically save scenario package
   ↓
Build candidate runtime
   ↓
Start a new session
```

### 7.1 提案预览

用户确认前，TUI 至少展示：

```text
合同审核助手

Identity
  企业合同审核助理

Capabilities
  ✓ 本地文档读取
  ✓ 公司知识库查询
  ✗ 文件编辑
  ✗ Bash
  ✗ 邮件发送

Policy
  原合同只读
  允许生成独立审核报告
  所有外部写操作需要确认

Output
  风险等级、原文、原因、修改建议

Missing
  company-knowledge MCP 尚未配置
```

### 7.2 确认语义

用户确认的是“有效 Runtime”，不是模型生成的一段文本。确认对象包括：

- 最终能力集合。
- 最终风险与审批策略。
- 最终 Prompt 预览。
- 会启动的外部进程和网络连接。
- 保存范围和文件位置。
- 缺失能力的处理方式。

## 8. 模型与确定性系统的职责

### 8.1 模型负责

- 理解自然语言目标。
- 提取用户角色、受众和工作流。
- 识别需要澄清的产品选择。
- 根据 Catalog 推荐候选能力。
- 草拟场景 Prompt。
- 解释差异和风险。

### 8.2 Lumo 负责

- 发现真实能力。
- 解析 Capability ID。
- 补齐依赖。
- 验证 Schema。
- 判断能力可用性。
- 应用权限下限。
- 阻止安全策略降级。
- 脱敏 Secret。
- 生成和保存 YAML。
- 构建、回滚和关闭 Runtime。

### 8.3 用户负责

- 决定最终业务目标。
- 选择数据与外部系统。
- 授权安装、连接和外部写操作。
- 确认有效 Runtime。

核心规则：

> 模型负责意图理解与 Prompt 草拟，Lumo 负责能力解析与安全校验，用户负责最终授权。

## 9. Experience Composition

### 9.1 Neutral Kernel

全局 Kernel 必须保持场景中性，只定义：

- Lumo 平台身份。
- Agent Loop 与工具协议。
- 通用安全规则。
- Session 与上下文机制。
- 通用交流规范。

Neutral Kernel 不能写“programming assistant”、不能假设用户主要提出软件工程任务，也不能列出可能不存在的 Bash、Git 或文件工具。

### 9.2 Scenario Experience

Scenario 决定：

- Role：模型在该场景中的身份。
- Mission：要帮助用户达到什么结果。
- Audience：输出面向谁。
- Default tasks：模糊请求默认如何理解。
- Workflows：领域任务的默认步骤。
- Output contract：输出格式和质量要求。
- Domain constraints：只读、引用来源、合规等边界。

### 9.3 Capability Guidance

工具说明由实际启用的 Capability 提供：

- `coding.files` 才能贡献 ReadFile、EditFile 的使用指导。
- `coding.shell` 才能贡献 Bash 指导。
- 邮件 MCP 只在连接并暴露工具后贡献邮件操作说明。
- 被过滤的工具不能继续出现在 Prompt 中。

### 9.4 Capability Truth

当用户询问“你能做什么”，回答必须来自有效 ToolRegistry 与 RuntimeSpec：

- 不宣称未启用能力。
- 区分读取、写入和外部操作。
- 告知缺失或降级能力。
- 告知哪些操作需要确认。

## 10. Office 场景示例

如果 Office 场景没有接入办公能力，产品不应暗示它已经能够管理邮件和日历。

合理展示：

```text
Office Base
办公助理基础 Runtime

当前能力
  ✓ 通用对话
  ✓ Skill
  ✗ 文档服务未连接
  ✗ 邮件服务未连接
  ✗ 日历服务未连接
```

合理的模型自述：

> 我是 Lumo 的办公助理 Runtime，当前配置模型为 deepseek-v4-flash。当前尚未连接文档、邮件或日历服务，因此可以帮助整理和分析你提供的文本，但不能直接操作这些外部系统。

接入能力后，模型才可以声明对应功能。

## 11. Memory 与 Session

场景身份不能只在首轮 Prompt 中存在，还必须体现在状态管理中。

默认规则：

- 每个正式场景拥有独立 Memory namespace。
- 项目指令可以跨场景共享，但场景偏好默认不共享。
- 场景切换创建新 Session。
- Session 保存 Runtime snapshot 与 Prompt fingerprint。
- 恢复时使用原 Runtime 身份，不自动采用同名场景的新内容。
- 用户可以显式选择共享 Memory，但 TUI 必须展示范围。

这样可以避免 Office 场景回忆并强化此前 Coding 场景的身份和偏好。

## 12. 安全与信任

### 12.1 Secret

- Designer 不要求用户把 API Key 写入 Prompt。
- 生成的配置只保存环境变量引用或 Credential reference。
- Prompt、Proposal、诊断、Diff、Session snapshot 不包含解析后的 Secret。

### 12.2 权限

- Designer 不能生成 `bypassPermissions`。
- 用户场景不能把 Provider 声明的写操作降级为只读。
- 新增外部写能力必须在预览中突出显示。
- 安装 Python Plugin 等价于执行本地代码，必须单独确认。

### 12.3 Prompt

- 场景 Prompt 不能覆盖 Neutral Kernel 的安全不变量。
- Prompt 中引用不存在的工具产生警告或错误。
- 从外部模板导入的 Prompt 视为不受信任内容。
- 模型生成 Prompt 后必须展示给用户审阅。

### 12.4 Authoring Runtime

Scenario Designer 自身使用受限 Runtime，只拥有 Catalog、Proposal、Validation 和场景草稿相关工具。它不依赖 Bash 或普通 WriteFile，不能写出场景目录，也不能直接安装插件或连接外部账号。

## 13. TUI 产品体验

### 13.1 场景工作室

场景工作室包含五个视图：

1. Describe：自然语言描述目标。
2. Clarify：回答关键问题。
3. Compose：查看身份、能力、策略和 Prompt。
4. Validate：查看错误、警告和探测结果。
5. Review：确认 Diff、保存范围并启动。

### 13.2 持续可见信息

运行期间状态栏和 `/status` 展示：

- Scenario。
- Permission mode。
- Provider 与 Model。
- Runtime health。
- 外部能力数量。

### 13.3 场景切换

切换前展示当前与目标 Runtime 对比，确认后创建新 Session。切换完成后展示：

- 原场景与新场景。
- 能力数量。
- 降级状态。
- 新 Session 信息。

## 14. 非目标

- 不让模型直接执行任意 YAML 或 Python。
- 不把自然语言描述当成已授权的外部写操作。
- 不在有历史的 Session 中原地替换身份和能力。
- 不保证模型生成的领域 Prompt 自动具备法律、医疗或财务专业资质。
- 不自动安装缺失 Plugin、CLI 或 MCP。
- 不让 Scenario 覆盖 Lumo 核心安全不变量。

## 15. 产品验收标准

### 15.1 创建

- 用户无需手写 YAML 即可完成专属场景创建。
- Designer 只询问影响设计的关键问题。
- 最终文件可读、可手工维护、无 Secret。

### 15.2 一致性

- 场景身份、Prompt、实际工具和 UI 描述一致。
- Office 场景不再自称 Coding Agent。
- 模型不猜测 Provider 或 Model。
- “你能做什么”的回答与有效 Capability Catalog 一致。

### 15.3 安全

- 所有写入和外部操作都能从预览中识别。
- 缺失能力不会被模型描述为已启用。
- Proposal 无法绕过 Resolver、PermissionChecker 或 Sandbox。

### 15.4 生命周期

- 保存前可完整验证。
- Candidate Runtime 构建失败不会破坏当前 Runtime。
- Session 和 Memory 不在场景间静默串用。

## 16. 产品成功指标

- 从自然语言描述到可启动场景的完成率。
- 用户在保存前发现并修正能力或权限问题的比例。
- 场景启动失败率及失败原因分布。
- 模型能力自述与实际工具集合的一致率。
- 用户手工编辑生成配置的频率。
- 场景复用、复制和项目分发数量。

## 17. 已确定的产品决策

1. Scenario 是能力与体验的组合，不是 Tool 白名单。
2. Neutral Kernel 不携带 Coding 身份。
3. 模型生成 Proposal，Lumo 生成最终配置。
4. 用户确认有效 Runtime，而不是确认原始模型文本。
5. 场景 Prompt 与确定性配置分离保存。
6. Capability Catalog 是能力事实来源。
7. Runtime Facts 是 Provider、Model 和能力自述的事实来源。
8. 场景切换创建新 Session。
9. Memory 默认按场景隔离。
10. 缺失能力不会自动安装或被描述为可用。

