# Proposal

## Why

视频能力在 VAAS 里其实有三条，但只有一条能真用：

1. **真生成**（`generate_video_submit` → 词元 `/v1/videos`）— 车道 `video.grok` active，
   但 `first_frame`（图生视频）只接受 **URL 字符串**：`litellm-bridge` 的
   `--first-frame` 帮助文本明写 "First frame image URL"。我们自己生成的图落在
   磁盘相对路径上，本地没有对外 URL 时这条参数根本喂不进去——**图生视频名义存在、实际断路**。
2. **代码生视频**（`render_video` 的 `html_full` / `type` 六种类型）— 已完整可用、
   零上游成本、确定性可重放，但剧创这类结构化数据源还没人用过它。
3. **低成本成片**（`carousel` 图 + Ken Burns + TTS、`kinetic-quote` 纯文字）—
   已在 `video-types` 里，正是"低成本幕布"的现成形态，不需要新造。

所以缺口不是"要买视频能力"，而是：(a) 图生视频的入口收不到本地图，(b) 代码生视频没有
被结构化产品用起来，(c) 视频计价仍是 0 而视频车道是 active，一出片就在白送。

## What Changes

- `first_frame` / `last_frame` 接受**本地资产或文件路径**，不再只吃 URL：
  驱动层把本地图转成上游可接受的形式（优先上传换取 URL，无上传通道时用 data URI），
  源图不存在或不可读在**提交前**失败——绝不提交一个空首帧去付费。
- 图生视频的**首帧确实生效**成为可判定事实：上游 `_grok_video.py` 用
  `payload.setdefault("image", ...)` 传首帧，但从未验证过承做方是否真的用它。
  新增探针判据：同一提示词、有/无首帧各出一次，产物首帧像素必须不同。
- 代码生视频与低成本成片**不新增类型**：`render_video` 的 `html_full` 与既有六类型
  已是交付面；本 change 只补一条"从结构化镜头数据编译 storyboard"的契约说明与示例，
  让下游知道这条路存在。
- 计价：`generate_video_submit` 从 `0.0` 改为真价；`_load_pricing(strict=True)` 的
  零价拒启闸（见 `expose-image-edit-lane` D5）同时覆盖视频。
- 订正过期注释：`_grok_video.py` 写着「sub2api 计费谓词等 `done`，所以视频完成也不落账」。
  该仓 `grokVideoStatusSuccessWords` 现为 `{done, completed, succeeded}`，注释已过期。
  以实测复核替代照抄，结论写回车道台账。
- **不做**：视频编辑（`/v1/videos/edits`）、视频延长、时间线/幕布编辑器、
  多镜自动衔接。不做视频模型自选或微调。

## Capabilities

### New Capabilities

（无——扩既有两条能力。）

### Modified Capabilities

- `ciyuan-lane-providers`: 「视频车道异步任务与轮询」增加图生视频的首帧约束
  （本地源可入、缺失即拒、首帧生效可判定）。
- `video-types`: 增加「代码生视频与低成本成片不新增类型」的边界，并写明
  `html_full` 是结构化数据源进视频的正式入口。

## Impact

- **代码**
  - `mcp-server/mcp_server/generators/video.py`：`first_frame` 前置校验与本地图解析。
  - `scripts/litellm-bridge.py`：`--first-frame` 接受路径，转 URL/data URI 后再提交。
  - `scripts/_grok_video.py` / `_ciyuan_video.py`：首帧形态适配与失败分类；
    订正过期计费注释。
  - `scripts/probe_video_lane.py`（新）：有/无首帧对照探针。
  - `mcp-server/mcp_server/lane_health.json`：`video.grok` 复验戳 + 计费实测结论。
  - `mcp-server/pricing.json`：`generate_video_submit` / `generate_video` 真价。
  - `docs/`：代码生视频入口说明（`render_video` 的 `html_full` 与六类型对照表）。
- **接口**：加性。`first_frame` 从"只收 URL"放宽为"收 URL 或本地路径"，
  旧调用不受影响。
- **风险**
  - data URI 首帧可能被上游拒（体积）。探针必须覆盖这一形态；若被拒，退到
    「先上传换 URL」路径，两条都通不过则图生视频降级为不可用并在车道台账写明。
  - 视频单价变动会直接影响剧创成本预估；真价未确认前不得开启对客户计费。
