# Spec Delta

## ADDED Requirements

### Requirement: 信源与核验工具进入托管工具面

系统 SHALL 在统一工具面新增 `source_*` 命名空间（至少含 `source_search`）与引用核验入口，
二者 MUST 为确定性、可计量的工具，MUST 在托管模式可用并遵守既有托管身份与 fail-closed 约束。
托管工具面 MUST NOT 因新增这些工具而暴露写作编排类知识。

#### Scenario: 托管实例可检索信源

- **WHEN** 托管租户调用 `source_search`
- **THEN** 返回带统一 provenance 的命中
- **AND** 该次调用按租户计量

#### Scenario: 写作编排不进工具面

- **WHEN** 检查托管实例的工具清单
- **THEN** 含 `source_*` 与核验入口
- **AND** 不含写作/选题/结构等编排类工具（它们以技能承载）

### Requirement: 写作编排以技能承载不进工具面

各严肃文本族的选题、结构、证据纪律 SHALL 以技能（skill）形式分发，MUST NOT 以 MCP 工具形态
暴露于本地或托管工具面。工具面只暴露确定性能力（信源检索、核验、渲染），编排知识由技能在
对话中驱动。

#### Scenario: 编排知识位于技能

- **WHEN** 用户需要选题或写作结构
- **THEN** 由技能在对话中提供
- **AND** 工具清单中不存在对应的编排工具