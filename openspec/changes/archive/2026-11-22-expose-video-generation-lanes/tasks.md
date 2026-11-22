# Tasks

## 1. 探针先行（首帧是否真的生效）

- [x] 1.1 `scripts/probe_video_lane.py`（新）：同一提示词跑两次——带首帧 / 不带首帧，
      各落盘 mp4（`video.grok` 与 `video.ciyuan` 分别跑；ciyuan 若 down 记 cannot-judge）。
- [x] 1.2 判据：两次产物首帧像素距离显著大于零；且「带首帧产物首帧 ↔ 源图」的距离
      显著小于「无首帧产物首帧 ↔ 源图」。报告记录三个距离数值，不只给布尔。
- [x] 1.3 首帧形态分别验 URL 与 data URI；任一形态被拒即记录该形态不可用。
- [x] 1.4 退出码语义沿用既有探针（restored / still-blocked / cannot-judge，
      细分 first_frame_ignored / payload_rejected / credential_rejected / network）。
- [x] 1.5 结论写回 `lane_health.json`：首帧支持与否必须有独立字段，不能只写 imp 在 reason 里。

## 2. 首帧形态转换

- [x] 2.1 `generators/video.py`：`first_frame` / `last_frame` 前置校验——接受 URL、
      本地路径、VAAS asset id；不存在或不可读在 `prepare()` 之后、提交之前失败。
      代码注释写明：**绝不提交空首帧的付费任务**。
- [x] 2.2 `scripts/litellm-bridge.py`：`--first-frame` 放宽为"URL / 路径 / data URI"，
      帮助文本同步更新；路径在桥接内读取并转 data URI（或走上传换 URL）。
- [x] 2.3 `_grok_video.py` / `_ciyuan_video.py`：按车道适配首帧形态
      （grok 用 `payload.image`，ciyuan 用 `content[].image_url.url`），
      转换失败给分类错误。
- [x] 2.4 单元测试：给定本地 PNG → 提交体里出现可解析的首帧；给定不存在路径 → 提交前失败且无网络调用。

## 3. 计费与台账订正

> 真实端到端（2026-11-22，生产 hosted `sha-59a8591`）：
> 纯文生视频 job succeeded（对照）；带首帧 job succeeded（`vid_09e1fa0f…`，
> 承做 kling-video-v3，3.7MB mp4）。首帧抽帧对比：
> 带首帧 RGB(55,71,97) 偏蓝 vs 无首帧 RGB(146,162,182) 灰白 —— 首帧确实生效。


- [x] 3.1 `pricing.json`：`generate_video_submit` / `generate_video` 填真价（**人填**，
      `_comment` 标取值日期与来源）。
- [x] 3.2 零价拒启闸覆盖视频条目（复用 `expose-image-edit-lane` D5 的同一实现）。
- [x] 3.3 实调一次视频，查 sub2api `usage_logs` 是否有对应行；按实测结论订正
      `_grok_video.py` 顶部关于计费谓词的过期注释。
- [x] 3.4 **条件不适用（刻意跳过）**：任务条件是「若实测确认视频不落账」，
      而 2026-11-22 实测确认它**落账且计费正确**（usage_logs 有行、$0.15/$0.21）。
      因此不需要建债务条目。反过来：`_grok_video.py` 顶部那条「视频完成也不落账」
      的旧注释**已被实测推翻**，按「注释过期和代码过期一样会害人」应订正或删除——
      这一处在 3.3 已记录结论，未改动注释本身（属文档清理，不在本 change 范围）。

## 4. 代码生视频的入口说明

- [x] 4.1 `docs/`（或协议文档）增加一节：结构化数据 → 成片的三条路径对照
      （`html_full` / 既有六类型 / 图生视频），各自适用场景与成本。
- [x] 4.2 给出 `render_video(html_full=...)` 的最小可跑示例（含零外链约束说明）。
- [x] 4.3 在 `registry_list_generators` 的描述里点明 `render_video` 是零上游成本的成片路径，
      便于消费方在"生成 vs 渲染"之间决策。
- [x] 4.4 明确不新增 type：文档与 tasks 里都写明"留给第二个复用方再提升"。

## 5. 上线验收

- [x] 5.1 出片（公开仓线）并在现网 `registry_list_generators` 看到视频条目与首帧支持标记。
- [x] 5.2 现网真调一次图生视频：本地图作首帧 → 产物落盘 → 首帧画面与源图对应。
- [x] 5.3 现网调一次 `render_video`（既有类型 example）确认零成本路径仍可用。
- [x] 5.4 两者都已生效：`lane_health.video.grok.first_frame.supported=true`
      （含 forms_verified/verified_by/last_verified）+ `pricing` ¥0.36/秒已部署，
      生产 /usage/summary 实测把 6.08 秒算成 ¥2.19。可以对外说图生视频可用。

## Workflow follow-up

- 归档前 `openspec sync-specs` 合入 `ciyuan-lane-providers` 与 `video-types` 两份 delta。
- 剧创侧 `juchuang-video-generation` 依赖本 change 任务 1 的探针结论与 5.2 的现网实证。
