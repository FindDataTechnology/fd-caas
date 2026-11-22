# Video Types

视频类型注册表：每种类型声明输入与 pipeline，对应一个 Remotion composition。首批五类型（carousel / listicle / data-viz / kinetic-quote / news-flash）由 `add-video-types-batch-1` 引入。

## Purpose
VAAS 视频类型注册表：每种视频类型声明输入契约与对应 Remotion composition，支撑可扩展的口播视频模板体系。

## Requirements

### Requirement: 图文轮播视频（carousel）

系统 SHALL 提供 carousel 类型：每段口播配一张图片场景（Ken Burns 推拉 + 交叉淡入转场）。输入 SHALL 为口播稿 + 图片列表；图片项 MAY 为本地路径或 seedream 提示词（类型声明 `generate: true` 时自动补图，补图清单 MUST 在渲染前列出供确认）。图片数少于场景数时 SHALL 循环复用且相邻场景不重复同图。

#### Scenario: 本地图片渲染轮播

- **WHEN** 用户以 script + 3 张本地图片执行 carousel 类型渲染
- **THEN** 产出 mp4 中每个口播段对应一张图的推拉场景
- **AND** 场景切换与口播段边界对齐

#### Scenario: 自动补图需确认

- **WHEN** images 列表中含 seedream 提示词项
- **THEN** 渲染前先完成补图并打印补图清单
- **AND** 用户确认后才进入渲染

### Requirement: 榜单合集视频（listicle）

系统 SHALL 提供 listicle 类型：开场钩子卡 → 倒数条目卡（排名弹出动画）→ CTA。输入 SHALL 为口播稿 + items.json（`[{rank,title,desc,image?}]`）。条目数与口播段数不一致时 MUST 在渲染前报错并列出段数与条目数对照。

#### Scenario: 榜单渲染

- **WHEN** 用户以 script + 3 个条目执行 listicle 渲染
- **THEN** 产出 hook 卡 + 3 张倒数条目卡 + CTA 的视频
- **AND** 每张条目卡与其口播段对齐

#### Scenario: 条目不匹配被拒

- **WHEN** items.json 有 5 条而口播只有 3 个条目段
- **THEN** 渲染前报错并显示 5 vs 3 的对照
- **AND** 不产生任何渲染产物

### Requirement: 数据可视化讲解视频（data-viz）

系统 SHALL 提供 data-viz 类型：动画图表场景（柱状生长/折线描画/饼图展开，含数值动画）随口播段切换。输入 SHALL 为口播稿 + data.json（charts spec，v1 支持 bar/line/pie）。图表配色 MUST 取自 `theme.ts` COLORS。图表组件 SHALL 实现为可复用组件，不绑定单一类型。

#### Scenario: 图表动画渲染

- **WHEN** 用户以 script + 含 bar 与 line 两张图的 data.json 执行渲染
- **THEN** 产出两张带动画的图表场景，切换与口播段对齐
- **AND** 图表颜色全部来自品牌色板

### Requirement: 金句文字动画视频（kinetic-quote）

系统 SHALL 提供 kinetic-quote 类型：纯文字动效，逐句入场、关键词放大变色，文字出现时机与句级字幕对齐。关键词 SHALL 优先采用口播稿中的显式标记。该类型 MUST NOT 依赖任何图片素材。逐字级同步为 stretch 目标，v1 承诺句级对齐（HTML 渲染器口径）。

#### Scenario: 纯文字渲染

- **WHEN** 用户仅提供口播稿（含 `**词**` 标记）执行 kinetic-quote 渲染
- **THEN** 产出逐句入场的文字动画视频
- **AND** 标记词以放大变色样式突出
- **AND** 全程无图片素材依赖

#### Scenario: 句级对齐

- **WHEN** 口播稿含 4 句而音频总时长 T
- **THEN** 每句入场时刻按其在段内比例位置对齐
- **AND** 句切换与句级字幕同步

### Requirement: 热点速报视频（news-flash）

系统 SHALL 提供 news-flash 类型：标题卡 → 要点卡 ×3 → CTA 的固定结构，口播段映射到对应卡片。输入 SHALL 为口播稿 + meta.json（`{headline, points[3], source?}`）。

#### Scenario: 速报渲染

- **WHEN** 用户以 script + meta.json（headline + 3 要点）执行渲染
- **THEN** 产出标题卡、3 张要点卡与 CTA 依口播顺序出现的视频

### Requirement: 类型质量门

本批全部类型 SHALL 满足：随附 `README.md` 与 `example/` 示例输入；用 example 输入可端到端渲出 mp4 且 preflight 通过；以短于预期的音频渲染不崩溃（无负 durationInFrames）；初始 `status` 为 experimental，实发成功一次后升 stable。

#### Scenario: 类型可被新用户照抄使用

- **WHEN** 新用户查看任一类型的目录
- **THEN** 能找到 README 与可直接运行的 example 输入
- **AND** 用 example 渲染出片无需阅读其他文档

### Requirement: 类型渲染器声明

每个类型的 type.json SHALL 含 `renderer` 字段声明其渲染器（`hyperframes` = 类型场景构建器产出 composition，经 presentation-plane 的 type 模式交视频引擎渲染；`remotion` 为历史值，其管线不再是 render_video 的执行体）。type.json 的 inputs/pipeline 声明 SHALL 保持为类型输入契约的唯一真源；渲染器切换 MUST NOT 改变 inputs 契约。

#### Scenario: 渲染器切换不改输入契约

- **WHEN** 类型 type.json 标注 renderer=hyperframes
- **THEN** 该类型的 inputs 校验规则与切换前一致
- **AND** 既有调用方以相同 inputs 调用无需修改

#### Scenario: 结构校验前置不变

- **WHEN** 类型管线声明 validate-structure（如 news-flash 的要点数对照）
- **THEN** 场景构建器在渲染前执行同等结构校验
- **AND** 校验失败的报错形态与既有口径一致（列出对照、零部分产物）

### Requirement: 代码生视频的正式入口

由程序数据（而非口播稿 + 素材清单）驱动成片 SHALL 走 `render_video` 的
`html_full` 场景类型：调用方提供完整 HTML 片段，渲染面按 composition 契约逐帧重放，
同一输入产出同一字节流。

系统 MUST NOT 为每一种结构化数据源新增一个视频类型；结构化数据源到画面的映射
属于调用方，类型注册表只承载可复用的成品形态。当某数据源的映射稳定且被第二个调用方
复用时，才 MAY 提升为新类型。

低成本成片形态（图文轮播、金句、速报等既有类型）SHALL 被视为本入口的现成实现，
MUST NOT 因引入程序化入口而另造一套等价的「幕布 / 时间线」体系。

#### Scenario: 结构化数据直接进渲染

- **WHEN** 调用方以 `html_full` 场景提交由自身数据生成的 HTML 片段
- **THEN** 渲染面按 composition 契约逐帧重放并产出成片
- **AND** 相同输入重复渲染得到相同字节流

#### Scenario: 不为单一数据源新增类型

- **WHEN** 某产品线希望把自己的一种结构化产物做成视频
- **THEN** 它先以 `html_full` 实现，不新增 type
- **AND** 仅当该形态被第二个调用方复用时才讨论提升为类型

#### Scenario: 不并存第二套时间线体系

- **WHEN** 需要「图 + 转场 + 字幕」的成片
- **THEN** 复用既有类型（如 carousel）的声明式输入
- **AND** 不引入独立的时间线 / 幕布编辑器作为第二条产出路径
