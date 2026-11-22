#!/usr/bin/env python3
"""视频车道探针：首帧到底生不生效？

为什么需要它（design D2）：上游不会说「我用了你的首帧」。`_grok_video.py` 的
`payload.setdefault("image", ...)` 只能证明我们**发了**，不能证明上游**用了**。
这跟图片编辑那边「200 返回原图」是同一类问题——判据只能是「看产物差异」。

做法：同一提示词跑两次（带首帧 / 不带首帧），比较两次产物的**首帧**与源图的距离。
判据不是绝对阈值，而是相对关系：
  带首帧产物的首帧，应当离源图**更近**，而离「无首帧产物首帧」**更远**。
绝对阈值会被「上游大量重绘但确实用了首帧」误判。

三种结论（沿用既有探针口径）：
  restored          —— 首帧确实生效
  still-blocked     —— 首帧被忽略（first_frame_ignored）/ 载荷被拒（payload_rejected）/ 5xx
  cannot-judge      —— 凭据/网络/参数问题（**不是**「上游不行」）

退出码：0 restored / 1 still-blocked / 2 cannot-judge

用法：
  CIYUAN_API_KEY=... python3 scripts/probe_video_lane.py \
      --base https://router.finddatatech.cloud --model grok-imagine-video --json
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _ciyuan_image import _host_only  # noqa: E402

RESTORED = 0
STILL_BLOCKED = 1
CANNOT_JUDGE = 2

DEFAULT_PROMPT = "a static shot of a large solid red disc centred on a plain white background"


def _solid_png(width: int = 512, height: int = 512, rgb: tuple = (0, 0, 255)) -> bytes:
    """纯色 PNG（stdlib 生成，不依赖 PIL）。

    源图刻意选**高饱和纯蓝**：如果首帧真生效，产物首帧应当明显偏蓝；
    如果被忽略，产物会按纯文本提示词生成（白底红盘），两者差异极大。
    """
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (len(data).to_bytes(4, "big") + tag + data
                + zlib.crc32(tag + data).to_bytes(4, "big"))

    ihdr = width.to_bytes(4, "big") + height.to_bytes(4, "big") + bytes([8, 2, 0, 0, 0])
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _first_frame_png(video: Path, out: Path) -> Path | None:
    """抽第一帧。需要 ffmpeg；没有就返回 None（判据降级为 cannot-judge）。"""
    ff = shutil.which("ffmpeg")
    if not ff:
        return None
    r = subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(video),
                        "-frames:v", "1", str(out)], capture_output=True)
    return out if r.returncode == 0 and out.is_file() else None


def _mean_rgb(img: Path) -> tuple[float, float, float] | None:
    """整图平均 RGB。用 ffmpeg 缩到 1x1 取像素——比引入 PIL 便宜。"""
    ff = shutil.which("ffmpeg")
    if not ff:
        return None
    r = subprocess.run(
        [ff, "-loglevel", "error", "-i", str(img),
         "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True,
    )
    if r.returncode != 0 or len(r.stdout) < 3:
        return None
    return (float(r.stdout[0]), float(r.stdout[1]), float(r.stdout[2]))


def _dist(a, b) -> float | None:
    if a is None or b is None:
        return None
    return round(sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5, 2)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Probe whether the video lane honours first_frame")
    ap.add_argument("--base", required=True)
    ap.add_argument("--model", default="grok-imagine-video")
    ap.add_argument("--api-key-env", default="CIYUAN_API_KEY")
    ap.add_argument("--prompt", default=DEFAULT_PROMPT)
    ap.add_argument("--duration", type=int, default=3)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    key = os.environ.get(args.api_key_env, "").strip()
    if not key:
        out = {"verdict": "cannot-judge", "reason": f"{args.api_key_env} is empty"}
        print(json.dumps(out) if args.json else out["reason"])
        return CANNOT_JUDGE
    if not shutil.which("ffmpeg"):
        out = {"verdict": "cannot-judge",
               "reason": "ffmpeg missing — cannot compare first frames, so this probe "
                         "cannot judge whether the lane honoured the image"}
        print(json.dumps(out, ensure_ascii=False) if args.json else out["reason"])
        return CANNOT_JUDGE

    tmp = Path(tempfile.mkdtemp(prefix="fd-video-probe-"))
    src = tmp / "source.png"
    src.write_bytes(_solid_png())

    # 延迟导入：探针必须跑**真实驱动**，不是平行实现
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _grok_video as drv

    def run(tag: str, first_frame: str | None) -> Path | None:
        dest = tmp / f"{tag}.mp4"
        try:
            res = drv.generate_and_wait(
                prompt=args.prompt, model=args.model, duration=args.duration,
                ratio="16:9", resolution="720p",
                first_frame=first_frame, last_frame=None,
                api_key=key, base_url=args.base, output_path=dest,
                timeout_seconds=600,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[{tag}] failed: {exc}", file=sys.stderr)
            return None
        return dest if dest.is_file() else None

    # 形态 A：data URI（本地图唯一不依赖上传通道的形态）
    import base64
    data_uri = "data:image/png;base64," + base64.b64encode(src.read_bytes()).decode()

    with_img = run("with", data_uri)
    without = run("without", None)

    if with_img is None or without is None:
        out = {"verdict": "still-blocked", "verdict_class": "generation_failed",
               "reason": "at least one of the two probe runs produced no video",
               "with_frame": bool(with_img), "without_frame": bool(without)}
        print(json.dumps(out, ensure_ascii=False) if args.json else out["reason"])
        return STILL_BLOCKED

    wf = _first_frame_png(with_img, tmp / "with_f.png")
    nf = _first_frame_png(without, tmp / "without_f.png")
    if wf is None or nf is None:
        out = {"verdict": "cannot-judge", "verdict_class": "frame_extract_failed",
               "reason": "could not extract a first frame from a produced video"}
        print(json.dumps(out, ensure_ascii=False) if args.json else out["reason"])
        return CANNOT_JUDGE

    src_rgb, with_rgb, without_rgb = _mean_rgb(src), _mean_rgb(wf), _mean_rgb(nf)
    d_with_src = _dist(with_rgb, src_rgb)      # 带首帧产物 ↔ 源图
    d_without_src = _dist(without_rgb, src_rgb)  # 无首帧产物 ↔ 源图
    d_with_without = _dist(with_rgb, without_rgb)

    honoured = (
        d_with_src is not None and d_without_src is not None
        and d_with_src < d_without_src          # 带首帧的更接近源图
        and d_with_without is not None and d_with_without > 0
    )

    out = {
        "verdict": "restored" if honoured else "still-blocked",
        "verdict_class": "ok" if honoured else "first_frame_ignored",
        "reason": ("the first frame demonstrably affected the product"
                   if honoured else
                   "the two products' first frames are indistinguishable relative to the "
                   "source — the lane appears to ignore first_frame"),
        "model": args.model,
        "host": _host_only(args.base),
        "form_probed": "data_uri",
        "distances": {
            "with_vs_source": d_with_src,
            "without_vs_source": d_without_src,
            "with_vs_without": d_with_without,
        },
        "rbg": {"source": src_rgb, "with": with_rgb, "without": without_rgb},
    }
    print(json.dumps(out, ensure_ascii=False, indent=1) if args.json else
          f"{out['verdict']} [{out['verdict_class']}]: {out['reason']} {out['distances']}")
    return RESTORED if honoured else STILL_BLOCKED


if __name__ == "__main__":
    raise SystemExit(main())