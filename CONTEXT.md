# CaaS（fd-caas）

内容即服务：把一份需求变成可被看见/听见的内容（视频、图文、图表、文档、语音），
并按收件方的能力投递出去。两条能力切面：生成面托管、发布面永不做多租户托管。

> 曾用名 VAAS（Variable Asset Authoring & Syndication）。改名是因为那个缩写只说
> 「video」，而这条线现在做全部形态；`-skills` 也低估了它（27 技能 + 25 个 MCP 工具
> + 独立渲染面）。仓库名 `fd-caas`，服务名仍是 `/vaas-wanxing`（运行时契约不改）。

## Language

### 能力切面

**生成面 (Generation Plane)**:
CaaS 中可多租户托管的服务工具族：素材生成、编排、素材库与注册表查询。
_Avoid_: 创作端（那是本机技能语境词）

**发布面 (Publish Plane)**:
CaaS 中依赖真实平台登录态、发布前必须人工确认的工具族；只在本机半自动运行，永不做多租户托管。
_Avoid_: 分发面、自动发布

**投递 (Delivery)**:
一次定向送达：信封 `to`（收件方）/ `forms`（可用形态）/ `ack`（期望回执强度）/
`correlation`（幂等键）。与「广播到平台」相对——发布面管后者，投递管前者。
形态闭集与收件方能力表在 `mcp_server/caas-forms.json`，是唯一权威。
_Avoid_: 发送、推（口语）
