# ciyuan-lane-providers Specification

## Purpose
萬星·词元（`router.finddatatech.cloud`，self-fork 的 sub2api）作为 VAAS 语音/图像/视频
生成的后端车道：规定三条车道（TTS / 图像 / 视频）的端点契约、可用模型白名单、产物落盘
口径与失败语义，使 VAAS 的生成调用走自家网关与账本，且不依赖第三方 LLM 桥接库。

## Requirements

### Requirement: 词元车道作为生成后端

VAAS 的语音、图像、视频生成 SHALL 支持以萬星·词元车道 API 为后端，与火山方舟直连并列
为可选的 provider 档位；选择由凭证记录声明，MUST NOT 由代码硬编码。

#### Scenario: 声明 ciyuan 档的凭证走词元车道

- **WHEN** 某次生成调用解析到的凭证记录 provider 为 `ciyuan`
- **THEN** 该调用请求词元车道端点（语音 `/v1/audio/*`、图像 `/v1/images/generations`、
  视频 `/v1/contents/generations/tasks`）
- **AND** 请求携带该凭证的 key 作为 Bearer 鉴权

#### Scenario: 声明 volcengine 档的凭证保持既有行为

- **WHEN** 凭证记录 provider 为 `volcengine`（或缺失）
- **THEN** 调用行为与本 change 之前完全一致

### Requirement: 语音车道（TTS）合成

TTS 生成 SHALL 以 OpenAI 音频协议调用词元 `POST /v1/audio/speech`，并把返回的音频流
按 VAAS 落盘约定写成产物文件；音色与模型由凭证记录提供，默认值 MUST 为实测可服务的组合。
当 provider 未返回逐字时间戳时，TTS 出口层 SHALL 合成字幕（fallback captions），
使 voiceover 管线在任一 provider 下都拿到完整 Caption[]，MUST NOT 因 provider 分叉。

#### Scenario: TTS 合成成功落盘

- **WHEN** 调用者请求一段文本的语音合成且凭证为 ciyuan 档
- **THEN** 系统以凭证声明的 model 与 voice 请求 `/v1/audio/speech`
- **AND** 返回的音频字节写入本次生成的产物路径
- **AND** 响应返回该产物的 slug 与相对路径，形状与既有 TTS 响应一致

#### Scenario: 词元语音车道上游不可用

- **WHEN** 词元语音车道返回车道级错误（如 `lane_upstream_error` /
  `lane_upstream_unavailable`）
- **THEN** 该次生成失败并向上暴露含车道标识的错误信息
- **AND** MUST NOT 把上游 5xx 伪装成成功，MUST NOT 产出空或占位音频文件

#### Scenario: 超上游长度上限时分段合成

- **WHEN** 口播文本超过 provider 单次请求长度上限（腾讯 TTS 实测 ~170 字）
- **THEN** TTS 出口层按句切分为 ≤150 字的段，逐段合成并拼接（段间短静音）
- **AND** captions 按段级实测时长对齐（段间时长计入时间轴）
- **AND** 单段重试有退避上限；任一段最终失败 SHALL 整体失败，MUST NOT 产出残缺音频
- **AND** 段间调用 MUST 有间隔，MUST NOT 因连续失败触发上游熔断扩大故障

#### Scenario: 无官方逐字戳时合成字幕兜底

- **WHEN** provider 返回音频但未返回逐字时间戳（词元档常态）
- **THEN** TTS 出口层以口播文本切分 token（中文按字、标点并入前字），
  生成 Caption[]（text/startMs/endMs/timestampMs），并以 ffprobe 实测音频时长
  对全部 token 时间戳做线性缩放对齐
- **AND** 合成结果随音频一起落盘为 captions 文件，形状与官方逐字戳完全一致
- **AND** 结果元数据标记 `captions_source: synthetic`；官方逐字戳存在时标记 `official`

#### Scenario: 合成字幕过管线门禁

