# source-lane Specification

## Purpose
CaaS 的信源层：按内容垂域把检索派发到外部信源 MCP 面，返回带统一 provenance 的命中，
并让车道可注册扩展而核心零改动。它让 CaaS 第一次能「找到真来源」，从而产出有出处的
严肃文本，而 CaaS 仍是编排层、不自己采集。

## Requirements

### Requirement: 信源车道注册表可扩展

系统 SHALL 以注册表声明信源车道：每车道声明域标识、寻址的外部信源 MCP 面与工具、以及
派发驱动。新增车道 MUST 只需一条注册条目加一个驱动，MUST NOT 改动派发核心。车道派发
MUST 是**适配器**——只包装对既有信源 MCP 面的调用，MUST NOT 内建采集或另起爬取通道。

#### Scenario: 新增车道不改核心

- **WHEN** 接入一个此前没有的信源域
- **THEN** 交付物仅为一条车道注册条目与其驱动
- **AND** 派发核心模块不被改动

#### Scenario: 车道是适配器不是采集器

- **WHEN** 一次检索经某车道返回命中
- **THEN** 结果来自该车道声明的外部信源 MCP 面
- **AND** CaaS 不发起任何自主采集

### Requirement: source_search 统一派发

系统 SHALL 提供 `source_search`：以域标识与查询为输入，派发到对应车道声明的信源面并返回
命中列表。不存在或未注册的域 MUST 报错并列出当前可用域，MUST NOT 静默退回某个默认源。

#### Scenario: 按域派发

- **WHEN** 调用方以已注册域与查询调用 source_search
- **THEN** 系统经该域车道检索并返回命中列表
- **AND** 每条命中携带统一 provenance

#### Scenario: 未注册域被拒

- **WHEN** 调用方使用一个未注册的域标识
- **THEN** 系统报错并回显可用域清单
- **AND** 不派发到任何信源、不返回结果

### Requirement: 统一 provenance 与空结果原因

每条命中 SHALL 携带 provenance：`source`、`stable_id`、`as_of` 三元组（沿用工作区
`text-query-surface` 契约），MAY 附覆盖度字段（如 `coverage`、`metadata_only`、
`matched_terms`）。空结果 MUST 附带原因（如 quota/upstream/unreachable/not_indexed），
MUST NOT 静默为空。上游失联 MUST 以可判别的方式上报，MUST NOT 以空命中冒充「无此内容」。

#### Scenario: 空结果必带原因

- **WHEN** 检索无命中或上游异常
- **THEN** 返回空结果并附原因
- **AND** 不以空列表静默冒充「该域没有内容」

#### Scenario: 覆盖度不完整如实标注

- **WHEN** 某车道只能提供部分覆盖（如中文文献走开放元数据兜底）
- **THEN** 命中的 provenance 标出覆盖不完整与命中的查询词项
- **AND** 不把部分覆盖表述为完整覆盖

### Requirement: 信源车道受租户归属与计量约束

托管模式下，source_search 的用量 SHALL 按租户计量；不得因信源派发而绕过既有的租户归属与
计量规则。信源面返回的凭据（如 BYOK 授权通道）MUST NOT 出现在持久化存储、日志或响应体中。

#### Scenario: 托管计量

- **WHEN** 托管租户发起一次 source_search
- **THEN** 该次调用记入该租户用量
- **AND** 不出现跨租户可见的命中

#### Scenario: 凭据不外泄

- **WHEN** 某车道使用使用者自备的凭据检索
- **THEN** 凭据不出现在持久化存储、日志或响应中
