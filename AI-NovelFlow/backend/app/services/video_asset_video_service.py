# -*- coding: utf-8 -*-
"""
视频资源替换 → 生成新视频 服务
==============================

链路（平台工作流三的最后一环）：
  替换资产后的逐镜 H3 提示词（minimax_h3/payloads/*.json）
  + 该镜关键帧（构图/动作起点）
  + 替换后的资产参考图（payload.image_url → assets/ 下新形象，替换后自动生效）
  → 逐镜调用 ComfyUI H3 视频工作流（video / first_last / three / four_frame）
  → 全部镜头串行生成 → ffmpeg 合并成片 → output/<stem>/new_video/

设计要点：
- 逐镜串行生成（ComfyUI 队列排队，避免显存并发争抢），每镜完成即时落库进度
- 单镜失败不中断整批（标记该镜 failed 继续下一镜），全部失败才判整体失败
- 参考图按数量自动匹配工作流：1 张→单图 video；2 张→首尾帧；3 张→三帧；≥4 张→四帧
- 每镜结束后调用 ComfyUI /free 释放模型显存（复用平台防卡死策略）
"""
import asyncio
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

from app.core.database import SessionLocal
from app.models.video_asset_history import VideoAssetHistory
from app.models.workflow import Workflow
from app.services.comfyui import ComfyUIService

# ── 常量 ──
NEW_VIDEO_DIR = "new_video"                     # 新视频产物子目录（相对 output_dir）
H3_MIN_SECONDS = 4                               # H3 单段最短时长（模型规格）
H3_MAX_SECONDS = 15                              # H3 单段最长时长（模型规格，超长镜头需分段/截断）
MAX_EXTRA_REF_IMAGES = 3                         # 附加参考图上限（主图 + 3 张 → 四帧工作流）

# 参考图数量 → 工作流类型
WORKFLOW_TYPE_BY_REF_COUNT = {
    1: "video",               # Minimax+H3+ref2va 加速工作流（单图）
    2: "first_last_video",    # Minimax H3 首尾帧生视频
    3: "three_frame_video",   # Minimax H3 三帧生视频
    4: "four_frame_video",    # Minimax H3 四帧生视频
}

# ═══════════════════════════ 工具函数 ═══════════════════════════

def _read_json(path: Path):
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _prompt_from_payload(payload: dict) -> str:
    """从逐镜 payload 提取六段式文本提示词"""
    if not payload:
        return ""
    for item in payload.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            return str(item.get("text") or "").strip()
    return ""


def _ref_images_from_payload(payload: dict) -> list:
    """从逐镜 payload 提取参考图相对路径（资产图，替换后自动生效）"""
    refs = []
    for item in payload.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "image_url":
            url = (item.get("image_url") or {}).get("url")
            if url:
                refs.append(str(url))
    return refs


_SHOT_HEAD_RE = re.compile(r"### Shot (\d+)（([\d.]+)-([\d.]+) 秒）")
_BLOCK_RE = re.compile(r"```\n(.*?)\n```", re.S)


def _prompts_md_fallback(output_dir: Path) -> dict:
    """prompts.md 兜底解析：{shot_index: prompt_text}（payload 缺失时使用）"""
    md_path = output_dir / "minimax_h3" / "prompts.md"
    if not md_path.exists():
        return {}
    try:
        text = md_path.read_text(encoding="utf-8")
    except Exception:
        return {}
    result = {}
    lines = text.splitlines()
    idx = 0
    while idx < len(lines):
        m = _SHOT_HEAD_RE.search(lines[idx])
        if m:
            shot_idx = int(m.group(1))
            block = ""
            j = idx + 1
            while j < len(lines) and not lines[j].startswith("### "):
                bm = _BLOCK_RE.search("\n".join(lines[j:j + 200])[:4000])
                if bm:
                    block = bm.group(1).strip()
                    break
                j += 1
            result[shot_idx] = block
        idx += 1
    return result


def _pick_workflow(ref_count: int) -> Workflow:
    """按参考图数量选激活的视频工作流（无激活时退化为任意一条）"""
    wf_type = WORKFLOW_TYPE_BY_REF_COUNT.get(max(1, min(ref_count, 4)))
    db = SessionLocal()
    try:
        wf = db.query(Workflow).filter(
            Workflow.type == wf_type, Workflow.is_active == True
        ).first()
        if not wf:
            wf = db.query(Workflow).filter(Workflow.type == wf_type).first()
        return wf
    finally:
        db.close()