- **WHEN** 使用 synthetic captions 走 voiceover 管线（fix-tts-timings → preflight → 渲染）
- **THEN** preflight 的音画三方对齐校验通过（字幕末帧与音频实测时长偏差 ≤ 容差）
- **AND** fix-tts-timings 对 synthetic captions 幂等无改动（其 token 时长由构造保证均匀）

#### Scenario: manifest 记录字幕来源

- **WHEN** 一次 TTS 任务写入 task.json
- **THEN** tts 段 SHALL 包含 provider、model、voice 与 captions_source
  （official | synthetic），成片口径可审计

### Requirement: 图像车道生成与模型白名单

图像生成 SHALL 以 OpenAI 图像协议调用词元 `POST /v1/images/generations`，并仅使用实测
返回合规 JSON 产物的模型；返回非 JSON（如 HTML）的模型 MUST NOT 作为默认或推荐值。

产物形态 SHALL 同时接受 `data[].b64_json` 与 `data[].url` 两种：前者直接解码落盘，
后者下载后落盘。**两种形态的校验 MUST 同等级别**：解码或下载所得字节 MUST 通过图像魔数
识别，识别不出 SHALL 失败且 MUST NOT 写盘（避免产出打不开的假产物）。下载另 MUST 只允许
http/https、设连接与读取超时、设体积上限，并按 `Content-Type` 与魔数双重校验。
产物扩展名 MUST 取实际容器类型，MUST NOT 沿用请求的 `output_format`；二者不一致时
SHALL 在响应 `warnings` 中如实披露。

#### Scenario: 图像生成成功落盘

- **WHEN** 调用者请求文生图且凭证为 ciyuan 档
- **THEN** 系统请求 `/v1/images/generations` 并解析响应 `data[]` 的产物
- **AND** `b64_json` 形态直接解码、`url` 形态先下载，二者都按 VAAS 命名约定写入产物目录
- **AND** 响应返回产物相对路径列表，形状与既有图像响应一致

#### Scenario: 图像车道返回非 JSON 应答

- **WHEN** 词元图像车道对某模型返回 `Content-Type: text/html` 或不可解析的响应体
- **THEN** 该次生成失败并明确报错（说明该模型应答形状不合规）
- **AND** MUST NOT 从 HTML 中猜测或提取图片 URL 充当产物

#### Scenario: 默认图像模型可服务

- **WHEN** 使用 ciyuan 档凭证且调用者未指定模型
- **THEN** 系统使用凭证记录声明的模型；未声明时回落到实测可服务的白名单值
- **AND** 已知返回 HTML 或不可路由的模型 MUST NOT 出现在白名单内

#### Scenario: 带源图的编辑返回可区分产物

- **WHEN** 调用者提交一张源图与一条修改指令
- **THEN** 系统请求 `/v1/images/edits`，产物按与生成相同的魔数与体积校验落盘
- **AND** 产物字节与源图不同；若完全相同则该次调用判失败并说明上游未执行编辑

#### Scenario: 源图数量超上限

- **WHEN** 源图与补充参考图合计超过上游声明的上限（Grok 媒体车道为 3）
- **THEN** 系统在发起调用前拒绝，错误信息指明上限与实际数量
- **AND** MUST NOT 丢弃多余图像后继续提交

#### Scenario: 编辑车道无可用后端

- **WHEN** 编辑车道健康状态为 `down`
- **THEN** 编辑调用以车道不可用失败返回，附原因类别与最后验证时间
- **AND** MUST NOT 静默回退为一次文生图调用

#### Scenario: 图像车道返回 url 形态产物

- **WHEN** 上游返回 `data[].url` 而未返回 `b64_json`（只回 URL 的 OpenAI 兼容上游）
- **THEN** 系统下载该 URL 并落盘为图像产物，响应形状与 `b64_json` 形态一致
- **AND** 下载体非图像（HTML/JSON 错误体）、超出体积上限或超时 SHALL 失败并说明原因
- **AND** MUST NOT 把上游 URL 直接当作产物路径返回给调用者

#### Scenario: b64 产物同样要过魔数校验

