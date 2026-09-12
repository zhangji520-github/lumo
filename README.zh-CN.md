# Lumo Agent Harness

[English](README.md) | **简体中文**

一个面向终端的 Agent Harness，用于组合场景专属的 Agent Runtime，并在
同一个项目中安全地协调多个 Agent。

![Lumo Coding Runtime 启动台](assets/screenshots/coding.png)

## 为什么选择 Lumo

### 编译式长期记忆

Lumo 可以把透明、可人工编辑的 Markdown 记忆与
[GBrain](https://github.com/garrytan/gbrain) 编译式知识大脑组合起来。你可以选择
`markdown`、`gbrain` 或 `hybrid`：混合模式让 Markdown 保存少量必须常驻的规则，
由 GBrain 提供带来源的事实、实体关系、关键词/向量检索、纠错与撤回，以及跨来源综合。

长期记忆不再依赖模型“碰巧想起来调用工具”。Lumo 会在每个 Session 首次进入时预热
GBrain，在第一次模型推理前完成相关记忆召回，并在上下文压缩后重新注入。GBrain
不可用时，读取会 fail-open 降级到 Markdown；经过用户明确授权的 `remember` 如果遇到
瞬时传输失败，则进入本地 outbox 等待重试。权限或参数错误不会绕过安全边界。

```yaml
memory:
  mode: hybrid
  gbrain_server: gbrain
  recall_timeout_seconds: 2.0
  recall_budget_tokens: 2000
  auto_capture: false

mcp_servers:
  - name: gbrain
    command: gbrain
    args: [serve, --surface, verbs]
    env:
      GBRAIN_HOME: ${LUMO_GBRAIN_HOME}
```

启动 Lumo 前，需单独初始化可选的本地大脑：

```powershell
bun install -g github:garrytan/gbrain#latest-stable
gbrain init --pglite --no-embedding
gbrain doctor --json
```

集成只依赖 GBrain 小而稳定的 `MEMORY_VERBS v1` 七动词协议，而不耦合其内部实现，
既避免把庞大的工具目录塞进 Agent，也保留未来替换记忆后端的能力。GBrain 自动捕获
对话默认关闭；显式保存的记忆必须携带 provenance。

### 可组合的场景 Runtime

场景不只是工具预设。Lumo 可以将内置能力包、MCP 服务、外部 CLI 工具、
Python 插件、Skill、Prompt、记忆、功能开关和安全策略组合成经过校验的
`RuntimeSpec`。

每个场景都有自己实际生效的工具集合、身份、Prompt、权限、记忆命名空间和
Session。TUI、Headless CLI 和 Remote 入口共用同一套 Resolver 与
RuntimeBuilder，确保声明的场景与真正执行的 Runtime 始终一致。

![Lumo Office Runtime 启动台](assets/screenshots/office.png)

### 多 Agent 协作 Harness

Lumo 可以把大型任务拆分给 Coordinator 和多个 Worker Agent。每个 Worker
使用独立的 Git Worktree 隔离文件修改，Agent 之间则通过文件邮箱异步传递
结构化的任务分配、进度更新和关闭请求。

任务拆分、并行执行、进度跟踪和代码隔离都由 Harness 提供，而不是只依赖
Prompt 约束 Agent 自行协调。

![Lumo Empty Runtime 启动台](assets/screenshots/empty.png)

## 开发

```powershell
uv sync
uv run lumo --help
uv run pytest
```

配置默认从 `.lumo/config.yaml` 加载。已有的 `.mewcode` 项目数据可以迁移，
且不会覆盖现有 `.lumo` 文件。
