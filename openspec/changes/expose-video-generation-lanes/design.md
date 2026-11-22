# Design

## Context

见 proposal.md - Why。技术现状：

- `generate_video_submit(prompt, duration, ratio, resolution, first_frame, last_frame, slug)`
  → `generators/video.py::_build_cmd` → `litellm-bridge.py video --first-frame <str>`
  → `_ciyuan_video.submit`（`content[]` 里 `role: first_frame`）
  或 `_grok_video.submit`（`payload["image"] = <str>`）。
  两条驱动都把 `first_frame` 当**字符串直接塞进 JSON**，没有任何本地文件读取。
- `render_video` 有两条路：storyboard 模式（`html_intro`/`html_outro`/`kenburns`/`html_full`）
  与 type 模式（六种已注册类型，`renderer: hyperframes`）。二者都产 composition 交渲染面。
- `_grok_video.py` 顶部注释称「sub2api 计费谓词等 `done`，所以视频完成也不落账」。
  sub2api 现状：`grokVideoStatusSuccessWords = {done, completed, succeeded}` —— 已修。
- `pricing.json` 的 `generate_video_submit` / `generate_video` 均为 `0.0`。
- 车道：`video.grok` active（11-15）、`video.ciyuan` down（组 9 封禁）、
  `render.local` active（本地算力，零成本）。

## Goals / Non-Goals

**Goals**
- 图生视频真的能用：本地图能进、首帧真的生效、失败在付费前拦下。
- 程序数据（剧创这类）有一条明确的成片入口，且不新增类型。
- 视频计价不再是 0，出片有账。

**Non-Goals**
- 视频编辑 / 视频延长（`/v1/videos/edits`、`/v1/videos/extensions` 不接）。
- 多镜自动衔接、首尾帧接力（不做，理由是收益不确定、`last_frame` 在 grok 车道本来被忽略）。
- 时间线/幕布编辑器。
- 视频模型自选、微调、自托管。

## Decisions

### D1. 首帧的三种形态，按能力降级而非按车道猜

**选择**：`first_frame` 接受（a）http(s) URL、（b）本地绝对/相对路径、（c）VAAS asset id。
本地形态在驱动层转成上游可接受形式：**先尝试上传换 URL**（词元若提供上传端点），
**否则用 data URI**。两条都不可用时在提交前失败并给出明确分类。

**为什么**：上游协议族不同——`_ciyuan_video` 走 `content[].image_url.url`（吃 URL 或 data URI），
`_grok_video` 走 `payload.image`（吃 URL；data URI 是否被接受未验）。
在驱动层做转换，调用方只需给"一张图"，不用知道车道差异。

**备选**：要求调用方自己先把图上传换 URL。把车道差异暴露给每个调用方，
且剧创这类本地出图的消费方每次都要自己实现一遍上传——不可取。

### D2. 首帧生效与否用「对照实验」判，不靠读上游响应

**选择**：探针用同一提示词跑两次（有首帧 / 无首帧），比较产物首帧像素。

**为什么**：上游不会告诉我们 "I used your first frame"。`_grok_video` 的
`payload.setdefault("image", ...)` 只能证明我们**发了**，不能证明上游**用了**。
这跟图片编辑那边「200 返回原图」是同一类问题，判据也应当是同一类：看产物差异。

**限制（写进代码注释）**：像素差异只对"首帧被尊重"必要不充分——上游可能用了首帧但
经过大量重绘。因此判据取「首帧帧与源图像素距离显著小于"无首帧产物首帧"与源图的距离」，
而不是绝对阈值。

### D3. 代码生视频不新增类型

**选择**：结构化数据源（剧创等）走 `render_video(html_full=...)`；共用既有六类型的声明式输入。

**为什么**：`video-types` 的六个类型已经覆盖了"图文轮播 / 榜单 / 图表 / 金句 / 速报 / 录屏"。
剧创的分镜本质是"每镜一张图 + 每镜一句台词"，**就是 carousel**。为它新建 `storyboard`
类型等于把同一件事实现两遍，且第二次实现必然先于第一次腐烂。

**备选**：新增 `storyboard` type。代价是需要自己的 builder、type.json、README、example，
且与 carousel 的能力差异只在"图从哪来"。收益不抵成本。

> shortcut: 若剧创需要 carousel 表达不了的镜头语义（如逐镜时长不同、镜头间特写切换），
> 升级点是给 carousel 的 inputs 增加 `segments[].duration_s` 覆盖，而不是新类型。

### D4. 视频计价：先填真价，再开放调用

**选择**：`generate_video_submit` / `generate_video` 从 `0.0` 改真价；
零价拒启闸与图片那条共用同一实现（`expose-image-edit-lane` D5）。

**为什么**：`video.grok` 11-15 已 active 且真记账（`usage_logs` 有行），
但我们出片收 0。这条车道一旦被剧创消费就是持续成本。

**风险**：真价不在仓内（`pricing.json` 注释要求按牌价核对）。这一项必须人工补值，
本设计的任务里明确标为"人填"。

### D5. 计费注释按实测复核，不照抄旧结论

**选择**：`_grok_video.py` 的「视频完成不落账」注释按 sub2api 现状订正；
结论以一次真实调用后查 `usage_logs` 为准。

**为什么**：旧注释可能让下一个人主动去"修"一个已经好了的东西，或者反过来信任一个
已经坏了的结论。注释过期和代码过期一样会害人。

## Risks / Trade-offs

| 风险 | 缓解 |
|---|---|
| 上游不接受 data URI 首帧 | D1 三级降级；探针分别覆盖 URL 与 data URI；都不通则图生视频标不支持 |
| 首帧被使用但重绘过头 | D2 的相对距离判据；探针报告写诊断量而不只写布尔 |
| 视频单价填错 | 计价走配置不入代码；任务里标注"人填"并写取值日期 |
| `render_video` 的 html_full 被滥用为任意 HTML 入口 | 渲染面已有外链硬校验与零外链约束；本 change 不改这些 |

## Migration Plan

1. 探针先行（首帧对照实验），结论写回车道台账。
2. 首帧三种形态的转换按探针结论启用（能用的形态才暴露在文档里）。
3. 计价真值补齐后重启 hosted 实例。
4. 回滚：首帧参数退回只收 URL（旧行为）；计价回滚只影响账不影响出片。

## Open Questions

- 词元是否提供图像上传端点（换取可公网获取的 URL）？当前设计按"优先尝试上传、
  失败回退 data URI"实现；若上游明确提供上传，可去掉 data URI 分支。
- `render_video(html_full=...)` 是否应支持从 VAAS 资产 id 直接引用图片？
  当前要求调用方给文件路径或 data URI；若被第二个消费方需要再讨论。