- **WHEN** 上游在 `data[].b64_json` 里返回的不是图像（例如 HTML 错误页的 base64）
- **THEN** 该次生成失败并指出首字节内容
- **AND** MUST NOT 把该字节写成产物文件（旧行为会写成一个打不开的假图片）

#### Scenario: 契约外容器保留并点名

- **WHEN** 上游返回的容器不在请求契约（`png`/`jpeg`/`webp`）之内（如 tiff/heic/avif）
- **THEN** 产物 SHALL 保留并按其真实容器命名，MUST NOT 因容器不合约而丢弃
  （上游已计费，丢弃等于让客户白付一次）
- **AND** 响应 `warnings` MUST 点名该容器契约外并提示发布前转换

#### Scenario: 扩展名跟实际容器走

- **WHEN** 上游返回的容器与调用者请求的 `output_format` 不一致（如请求 jpeg、实得 PNG）
- **THEN** 产物文件按实际容器命名（`.png`），MUST NOT 命名为 `.jpeg`
- **AND** 响应的 `warnings` 中如实披露该差异

### Requirement: 视频车道异步任务与轮询

视频生成 SHALL 以词元异步任务协议调用：创建任务取回任务 ID，轮询同一路径至终态，
终态取回视频 URL 并下载落盘；失败终态与上游不可路由 MUST 如实报错。

图生视频 SHALL 接受本地图像资产或文件路径作为首帧，并由生成层转换为上游可接受的形式
（上传换取 URL，或等价内联形式）。首帧来源 MUST 在提交前校验可读；
缺失、不可读或格式无法识别时 MUST 在提交前失败，MUST NOT 提交一个没有首帧的付费任务。

首帧是否真的影响产物 SHALL 可判定：同一提示词在有首帧与无首帧两种条件下各生成一次，
产物首帧画面 MUST 不同；若相同则该车道不支持图生视频，MUST 在车道台账如实标注，
MUST NOT 以「车道 active」掩盖该差异。

#### Scenario: 视频任务创建并轮询至成功

- **WHEN** 调用者请求文生视频且凭证为 ciyuan 档
- **THEN** 系统 `POST /v1/contents/generations/tasks` 创建任务并取回任务 ID
- **AND** 轮询 `GET /v1/contents/generations/tasks/<id>` 直至终态
- **AND** 终态为成功时下载 `content.video_url` 的字节写入产物路径
- **AND** 响应返回产物相对路径，形状与既有视频响应一致

#### Scenario: 视频模型不可路由

- **WHEN** 词元对请求的视频模型返回 404（无对应上游端点）或 503（上游不可达）
- **THEN** 该次生成失败并如实上报状态码与错误信息
- **AND** MUST NOT 重试其他模型来掩盖模型不可路由的事实

#### Scenario: 默认视频模型可服务

- **WHEN** 使用 ciyuan 档凭证且调用者未指定模型
- **THEN** 系统使用默认模型 `doubao-seedance-2-0-fast-260128`

#### Scenario: 异步两阶段与重复计费防护

- **WHEN** 托管模式以 submit/poll 两阶段调用视频生成
- **THEN** 提交阶段只创建任务并返回任务 ID，轮询阶段负责落盘
- **AND** 客户端超时后重试的是状态查询，MUST NOT 重复提交已付费的创建请求

#### Scenario: 本地图作首帧

- **WHEN** 调用者以一张本地产出的图像资产作为首帧提交图生视频
- **THEN** 系统把该图转换为上游可接受的形式并在提交前确认文件可读
- **AND** 产物落盘并通过容器魔数校验

#### Scenario: 首帧缺失即拒绝

- **WHEN** 指定的首帧文件不存在或无法识别
- **THEN** 系统在提交任务前失败并指明是哪一项
- **AND** 不产生任何付费任务

#### Scenario: 首帧不生效被判出

- **WHEN** 同一提示词在有首帧与无首帧两种条件下生成，产物首帧画面完全相同
- **THEN** 该车道的图生视频能力在台账中标为不支持
- **AND** 发现面不得把图生视频呈现为可用能力

