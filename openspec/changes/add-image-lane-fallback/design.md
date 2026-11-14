## Context

图像车道只有一条凭证路径（`vaas/pool/wanxing/image`），而它的上游唯一账号被封。
现场已有一条可用替代上游（组 19 `fed-workbuddy-media` / `grok-imagine-image`），
唯一挡路的是 `_ciyuan_image.py` 只认 `data[].b64_json`。

## Decisions

### D1 两种产物形态，`b64_json` 优先

`data[]` 单项按 `b64_json → url → 失败` 顺序解析。`b64_json` 仍是首选：它不引入
二次网络请求，也不引入「这个 URL 指向谁」的判断面。顺序固定，不做按大小/尺寸的智能选择。

### D2 URL 下载的安全包封

URL 形态是**新增的网络面**，因此下载路径必须自带约束，不能靠上游善意：

- scheme 只许 `http` / `https`（拒 `file:`/`data:`/其余）；
- 连接与读取超时（默认 60s，可用 `CIYUAN_IMAGE_URL_TIMEOUT_S` 覆盖）；
- 体积上限（默认 24 MiB，可用 `CIYUAN_IMAGE_MAX_URL_BYTES` 覆盖），边读边计，
  超限立即中断而不是读完再判；
- `Content-Type` 必须以 `image/` 开头 **且** 字节头命中图像魔数（JPEG/PNG/WEBP）——
  两条都要，因为上游既可能把 HTML 标成 image，也可能把 image 标成 octet-stream；
- 失败信息只带 URL 的主机名，不带查询串（签名下载 URL 的凭据常在 query 里）。

### D3 不在代码里做「自动切车道」

上游失败时**不**回落到另一条凭证。理由：车道切换会改变计费主体与产物归属口径，
静默切换会让「这条产出来自哪条车道」无法回溯，也与本仓既有纪律（缺陷如实暴露、
以实调销账）冲突。切换是凭证记录的运维动作，由 D4 支撑。

### D4 换上游 = 改凭证记录

`provider` / `base_url` / `api_key` / `model` 全在 vault 记录里（`credentials.resolve`），
所以切换上游不需要改代码、不需要重出镜像；镜像内唯一与车道状态有关的是
`lane_health.json`。这条写进规格，避免下次又有人以为要出片。

### D6 契约外容器：保留产物 + 点名披露

`IMAGE_FORMATS`（png/jpeg/webp）是**请求**契约。真实容器可能落在它外面（tiff/heic/avif）。
两条路都不好：丢弃 = 上游已经计费而客户拿不到东西（就是 11-06 那种「空计费」形态的翻版）；
谎报扩展名 = 下游按扩展名选上传类型，必错。

取第三条：产物**保留并如实命名**，同时在 `warnings` 里点名「契约外 + 请先转换」。
这样钱没白花、真相在场，代价是调用者要读 warnings——比静默错标或静默丢件都轻。

### D5 台账只认实调

`lane_health.json` 的 `down` 会阻断上游探测（车道闸先拒），`last_verified` 不会自己前进。
因此翻 `active` 必须有一次「产物落盘 + 形状合规」的实调证据；仅改状态视为未销账。

## Risks / Trade-offs

- URL 下载引入 SSRF 面（上游可让服务端去取任意地址）。当前上游是我们自己的词元网关
  与受信分组，风险有限；若将来接入第三方，需再加私网/回环地址拒绝（`ip.is_global`）。
  本 change 不做，先记录。
- 24 MiB 上限对 4K 图足够，但对视频帧导出类产物可能偏小；届时按产物类型分档。