def _effective_node_mapping(workflow: Workflow) -> dict:
    """节点映射适配：首尾帧工作流用 first_image/last_image，统一映射到 reference_image/keyframe_node_1"""
    mapping = {}
    try:
        mapping = json.loads(workflow.node_mapping or "{}")
    except Exception:
        mapping = {}
    if workflow.type == "first_last_video":
        mapping = dict(mapping)
        mapping["reference_image_node_id"] = mapping.get("first_image_node_id")
        mapping["keyframe_node_1"] = mapping.get("last_image_node_id")
    return mapping


def _clamp_duration(seconds) -> int:
    """H3 单段时长约束：4–15s"""
    try:
        d = int(float(seconds))
    except (TypeError, ValueError):
        d = 4
    return max(H3_MIN_SECONDS, min(d, H3_MAX_SECONDS))


# ═══════════════════════════ 生成计划 ═══════════════════════════

def build_shot_plans(output_dir: Path) -> list:
    """
    构建逐镜生成计划：
      [{index, prompt, main_image, extra_images, duration, ratio, ref_count}]

    参考图组合：
      - 主图 = 该镜关键帧首帧（保留原构图/动作起点）
      - 附加图 = 逐镜 payload 引用的资产图（角色/场景，替换后为新形象，最多 3 张）
      - payload 缺失时兜底：prompts.md 六段式 + 按镜头归属取场景/角色资产图
    """
    shots_data = _read_json(output_dir / "shots.json") or {}
    assets_data = _read_json(output_dir / "assets.json") or {}
    payload_dir = output_dir / "minimax_h3" / "payloads"
    md_fallback = _prompts_md_fallback(output_dir)

    # 镜头归属 → 资产图（兜底用）
    scene_by_shot = {}
    for sc in assets_data.get("scenes") or []:
        for sid in sc.get("shot_ids") or []:
            refs = sc.get("ref_images") or []
            if refs:
                scene_by_shot.setdefault(int(sid), refs[0])
    char_by_shot = {}
    for ch in assets_data.get("characters") or []:
        for sid in ch.get("source_shots") or []:
            refs = ch.get("ref_images") or []
            if refs:
                char_by_shot.setdefault(int(sid), refs[0])

    plans = []
    for shot in shots_data.get("shots") or []:
        sid = int(shot.get("id", 0))
        shot_idx = sid + 1
        payload = _read_json(payload_dir / f"shot_{shot_idx:03d}.json") or {}

        prompt = _prompt_from_payload(payload)
        if not prompt:
            prompt = md_fallback.get(shot_idx, "")

        # 主图：该镜关键帧首帧
        keyframes = shot.get("keyframes") or []
        main_image = keyframes[0] if keyframes else None

        # 附加参考图：优先 payload 资产图，缺失时按镜头归属兜底
        extras = _ref_images_from_payload(payload)
        if not extras:
            extras = []
            if sid in scene_by_shot:
                extras.append(scene_by_shot[sid])
            if sid in char_by_shot:
                extras.append(char_by_shot[sid])

        # 去重（排除与主图相同的路径）
        seen = {main_image} if main_image else set()
        extras_dedup = []
        for e in extras:
            if e and e not in seen:
                seen.add(e)
                extras_dedup.append(e)
        extras = extras_dedup[:MAX_EXTRA_REF_IMAGES]

        duration = _clamp_duration(payload.get("duration") or (shot.get("end", 0) - shot.get("start", 0)))
        ratio = payload.get("ratio") or "9:16"
        ref_count = 1 + len(extras)

        plans.append({
            "index": sid,
            "shot_idx": shot_idx,
            "prompt": prompt,
            "main_image": main_image,
            "extra_images": extras,
            "duration": duration,
            "ratio": ratio,
            "ref_count": ref_count,
        })
    return plans


# ═══════════════════════════ 状态落库 ═══════════════════════════