### Requirement: 词元分组口径与分路凭证

系统 SHALL 承认词元「一把密钥只绑一个分组、图像/视频与语音分属不同分组」的约束，
并按生成类型（kind）分路解析凭证；MUST NOT 假设单一 key 可服务全部车道。

同一 kind 的 provider、端点、密钥与模型 SHALL 全部由凭证记录
（`vaas/pool/<line>/<kind>` 或 `vaas/service/default/<kind>`）声明；切换上游
MUST 只改该记录，MUST NOT 需要改动生成代码或重出镜像。镜像内与车道状态相关的唯一
产物是 `lane_health.json`，其变更随镜像生效。

provider 档 SHALL 为显式枚举（`volcengine`/`ciyuan`/`grok`）；未受支持的 provider
MUST 显式拒绝并列出受支持档位，MUST NOT 静默回落。**车道身份跟随凭据分组**：
台账条目按凭据所属分组记账（如 `image.grok`/`video.grok`），MUST NOT 把一条分组
的销账顺手写到另一条分组的条目上（一条分组的封禁与另一条分组的可用互相独立，
混写会互相遮蔽）。

#### Scenario: 语音与图像使用各自分组的凭证

- **WHEN** 同一次部署内分别发起语音合成与图像生成
- **THEN** 两者可分别解析到不同分组的词元 key
- **AND** 各自调用成功，不因分组差异而互相失败

#### Scenario: 缺失某路凭证时仅该路失败

- **WHEN** 某一 kind（如 image）未配置 ciyuan 凭证而其他 kind 已配置
- **THEN** 该 kind 的生成调用失败并指出缺失的凭证路径
- **AND** 其他 kind 的生成不受影响

#### Scenario: 备用上游经凭证记录切换

- **WHEN** 主用上游账号被封禁而同一 kind 存在另一条可用上游（如另一分组的
  `grok-imagine-image`）
- **THEN** 只需改写该 kind 的凭证记录（provider/base_url/api_key/model）即可切到备用上游
- **AND** 该切换 MUST NOT 要求改动生成代码、MUST NOT 要求重出镜像
- **AND** `lane_health.json` 的状态更新 MUST 以切换后的实调端到端证据为准

#### Scenario: grok 图像档与 ciyuan 档共用驱动、分记账本

- **WHEN** 凭证记录声明 `provider: grok` 发起图像生成
- **THEN** 系统经同一图像驱动请求 `/v1/images/generations`（组 19 端点形状与组 9 一致），
  仅凭据与模型名不同（缺省 `grok-imagine-image`，`GROK_API_KEY`/`GROK_BASE_URL`
  缺省回落 `CIYUAN_*`——同一网关）
- **AND** 台账销账记在 grok 分组的条目上；组 9 的缺陷台账 MUST NOT 因组 19 可用而销账

#### Scenario: 未受支持的 provider 显式拒绝

- **WHEN** 调用者指定的 image/video provider 不在受支持档位内
- **THEN** 调用失败并列出受支持的 provider 档位
- **AND** MUST NOT 回落到任何其他 provider 静默代做

### Requirement: 移除第三方 LLM 桥接依赖

生成后端 MUST NOT 依赖通用 LLM 桥接库（litellm）：多 Provider 能力由 volcengine 直连
与 ciyuan 车道两条自有路径提供，词元本身即是网关。

#### Scenario: 生成代码无桥接库依赖

- **WHEN** 检查生成链路的依赖声明
- **THEN** 其中不含 litellm 及其传递依赖要求
- **AND** 词元车道调用仅使用标准库 HTTP 能力

#### Scenario: 第三方 provider 值被明确拒绝

- **WHEN** 凭证或环境声明了一个既非 volcengine 也非 ciyuan 的 provider 值
- **THEN** 系统拒绝该次生成并提示仅支持 `volcengine` 与 `ciyuan`
- **AND** MUST NOT 静默回落到任一可用后端

