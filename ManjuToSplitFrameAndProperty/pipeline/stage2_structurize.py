# -*- coding: utf-8 -*-
"""
阶段2：内容结构化
- 抽取音轨为 16k 单声道 wav
- ASR（faster-whisper，可选，--asr-model off 关闭）→ 台词分段 + srt 字幕
- 视觉理解（VLM，可选，dashscope/openai 兼容接口）→ 每个镜头的画面描述
- 合并到 shots.json → storyboard.json

用法: python -m pipeline.stage2_structurize <视频> [--asr-model tiny|base|small|medium|off]
      [--vlm-provider dashscope|openai] [--vlm-model qwen-vl-max] [--config config.yaml]
"""
from __future__ import annotations

import argparse
import datetime
import os
import sys
from pathlib import Path

from .common import LOG, ensure_dir, find_ffmpeg, load_config, load_json, run, save_json, setup_logger

VISUAL_FIELDS = ("scene", "characters", "action", "costume", "props", "camera", "lighting", "music_note")


def extract_audio(video: str, wav_path: Path) -> bool:
    """抽取 16k 单声道 wav，供 ASR 使用。"""
    ensure_dir(wav_path.parent)
    ffmpeg = find_ffmpeg()
    r = run([ffmpeg, "-y", "-i", video, "-vn", "-ac", "1", "-ar", "16000", str(wav_path)])
    if r.returncode != 0:
        LOG.warning("音频抽取失败: %s", r.stderr[-400:])
        return False
    return wav_path.exists() and wav_path.stat().st_size > 0


def run_asr(wav_path: Path, model_size: str, language: str) -> list[dict]:
    """faster-whisper 转写，返回 [{start,end,text}]。"""
    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, info = model.transcribe(str(wav_path), language=language or None, vad_filter=True)
    out = []
    for s in segments:
        text = s.text.strip()
        if text:
            out.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": text})
    LOG.info("ASR: %d 条台词分段（语言=%s）", len(out), info.language)
    return out


def write_srt(segments: list[dict], path: Path) -> None:
    def ts(t: float) -> str:
        h, m = int(t // 3600), int(t % 3600 // 60)
        s = t % 60
        return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")

    lines = []
    for i, seg in enumerate(segments, 1):
        lines += [str(i), f"{ts(seg['start'])} --> {ts(seg['end'])}", seg["text"], ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def _overlap(a1: float, a2: float, b1: float, b2: float) -> float:
    return max(0.0, min(a2, b2) - max(a1, b1))


def map_dialogue_to_shots(segments: list[dict], shots: list[dict]) -> None:
    """把 ASR 分段按时间重叠映射到镜头。"""
    for seg in segments:
        best, best_ov = None, 0.0
        for s in shots:
            ov = _overlap(seg["start"], seg["end"], s["start"], s["end"])
            if ov > best_ov:
                best, best_ov = s, ov
        if best is not None and best_ov > 0:
            best.setdefault("dialogue", []).append(seg)


def run_vlm(cfg: dict, out_root: Path, shots: list[dict]) -> None:
    """对每个镜头关键帧调用 VLM 生成画面描述。无 API Key 或未配置 provider 时跳过。"""
    from . import vlm

    vcfg = cfg["structurize"]
    provider = vcfg.get("vlm_provider") or ""
    if not provider:
        LOG.info("未配置 vlm_provider，视觉理解跳过（可后续补跑）")
        return
    api_key = os.environ.get(vcfg.get("vlm_api_key_env") or "", "")
    base_url = (vcfg.get("vlm_base_url") or "").strip()
    if not api_key:
        if base_url:
            # 本地 OpenAI 兼容端点（如 Ollama）不需要真实 Key，用占位值放行
            api_key = "ollama"
            LOG.info("本地 VLM 端点 %s，使用占位 API Key", base_url)
        else:
            LOG.warning("环境变量 %s 未设置，视觉理解跳过", vcfg.get("vlm_api_key_env"))
            return

    items = []
    for s in shots:
        if s.get("keyframes"):
            items.append({
                "keyframe": str(out_root / s["keyframes"][0]),
                "n": s["id"] + 1,
                "start": s["start"],
                "end": s["end"],
            })
    if not items:
        return
    LOG.info("VLM 分析 %d 个镜头关键帧（模型 %s）", len(items), vcfg["vlm_model"])
    descs = vlm.describe_frames(items, provider, vcfg["vlm_model"], api_key, base_url=base_url or None)
    for s, d in zip(shots, descs):
        if not d:
            continue
        s["visual"] = {k: (d.get(k, "") if not isinstance(d.get(k), (list, tuple)) else list(d.get(k))) for k in VISUAL_FIELDS}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="阶段2 内容结构化")
    parser.add_argument("video", help="输入视频路径（须先运行阶段1）")
    parser.add_argument("--asr-model", default=None, help="faster-whisper 模型，off=关闭")
    parser.add_argument("--asr-language", default=None, help="语言代码，留空自动检测")
    parser.add_argument("--vlm-provider", default=None, help="dashscope/openai，空=按配置")
    parser.add_argument("--vlm-model", default=None)
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args(argv)
    setup_logger()

    cfg = load_config(args.config)
    scfg = cfg["structurize"]
    asr_model = args.asr_model if args.asr_model is not None else scfg["asr_model"]
    asr_language = args.asr_language if args.asr_language is not None else scfg["asr_language"]
    if args.vlm_provider is not None:
        scfg["vlm_provider"] = args.vlm_provider
    if args.vlm_model:
        scfg["vlm_model"] = args.vlm_model

    video = Path(args.video).resolve()
    if not video.exists():
        LOG.error("视频不存在: %s", video)
        return 1
    out_root = ensure_dir(cfg["workdir"] / video.stem)
    shots_path = out_root / "shots.json"
    if not shots_path.exists():
        LOG.error("缺少 %s，请先运行阶段1", shots_path)
        return 1

    shots = load_json(shots_path)["shots"]
    LOG.info("=== 阶段2 结构化: %s（%d 镜头）===", video, len(shots))

    # ASR
    wav_path = out_root / "audio" / "speech.wav"
    if asr_model and asr_model != "off" and extract_audio(str(video), wav_path):
        try:
            segments = run_asr(wav_path, asr_model, asr_language)
            map_dialogue_to_shots(segments, shots)
            write_srt(segments, out_root / "subtitles.srt")
            LOG.info("字幕已写出: %s", out_root / "subtitles.srt")
        except Exception as e:  # noqa: BLE001
            LOG.warning("ASR 失败（%s），跳过台词转录", e)
    else:
        LOG.info("ASR 已关闭或音频不可用，台词转录跳过")

    # VLM 视觉理解
    run_vlm(cfg, out_root, shots)

    warnings = []
    for s in shots:
        if not s.get("dialogue"):
            warnings.append(f"镜头 {s['id']} 无台词")
        if not s.get("visual"):
            warnings.append(f"镜头 {s['id']} 无画面描述")

    save_json({
        "video": str(video),
        "duration": load_json(out_root / "video_info.json").get("duration", 0.0),
        "asr_model": asr_model,
        "vlm_provider": scfg.get("vlm_provider") or "",
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "shots": shots,
        "warnings": warnings,
    }, out_root / "storyboard.json")

    LOG.info("阶段2完成: %d 镜头，%d 条提示，产物 %s", len(shots), len(warnings), out_root / "storyboard.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