def _update_video_state(job_id: str, *, status=None, stage=None, error=None, summary=None):
    db = SessionLocal()
    try:
        job = db.query(VideoAssetHistory).filter(VideoAssetHistory.id == job_id).first()
        if not job:
            return
        if status is not None:
            job.video_status = status
        if stage is not None:
            job.video_stage = stage
        if error is not None:
            job.video_error = error
        if summary is not None:
            job.video_summary_json = json.dumps(summary, ensure_ascii=False)
        db.commit()
    finally:
        db.close()


# ═══════════════════════════ 视频下载 / 合并 ═══════════════════════════

async def _download_video(url: str, dest: Path):
    """下载 ComfyUI 生成的视频到产物目录"""
    import httpx
    async with httpx.AsyncClient(timeout=600) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.content)


async def _free_comfyui_models():
    """视频任务结束后释放 ComfyUI 模型内存（防 OOM 死锁）"""
    try:
        import httpx
        from app.core.config import get_settings
        settings = get_settings()
        host = getattr(settings, "COMFYUI_HOST", "http://127.0.0.1:8188")
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(f"{host}/free", json={"unload_models": True, "free_memory": True})
        print("[VideoAsset] ComfyUI 模型已释放 (POST /free)")
    except Exception as e:
        print(f"[VideoAsset] ComfyUI /free 调用失败: {e}")


