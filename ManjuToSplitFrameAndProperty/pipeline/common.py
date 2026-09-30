# -*- coding: utf-8 -*-
"""
公共工具模块：路径、日志、JSON 读写、配置加载、ffmpeg 定位与视频探测。

ffmpeg 定位优先级：环境变量 FFMPEG_BIN > 系统 PATH > imageio-ffmpeg 自带二进制。
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

LOG = logging.getLogger("pipeline")


def setup_logger(level: int = logging.INFO) -> logging.Logger:
    if not LOG.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%H:%M:%S"))
        LOG.addHandler(handler)
    LOG.setLevel(level)
    return LOG


def ensure_dir(p: str | Path) -> Path:
    d = Path(p)
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_json(path: str | Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(data, path: str | Path, indent: int = 2) -> Path:
    p = Path(path)
    ensure_dir(p.parent)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)
    LOG.info("写出 %s", p)
    return p


def load_config(path: str = "config.yaml") -> dict:
    """加载工程根目录下的 config.yaml，并合并默认值。返回的 workdir 为绝对 Path。"""
    import yaml

    root = Path(__file__).resolve().parent.parent  # 项目根
    p = Path(path)
    if not p.is_absolute():
        p = root / p
    data: dict = {}
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

    defaults = {
        "workdir": "output",
        "parse": {"sample_fps": 1.0, "min_shot_len": 0.5, "scene_threshold": 30.0},
        "structurize": {
            "asr_model": "tiny",
            "asr_language": "zh",
            "vlm_provider": "",
            "vlm_model": "qwen-vl-max",
            "vlm_api_key_env": "DASHSCOPE_API_KEY",
            "vlm_base_url": "",
        },
        "assets": {"min_face_size": 60, "cluster_hamming_threshold": 13},
        "generate": {
            "platform": "seedance",
            "style": "电影质感，写实，暖色调，浅景深",
            "aspect_ratio": "9:16",
        },
    }
    for k, v in defaults.items():
        if not isinstance(v, dict):
            continue
        merged = dict(v)
        user_part = data.get(k) or {}
        if isinstance(user_part, dict):
            merged.update(user_part)
        data[k] = merged

    wd = data.get("workdir", "output")
    data["workdir"] = Path(wd) if Path(wd).is_absolute() else root / wd
    return data


def find_ffmpeg() -> str:
    """定位 ffmpeg 可执行文件路径。"""
    exe = os.environ.get("FFMPEG_BIN")
    if exe and Path(exe).exists():
        return exe
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def imread_unicode(path: str | Path):
    """cv2.imread 的 unicode 安全替代（修复 Windows 中文路径无法读取，返回 BGR ndarray 或 None）。"""
    import cv2
    import numpy as np

    p = Path(path)
    if not p.exists():
        return None
    try:
        data = np.fromfile(str(p), dtype=np.uint8)
        if data.size == 0:
            return None
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    except Exception:  # noqa: BLE001
        return None


def imwrite_unicode(path: str | Path, img, ext: str = ".jpg") -> bool:
    """cv2.imwrite 的 unicode 安全替代（修复 Windows 中文路径无法写入）。"""
    import cv2

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(ext, img)
    if not ok:
        return False
    buf.tofile(str(p))
    return True


def find_ffprobe() -> str:
    """定位 ffprobe（imageio-ffmpeg 不自带，找不到则返回空串，由调用方回退 cv2）。"""
    ffmpeg = find_ffmpeg()
    lower = ffmpeg.lower()
    if lower.endswith("ffmpeg.exe"):
        cand = ffmpeg[: -len("ffmpeg.exe")] + "ffprobe.exe"
    elif lower.endswith("ffmpeg"):
        cand = ffmpeg[: -len("ffmpeg")] + "ffprobe"
    else:
        cand = ""
    if cand and Path(cand).exists():
        return cand
    return shutil.which("ffprobe") or ""


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    LOG.info("执行: %s", " ".join(map(str, cmd)))
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    kw.setdefault("encoding", "utf-8")
    kw.setdefault("errors", "replace")
    return subprocess.run(cmd, **kw)


def probe_video(video: str | Path) -> dict:
    """返回视频元信息。优先 ffprobe，缺失时回退 cv2。"""
    video = str(video)
    ffprobe = find_ffprobe()
    if ffprobe:
        r = run([ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", video])
        if r.returncode == 0 and r.stdout.strip():
            info = json.loads(r.stdout)
            streams = info.get("streams", [])
            vstream = next((s for s in streams if s.get("codec_type") == "video"), None)
            astream = next((s for s in streams if s.get("codec_type") == "audio"), None)
            fmt = info.get("format", {})
            dur = float(fmt.get("duration") or 0)
            fps = 0.0
            if vstream and vstream.get("r_frame_rate"):
                num, _, den = vstream["r_frame_rate"].partition("/")
                try:
                    fps = float(num) / float(den) if float(den) else 0.0
                except (ValueError, ZeroDivisionError):
                    fps = 0.0
            return {
                "path": video,
                "duration": round(dur, 3),
                "width": int(vstream.get("width") or 0) if vstream else 0,
                "height": int(vstream.get("height") or 0) if vstream else 0,
                "fps": round(fps, 3),
                "has_audio": bool(astream),
                "streams": streams,
            }

    import cv2

    cap = cv2.VideoCapture(video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    cap.release()
    # ffprobe 缺失时，用 ffmpeg -i 的输出判断是否含音轨
    has_audio = False
    try:
        r = run([find_ffmpeg(), "-i", video], capture_output=True)
        has_audio = bool(re.search(r"Audio:", r.stderr or ""))
    except Exception:  # noqa: BLE001
        pass
    return {
        "path": video,
        "duration": round(frames / fps, 3) if fps else 0.0,
        "width": w,
        "height": h,
        "fps": round(fps, 3),
        "has_audio": has_audio,
        "streams": [],
    }