### Requirement: 词元侧车道缺陷的记录与规避

对已实测确认的词元侧车道缺陷（应答形状不合规、模型不可路由、上游账号封禁），系统 SHALL 以默认模型白名单与显式错误规避之，并在文档中如实标注；MUST NOT 假装这些车道正常可用。缺陷台账 SHALL 同时机器可读：每条车道维护状态（`active`/`degraded`/`down`）、原因类别与最后验证时间，可经发现工具查询，并与部署侧台账一致；状态为 down 的车道 MUST NOT 在任何发现面被标为可用，对其调用 SHALL 返回 `lane_down` 分类错误并附状态与可行动提示。缺陷销账 MUST 以上游修复后的实调端到端证据为准，MUST NOT 仅凭状态翻转收口。

台账条目 SHALL 自带陈旧度：按条目声明的复验周期（缺省 7 天，可用 `stale_after_days` 覆盖）判定
`stale`，并在发现面随状态一并暴露 `stale` 与 `age_days`。**`stale` MUST NOT 被当作新鲜**——
陈旧的 `down` 会继续拒绝一条可能已恢复的车道，陈旧的 `active` 会推荐一条可能已死的车道；
二者都是错，且方向相反。时间戳缺失或不可解析时 `stale` SHALL 为「判不出」（`null`），
MUST NOT 记为新鲜。

#### Scenario: 已知缺陷模型不作为默认

- **WHEN** 配置或文档声明 ciyuan 档的默认模型
- **THEN** 默认模型取自实测可服务的白名单
- **AND** 已知返回 HTML 或不可路由的模型被明确标注为不可用

#### Scenario: 语音车道上游未配置时如实报错

- **WHEN** 调用 ASR 转写且词元语音车道无可用的上游账号
- **THEN** 系统如实上报车道不可用（含车道与引擎标识），不伪装成输入错误

#### Scenario: 上游封禁时车道状态如实可查

- **WHEN** 图像车道因上游账号被封而对请求返回 503
- **THEN** 发现工具中该车道状态为 down（含原因类别与最后验证时间）
- **AND** 租户调用返回 lane_down 分类错误而非上游原始错误体，且提示不重试等待修复

#### Scenario: 修复以实调为凭

- **WHEN** 上游账号封禁解除后运行车道探针
- **THEN** 实调成功（产物落盘、形状合规）方可将状态记为 active
- **AND** 探针证据与时间戳写入台账

#### Scenario: 台账陈旧在发现面可见

- **WHEN** 某车道的 `last_verified` 距今超过其复验周期（缺省 7 天）
- **THEN** 发现工具对该车道的 `lane` 视图带 `stale: true` 与 `age_days`
- **AND** 该标注对 `down` 与 `active` 一视同仁（两种陈旧都需要人复验）
- **AND** 时间戳不可解析时标注为「判不出」（`null`），MUST NOT 记为 `false`

### Requirement: TTS 输出确定性落盘

TTS 合成产物 SHALL 支持调用方指定输出目录直写；未指定时才回退到既有桌面时间戳目录约定。

#### Scenario: 指定输出目录

- **WHEN** 调用方（如 task-render 的 tts 步骤）向 TTS 包装层传入输出目录
- **THEN** 音频与 captions 文件 SHALL 直写该目录，落盘路径在调用结果中如实返回
- **AND** 调用方 MUST NOT 依赖「扫描桌面最新时间戳目录」来定位本次产物

#### Scenario: 未指定输出目录的兼容行为

- **WHEN** 调用方未传输出目录
- **THEN** 行为与现状一致（三级 fallback 目录 + 时间戳子目录）

### Requirement: TTS 错误按真实原因分类

TTS 合成失败 SHALL 向上暴露 provider 返回的真实错误类别与可行动提示。

#### Scenario: Ark 订阅失效可行动报错

- **WHEN** 火山方舟分块响应中返回 JSON 错误块（如 code=InvalidSubscription）
- **THEN** 错误信息 SHALL 包含该 code 与原文要点，并附可行动提示
  （续订方舟，或切换 `TTS_PROVIDER=ciyuan`）
