## MODIFIED Requirements

### Requirement: 生成方式注册

系统 SHALL 提供生成方式注册表，每一条目 SHALL 含 `name`、`provider`、`model`、`driver`（可执行适配器路径）。注册表 SHALL 按资产类型分组，类型至少覆盖 video/copy/image/voice/cover，并扩展 chart 与 doc（确定性渲染驱动与云生成驱动同表注册、同一调度入口）。同一类型下 MAY 有多个 provider 的实现。

image 类型 SHALL 至少含两种方式：文生图与带源图的编辑。两者 MUST 是各自独立的条目，
MUST NOT 用「是否传了源图」在同一个条目上分叉行为——同一 name 的产物来源与重放键语义
必须唯一。

#### Scenario: 同一类型多 provider
- **WHEN** 系统配置了 video 类型下的 seedance（volcengine）与 voiceover（local/remotion）两种方式
- **THEN** 两者都出现在 video 生成方式清单中
- **AND** 客户端可按 name 精确调度

#### Scenario: 确定性渲染驱动同表注册
- **WHEN** 系统注册 chart 类型下的声明式图表驱动（local）与 doc 类型下的分页文档驱动（local）
- **THEN** 两者分别出现在 chart 与 doc 生成方式清单中
- **AND** 与云生成驱动走同一注册表与同一按名调度入口

#### Scenario: 文生图与编辑并存
- **WHEN** 客户端列出 image 类型生成方式
- **THEN** 文生图与编辑各自是一条独立条目，各自带 provider/model
- **AND** 调用编辑条目必须显式传源图，否则按参数缺失报错，不退回文生图

### Requirement: 生成调度
系统 SHALL 通过 `generate.*` 工具按注册的 name 调度生成。未知 name SHALL 返回明确错误并列出可用方式。生成结果 SHALL 写入统一数据库的 `assets` 表并记录 `provider` 字段。

编辑调度的产物 SHALL 记录它由哪一条资产编辑而来（父资产引用），使「第几次改、从哪张改」
可从账本单向回溯，MUST NOT 只留一个无来源的新资产。

#### Scenario: 按名生成视频
- **WHEN** 客户端调用 `generate.video(name="seedance", prompt=..., ...)`
- **THEN** 系统按注册表查找 driver 执行生成
- **AND** 成功后 assets 表新增一条 type=video、provider=volcengine 的记录
- **AND** 返回 id/slug/file_path

#### Scenario: 未知生成方式
- **WHEN** 客户端调用 `generate.video(name="nonexistent")`
- **THEN** 系统返回错误
- **AND** 错误信息中列出当前可用的 video 方式清单

#### Scenario: 编辑产物的血缘
- **WHEN** 客户端对一张既有资产提交编辑并成功
- **THEN** 新资产的父引用指向源资产
- **AND** 从源资产出发可列出其全部编辑后代，方向单一不产生环

### Requirement: 方式可发现性

系统 SHALL 提供 `registry.list_generators(type?)` 工具，返回可用生成方式清单（name/provider/model 及简述）。无 type 时 SHALL 返回全部类型清单，含 chart 与 doc 类型。清单中依赖外部车道的条目 SHALL 携带该车道健康状态（`health`: active/degraded/down、原因类别、最后验证时间），与车道缺陷台账同源；`models_voices` 等其余发现工具 SHALL 同样透出语音车道状态。发现面 MUST NOT 将 down 车道呈现为可选用能力。

编辑方式 SHALL 与文生图分别携带各自车道的健康状态：文生图车道可用而编辑车道 down
是允许的组合，发现面 MUST 如实呈现该差异，MUST NOT 用其中一个的状态代表另一个。

#### Scenario: 发现全部生成方式

- **WHEN** 客户端调用 `registry.list_generators()`
- **THEN** 返回按 type 分组的全部生成方式（含 chart/doc）
- **AND** 每项含 name、provider、model 字段

#### Scenario: 清单携带车道状态

- **WHEN** 图像上游车道处于 down 时客户端列出 image 生成方式
- **THEN** 对应条目 health=down 且附原因类别与最后验证时间
- **AND** 语音车道 active 条目同表可对比，租户可先决策再调用

#### Scenario: 生成可用而编辑不可用

- **WHEN** 文生图车道 active 而编辑车道 down
- **THEN** 两条 image 条目分别携带各自状态
- **AND** 调用编辑条目仍被拒绝，不因同类型另有可用条目而放行