def _merge_videos_ffmpeg(video_paths: list, output_path: Path) -> dict:
    """ffmpeg concat 合并成片；-c copy 失败时回退重编码"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    list_file = output_path.with_suffix(".concat.txt")
    try:
        with open(list_file, "w", encoding="utf-8") as f:
            for p in video_paths:
                escaped = str(p).replace("'", "'\\''")
                f.write(f"file '{escaped}'\n")
        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(output_path)]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            # 回退：重编码保证可播放
            cmd = [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(output_path),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            return {"success": False, "message": (proc.stderr or proc.stdout)[-500:]}
        return {"success": output_path.exists(), "output_path": str(output_path)}
    finally:
        try:
            list_file.unlink()
        except Exception:
            pass


# ═══════════════════════════ 主流程 ═══════════════════════════

async def run_generate_new_video(job_id: str, video_path: str, output_dir: str):
    """
    后台任务：替换资产后 → 逐镜生成新视频 → 合并成片

    - 逐镜串行：ComfyUI 队列排队，避免显存并发争抢
    - 单镜失败标记 failed 继续下一镜；全部失败才判整体失败
    - 产物：output/<stem>/new_video/shot_NNN.mp4 + final_video.mp4
    """
    output = Path(output_dir)
    new_video_dir = output / NEW_VIDEO_DIR
    comfyui = ComfyUIService()

    def fail(message: str):
        _update_video_state(job_id, status="failed", stage="视频生成失败", error=message)

    try:
        plans = build_shot_plans(output)
        if not plans:
            fail("没有可生成的镜头（缺少 shots.json 或逐镜提示词）")
            return

        summary = {
            "total": len(plans),
            "done": 0,
            "ratio": plans[0].get("ratio") or "9:16",
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "shots": [
                {
                    "index": p["index"],
                    "shot_idx": p["shot_idx"],
                    "status": "pending",
                    "video_path": None,
                    "error": None,
                    "duration": p["duration"],
                    "workflow": None,
                }
                for p in plans
            ],
            "merged": {"status": "pending", "video_path": None, "error": None},
        }
        _update_video_state(job_id, status="running", stage=f"准备生成 {len(plans)} 个镜头…", summary=summary)

        succeeded_paths = []
        for pos, plan in enumerate(plans, 1):
            if plan["ref_count"] > 4:
                plan["ref_count"] = 4
            workflow = _pick_workflow(plan["ref_count"])
            if not workflow:
                _mark_shot(summary, plan["index"], "failed", error="未配置匹配的视频生成工作流")
                _update_video_state(job_id, stage=f"镜头 {pos}/{len(plans)} 失败：缺少工作流", summary=summary)
                continue

            main_path = None
            if plan["main_image"]:
                candidate = (output / plan["main_image"]).resolve()
                if candidate.exists():
                    main_path = str(candidate)
            keyframe_paths = []
            for rel in plan["extra_images"]:
                candidate = (output / rel).resolve()
                if candidate.exists():
                    keyframe_paths.append(str(candidate))

            # 参考图不足时回退到主图兜底（避免空图提交）
            if not main_path and keyframe_paths:
                main_path = keyframe_paths.pop(0)
            if not main_path:
                _mark_shot(summary, plan["index"], "failed", error="该镜无参考图（关键帧/资产图缺失）")
                _update_video_state(job_id, stage=f"镜头 {pos}/{len(plans)} 失败：缺少参考图", summary=summary)
                continue

            _mark_shot(summary, plan["index"], "running")
            _update_video_state(
                job_id,
                stage=f"正在生成镜头 {pos}/{len(plans)}（{workflow.name}，{plan['duration']}s）…",
                summary=summary,
            )
            print(f"[VideoAsset] Shot {plan['shot_idx']} prompt: {plan['prompt'][:120]!r}")

            try:
                result = await comfyui.generate_shot_video_with_workflow(
                    prompt=plan["prompt"],
                    workflow_json=workflow.workflow_json,
                    node_mapping=_effective_node_mapping(workflow),
                    aspect_ratio=plan["ratio"],
                    character_reference_path=main_path,
                    duration_seconds=plan["duration"],
                    keyframe_paths=keyframe_paths,
                )
            except Exception as exc:
                result = {"success": False, "message": f"调用 ComfyUI 异常: {exc}"}

            if not result.get("success") or not result.get("video_url"):
                _mark_shot(summary, plan["index"], "failed", error=(result.get("message") or "生成失败")[:300])
                _update_video_state(job_id, stage=f"镜头 {pos}/{len(plans)} 失败，继续下一镜…", summary=summary)
                continue

            dest = new_video_dir / f"shot_{plan['shot_idx']:03d}.mp4"
            try:
                await _download_video(result["video_url"], dest)
            except Exception as exc:
                _mark_shot(summary, plan["index"], "failed", error=f"视频下载失败: {exc}")
                _update_video_state(job_id, stage=f"镜头 {pos}/{len(plans)} 下载失败…", summary=summary)
                continue

            _mark_shot(
                summary, plan["index"], "succeeded",
                video_path=f"{NEW_VIDEO_DIR}/shot_{plan['shot_idx']:03d}.mp4",
                workflow=workflow.name,
            )
            succeeded_paths.append(str(dest))
            _update_video_state(
                job_id,
                stage=f"镜头 {pos}/{len(plans)} 完成（{len(succeeded_paths)}/{len(plans)}）…",
                summary=summary,
            )

        # ── 合并成片 ──
        if not succeeded_paths:
            fail("所有镜头生成失败，未产出可合并视频")
            return

        summary["merged"]["status"] = "running"
        _update_video_state(job_id, stage=f"正在合并 {len(succeeded_paths)} 个镜头…", summary=summary)
        final_path = new_video_dir / "final_video.mp4"
        merge_result = await asyncio.get_event_loop().run_in_executor(
            None, _merge_videos_ffmpeg, succeeded_paths, final_path
        )

        if not merge_result.get("success"):
            summary["merged"]["status"] = "failed"
            summary["merged"]["error"] = merge_result.get("message", "合并失败")
            _update_video_state(job_id, stage="镜头生成完成，但合并失败", summary=summary)
            fail(f"合并失败: {merge_result.get('message', '未知错误')[:300]}")
            return

        summary["merged"]["status"] = "succeeded"
        summary["merged"]["video_path"] = f"{NEW_VIDEO_DIR}/final_video.mp4"
        summary["done"] = len(succeeded_paths)
        _update_video_state(
            job_id,
            status="success",
            stage=f"新视频生成完成：{len(succeeded_paths)}/{len(plans)} 镜头，合并成片",
            summary=summary,
        )
        print(f"[VideoAsset] 新视频生成完成: {final_path}")

    except Exception as exc:
        import traceback
        traceback.print_exc()
        fail(f"视频生成任务异常: {exc}")
    finally:
        await _free_comfyui_models()


def _mark_shot(summary: dict, index: int, status: str, video_path=None, error=None, workflow=None):
    """更新 summary 中某一镜的状态"""
    for s in summary.get("shots", []):
        if s.get("index") == int(index):
            s["status"] = status
            if video_path is not None:
                s["video_path"] = video_path
            if error is not None:
                s["error"] = error
            if workflow is not None:
                s["workflow"] = workflow
            done = sum(1 for x in summary.get("shots", []) if x.get("status") == "succeeded")
            summary["done"] = done
            break
