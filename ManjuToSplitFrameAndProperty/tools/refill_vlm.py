# -*- coding: utf-8 -*-
"""
补跑缺失镜头的 VLM 画面描述（应对瞬时 502），合并回 storyboard.json。
用法: .venv\\Scripts\\python tools\\refill_vlm.py <视频路径>
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import vlm  # noqa: E402
from pipeline.common import load_config, load_json, save_json, setup_logger  # noqa: E402

VISUAL_FIELDS = ["scene", "characters", "action", "costume", "props", "camera", "lighting", "music_note"]
RETRY = 3
WAIT = 15


def main() -> int:
    setup_logger()
    video = sys.argv[1] if len(sys.argv) > 1 else ""
    cfg = load_config("config.yaml")
    vcfg = cfg["structurize"]
    out_root = cfg["workdir"] / Path(video).stem
    sb_path = out_root / "storyboard.json"
    sb = load_json(sb_path)
    provider = vcfg.get("vlm_provider") or ""
    base_url = (vcfg.get("vlm_base_url") or "").strip() or None
    api_key = "ollama"  # 本地端点占位
    model = vcfg["vlm_model"]

    missing = [s for s in sb["shots"] if not s.get("visual") and s.get("keyframes")]
    if not missing:
        print("无缺失镜头，无需补跑")
        return 0
    print(f"待补跑 {len(missing)} 个镜头: {[s['id'] + 1 for s in missing]}")

    for s in missing:
        item = {"keyframe": str(out_root / s["keyframes"][0]), "n": s["id"] + 1, "start": s["start"], "end": s["end"]}
        for attempt in range(1, RETRY + 1):
            try:
                descs = vlm.describe_frames([item], provider, model, api_key, base_url=base_url, max_items=1)
                d = descs[0] if descs else None
                if d and isinstance(d, dict):
                    s["visual"] = {k: (list(d.get(k)) if isinstance(d.get(k), (list, tuple)) else d.get(k, "")) for k in VISUAL_FIELDS}
                    print(f"镜头 {s['id'] + 1} 补跑成功")
                    break
            except Exception as e:  # noqa: BLE001
                print(f"镜头 {s['id'] + 1} 异常: {e}")
            if attempt < RETRY:
                print(f"  第 {attempt} 次失败，{WAIT}s 后重试...")
                time.sleep(WAIT)
        else:
            print(f"镜头 {s['id'] + 1} 补跑失败（已重试 {RETRY} 次），visual 仍为空")

    save_json(sb, sb_path)
    done = sum(1 for s in sb["shots"] if s.get("visual"))
    print(f"完成：{done}/40 个镜头有 visual")
    return 0


if __name__ == "__main__":
    sys.exit(main())