- **AND** MUST NOT 将其报为音频解码失败（DECODE_FAILED）

#### Scenario: 词元车道错误保持车道标识

- **WHEN** 词元 audio 车道返回非音频响应或车道错误
- **THEN** 错误信息保持车道标识与上游原文要点（既有语义不变）

### Requirement: 词元语音模型与音色白名单

词元语音车道的默认 model 与 voice MUST 为实测可服务的组合，白名单随实调更新。

#### Scenario: 腾讯 TTS 默认组合

- **WHEN** 凭证为 ciyuan 档且未显式指定 TTS model/voice
- **THEN** 默认组合为 `tencent-tts` + 音色 `101001`（2026-11-06 实调可用）
- **AND** qwen3-tts-flash（voice Cherry）保留为白名单备选
- **AND** audio 车道按 protocol 路由、模型名不进 `/v1/models` 目录的实测事实
  记录于白名单文档，防止以目录探测代替实调

### Requirement: grok 视频车道（/v1/videos 协议族）

`provider=grok` 的视频生成 SHALL 走独立的 `/v1/videos` 协议族驱动
（`POST /v1/videos` → `GET /v1/videos/<id>` → `GET /v1/videos/<id>/content`），
MUST NOT 复用 ciyuan 档的 `/v1/contents/generations/tasks` 端点——两者形状不同。
驱动 SHALL 提供 `submit/poll/status_once/download` 四函数接口，与 ciyuan 视频驱动
同名同签名，使宿主两阶段防重复计费语义（`--async` 只提交不等待、`--status` 只查态
不提交）原样保留。

驱动 SHALL 编入三个实测上游事实：① 终态按上游真实词表判（`completed` 映射为统一
`succeeded`，兼容 `succeeded`/`done`）；② 取件地址可为相对路径（`/v1/videos/<id>/content`），
下载 SHALL 拼 base_url 并携带 Authorization，绝对签名 URL 免鉴权直取；③ 下载字节
MUST 通过 MP4 容器魔数（`ftyp`）校验，不符 SHALL 失败且 MUST NOT 写盘。

时长 SHALL 夹紧到上游硬边界 [3,15] 秒：越界值夹到边界并在响应 `warnings` 中点名
原值与生效值，MUST NOT 静默改写不披露，MUST NOT 因越界直接拒绝。上游未按请求交付的
参数（如要 480p 实得 720P）SHALL 在 `warnings` 中如实披露。

#### Scenario: grok 视频两阶段提交与查询

- **WHEN** 调用者以 `--async` 发起 grok 视频生成
- **THEN** 系统仅 `POST /v1/videos` 并立即返回任务 id 与上游状态，不内部轮询
- **AND** 后续以 `--status <id>` 查询时仅读状态，MUST NOT 重新提交付费任务

#### Scenario: grok 视频完成取件

- **WHEN** 任务上游状态到达终态 `completed` 且返回取件地址
- **THEN** 驱动把状态归一为 `succeeded` 并解析扁平 `url`（兼容嵌套 `video.url`）
- **AND** 相对路径取件 SHALL 拼 base_url 并带 Authorization；绝对 URL 直取
- **AND** 下载体非 MP4 容器 SHALL 失败且不写盘

#### Scenario: 越界时长夹紧披露

- **WHEN** 调用者请求时长 1 秒（上游硬边界为 [3,15] 秒）
- **THEN** 驱动以 3 秒提交任务
- **AND** 响应 `warnings` 点名「请求 1s → 实发 3s」，MUST NOT 静默改写

#### Scenario: grok 视频车道未实调前不进台账

- **WHEN** grok 视频车道尚未取得端到端实调证据（提交→轮询→取件真产物）
- **THEN** 台账 MUST NOT 预先记录该车道为 active
- **AND** 未记录的车道按 degraded 放行（车道闸不拦未知车道），但这不构成销账证据
