# -*- coding: utf-8 -*-
"""
阶段1：视频解析
- 读取视频元信息（video_info.json）
- 按采样帧率抽帧（frames/）
- 场景切分/镜头边界检测：优先 scenedetect（帧级精确），失败回退 cv2 直方图差法
- 每个镜头提取关键帧（镜头中帧，keyframes/）
- 输出 shots.json

用法: python -m pipeline.stage1_parse <视频> [--fps 1] [--min-shot 0.5] [--scene-threshold 30] [--config config.yaml]
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

from .common import LOG, ensure_dir, find_ffmpeg, load_config, probe_video, run, save_json, setup_logger


def extract_sampled_frames(video: str, out_dir: Path, fps: float) -> None:
    """按 fps 抽样抽帧到 out_dir。"""
    ensure_dir(out_dir)
    for old in out_dir.glob("frame_*.jpg"):
        old.unlink()
    ffmpeg = find_ffmpeg()
    r = run([ffmpeg, "-y", "-i", video, "-vf", f"fps={fps}", "-q:v", "2", str(out_dir / "frame_%05d.jpg")])
    if r.returncode != 0:
        LOG.warning("抽样抽帧失败: %s", r.stderr[-500:])


def merge_short_shots(shots: list[tuple[float, float]], min_len: float) -> list[tuple[float, float]]:
    """把短于 min_len 的镜头并入相邻镜头。"""
    if not shots:
        return shots
    merged = []
    for s, e in shots:
        if merged and (e - s) < min_len:
            ps, pe = merged[-1]
            merged[-1] = (ps, e)  # 并入前一个镜头
        else:
            merged.append((s, e))
    return merged


def _tc_sec(tc) -> float:
    """跨版本兼容的 FrameTimecode → 秒。"""
    try:
        return float(tc.seconds)
    except Exception:  # noqa: BLE001
        return float(tc.get_seconds())


def detect_scenes(video: str, min_shot: float, threshold: float, duration: float) -> tuple[list[tuple[float, float]], str]:
    """返回 ([(start, end)...], method)。优先 scenedetect，失败回退 cv2；无切点时整段为一个镜头。"""
    try:
        from scenedetect import ContentDetector, detect

        scenes = detect(video, ContentDetector(threshold=threshold))
        out = [(round(_tc_sec(s[0]), 2), round(_tc_sec(s[1]), 2)) for s in scenes]
        if not out:
            out = [(0.0, round(duration, 2))]
        out = merge_short_shots(out, min_shot)
        LOG.info("scenedetect: 检出 %d 个镜头", len(out))
        return out, "scenedetect"
    except Exception as e:  # noqa: BLE001
        LOG.warning("scenedetect 不可用（%s），回退 cv2 直方图切分", e)
        out = detect_scenes_cv2(video, min_shot)
        return out, "cv2-fallback"


def detect_scenes_cv2(video: str, min_shot: float, sample_interval: float = 0.4, k: float = 2.0) -> list[tuple[float, float]]:
    """cv2 兜底切分：对采样帧计算 HSV 直方图 Bhattacharyya 距离，超过均值+k·标准差视为切点。"""
    import cv2

    cap = cv2.VideoCapture(video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    duration = total / fps if fps else 0.0
    diffs: list[tuple[float, float]] = []
    prev_hist = None
    next_t = 0.0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
        if t < next_t:
            continue
        next_t = t + sample_interval
        small = cv2.resize(frame, (64, 48))
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [16, 8], [0, 180, 0, 256])
        cv2.normalize(hist, hist)
        if prev_hist is not None:
            d = cv2.compareHist(prev_hist, hist, cv2.HISTCMP_BHATTACHARYYA)
            diffs.append((t, d))
        prev_hist = hist
    cap.release()

    if not diffs:
        return [(0.0, round(duration, 2))]
    vals = [d for _, d in diffs]
    mean = statistics.mean(vals)
    sd = statistics.stdev(vals) if len(vals) > 1 else 0.0
    thr = mean + k * sd
    cuts = [t for t, d in diffs if d > thr]
    bounds = [0.0] + cuts + [duration]
    shots = [(round(bounds[i], 2), round(bounds[i + 1], 2)) for i in range(len(bounds) - 1)]
    shots = merge_short_shots(shots, min_shot)
    LOG.info("cv2-fallback: 检出 %d 个镜头", len(shots))
    return shots


def extract_keyframes(video: str, shots: list[tuple[float, float]], out_dir: Path) -> list[str]:
    """每个镜头取中帧作为关键帧，返回相对文件名列表。"""
    ensure_dir(out_dir)
    ffmpeg = find_ffmpeg()
    names: list[str] = []
    for i, (s, e) in enumerate(shots):
        mid = (s + e) / 2
        name = f"shot_{i:03d}_{mid:06.2f}.jpg"
        r = run([ffmpeg, "-y", "-ss", f"{mid:.3f}", "-i", video, "-frames:v", "1", "-q:v", "2", str(out_dir / name)])
        if r.returncode != 0:
            LOG.warning("镜头 %d 关键帧提取失败: %s", i, r.stderr[-300:])
            continue
        names.append(name)
    return names


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="阶段1 视频解析")
    parser.add_argument("video", help="输入视频路径")
    parser.add_argument("--fps", type=float, default=None, help="抽样帧率")
    parser.add_argument("--min-shot", type=float, default=None, help="最短镜头秒数")
    parser.add_argument("--scene-threshold", type=float, default=None, help="场景检测阈值")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args(argv)
    setup_logger()

    cfg = load_config(args.config)
    parse_cfg = cfg["parse"]
    fps = args.fps if args.fps is not None else parse_cfg["sample_fps"]
    min_shot = args.min_shot if args.min_shot is not None else parse_cfg["min_shot_len"]
    threshold = args.scene_threshold if args.scene_threshold is not None else parse_cfg["scene_threshold"]

    video = Path(args.video).resolve()
    if not video.exists():
        LOG.error("视频不存在: %s", video)
        return 1

    out_root = ensure_dir(cfg["workdir"] / video.stem)
    LOG.info("=== 阶段1 解析: %s ===", video)

    info = probe_video(video)
    LOG.info("时长 %.1fs | %dx%d | %.2ffps | 音频:%s", info["duration"], info["width"], info["height"], info["fps"], info["has_audio"])
    save_json(info, out_root / "video_info.json")

    extract_sampled_frames(str(video), out_root / "frames", fps)
    shots, method = detect_scenes(str(video), min_shot, threshold, info["duration"])
    kf_names = extract_keyframes(str(video), shots, out_root / "keyframes")

    shots_out = []
    for i, (s, e) in enumerate(shots):
        shots_out.append({
            "id": i,
            "start": s,
            "end": e,
            "keyframes": [f"keyframes/{kf_names[i]}"] if i < len(kf_names) else [],
            "dialogue": [],
        })
    save_json({
        "video": str(video),
        "duration": info["duration"],
        "scene_method": method,
        "shots": shots_out,
    }, out_root / "shots.json")

    LOG.info("阶段1完成: %d 个镜头，产物在 %s", len(shots_out), out_root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
