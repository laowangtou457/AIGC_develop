# -*- coding: utf-8 -*-
"""
MiniMax H3 提交工具：把导出包中的逐镜 payload 提交到 H3 API 并轮询结果。

用法:
    python tools/submit_minimax_h3.py <payload文件或目录>
        [--key <API Key>]            # 缺省读环境变量 MINIMAX_API_KEY
        [--base-url https://api.minimax.cn]
        [--poll]                     # 提交后轮询任务状态（默认只提交，打印 task_id）
        [--poll-interval 10]

说明:
- payload 中本地图片（相对路径）会自动转为 base64 data URL（请求体 ≤64MB，图片 ≤30MB）。
- 结果写入 <payload目录>/submit_results.json：{task_id: {status, video_url, error}}。
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.common import load_config  # noqa: E402


def _resolve_image_url(url: str, root: Path) -> str:
    """本地相对路径 → base64 data URL；http(s) 或 data: 原样返回。"""
    if url.startswith(("http://", "https://", "data:")):
        return url
    p = root / url
    if not p.exists():
        raise FileNotFoundError(f"图片不存在: {p}")
    ext = p.suffix.lower().lstrip(".") or "jpeg"
    if ext == "jpg":
        ext = "jpeg"
    b64 = base64.b64encode(p.read_bytes()).decode()
    return f"data:image/{ext};base64,{b64}"


def load_payloads(path: str | Path, root: Path) -> list[dict]:
    p = Path(path)
    files = sorted(p.glob("shot_*.json")) if p.is_dir() else [p]
    out = []
    for f in files:
        payload = json.loads(f.read_text(encoding="utf-8"))
        meta = payload.pop("_meta", {})
        payload["_meta"] = meta
        for item in payload.get("content", []):
            if item.get("type") == "image_url":
                item["image_url"]["url"] = _resolve_image_url(item["image_url"]["url"], root)
        out.append(payload)
    return out


def submit_one(payload: dict, base_url: str, api_key: str, timeout: int = 60) -> str:
    url = base_url.rstrip("/") + "/v2/video_generation"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {k: v for k, v in payload.items() if k != "_meta"}
    r = requests.post(url, headers=headers, json=body, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    return r.json()["task_id"]


def query_task(task_id: str, base_url: str, api_key: str, timeout: int = 60) -> dict:
    url = base_url.rstrip("/") + f"/v2/query/video_generation/{task_id}"
    headers = {"Authorization": f"Bearer {api_key}"}
    r = requests.get(url, headers=headers, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    return r.json().get("task", {})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MiniMax H3 提交工具")
    parser.add_argument("payload", help="payload JSON 文件或目录（含 shot_*.json）")
    parser.add_argument("--key", default=None, help="API Key；缺省读 MINIMAX_API_KEY")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--poll", action="store_true", help="提交后轮询直至成功/失败")
    parser.add_argument("--poll-interval", type=int, default=10)
    args = parser.parse_args(argv)

    cfg = load_config()
    h3cfg = cfg.get("h3", {})
    api_key = args.key or __import__("os").environ.get(h3cfg.get("api_key_env", "MINIMAX_API_KEY"), "")
    if not api_key:
        print("错误：未提供 API Key（--key 或环境变量 MINIMAX_API_KEY）")
        return 1
    base_url = (args.base_url or h3cfg.get("api_base_url", "https://api.minimax.cn")).rstrip("/")
    root = cfg["workdir"]
    payload_dir = Path(args.payload)

    try:
        payloads = load_payloads(args.payload, root)
    except FileNotFoundError as e:
        print(f"错误：{e}")
        return 1
    if not payloads:
        print(f"错误：{args.payload} 下没有 shot_*.json")
        return 1

    results: dict = {}
    print(f"提交 {len(payloads)} 个任务到 {base_url} ...")
    for i, p in enumerate(payloads, 1):
        shot_id = p.get("_meta", {}).get("shot_id")
        try:
            task_id = submit_one(p, base_url, api_key)
            print(f"[{i}/{len(payloads)}] shot {shot_id} -> task_id={task_id}")
            results[str(shot_id)] = {"task_id": task_id, "status": "submitted", "video_url": "", "error": ""}
        except Exception as e:  # noqa: BLE001
            print(f"[{i}/{len(payloads)}] shot {shot_id} 提交失败: {e}")
            results[str(shot_id)] = {"task_id": "", "status": "submit_failed", "video_url": "", "error": str(e)}

    if args.poll:
        print(f"轮询中（间隔 {args.poll_interval}s）...")
        pending = {k: v for k, v in results.items() if v["status"] in ("submitted",)}
        while pending:
            time.sleep(args.poll_interval)
            for shot_id in list(pending):
                task_id = results[shot_id]["task_id"]
                try:
                    t = query_task(task_id, base_url, api_key)
                    status = t.get("status", "unknown")
                    results[shot_id]["status"] = status
                    if status == "succeeded":
                        results[shot_id]["video_url"] = (t.get("content") or {}).get("url", "")
                        print(f"shot {shot_id}: 成功 -> {results[shot_id]['video_url']}")
                        del pending[shot_id]
                    elif status in ("failed", "cancelled"):
                        results[shot_id]["error"] = str(t.get("error", ""))
                        print(f"shot {shot_id}: {status} {results[shot_id]['error']}")
                        del pending[shot_id]
                    else:
                        print(f"shot {shot_id}: {status}")
                except Exception as e:  # noqa: BLE001
                    print(f"shot {shot_id} 查询失败: {e}")

    out_path = (payload_dir if payload_dir.is_dir() else payload_dir.parent) / "submit_results.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结果已写入 {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
