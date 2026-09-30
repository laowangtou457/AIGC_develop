# -*- coding: utf-8 -*-
"""
VLM 视觉理解客户端（OpenAI 兼容接口）
- provider=dashscope: 默认 base https://dashscope.aliyuncs.com/compatible-mode/v1
- provider=openai:    默认 base https://api.openai.com/v1（可传 base_url 覆盖）
API Key 由调用方从环境变量读取后传入。
"""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import requests

from .common import LOG

DEFAULT_BASES = {
    "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "openai": "https://api.openai.com/v1",
}

PROMPT = """你是视频分镜分析助手。这是视频第{n}个镜头（{start}-{end}秒）的关键帧。
请只输出一个 JSON 对象（不要多余文字、不要代码块）：
{{"scene":"场景与空间描述","characters":["人物及外貌特征"],"action":"本镜动作/事件",
"costume":"服装","props":["道具"],"camera":"景别、角度、运镜推断",
"lighting":"光线与色调","music_note":"音乐情绪推断(无则空字符串)"}}"""


def _extract_json(text: str) -> dict | None:
    text = (text or "").strip()
    text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:  # noqa: BLE001
                return None
    return None


def describe_frames(
    items: list[dict],
    provider: str,
    model: str,
    api_key: str,
    base_url: str | None = None,
    max_items: int = 1000,
) -> list[dict | None]:
    """items: [{"keyframe": 绝对路径, "n": 镜头号, "start": s, "end": e}] → [描述dict|None]"""
    base = base_url or DEFAULT_BASES.get(provider) or DEFAULT_BASES["openai"]
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    results: list[dict | None] = []
    for it in items[:max_items]:
        try:
            b64 = base64.b64encode(Path(it["keyframe"]).read_bytes()).decode()
            payload = {
                "model": model,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": PROMPT.format(n=it["n"], start=it["start"], end=it["end"])},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    ],
                }],
            }
            r = requests.post(f"{base}/chat/completions", headers=headers, json=payload, timeout=120)
            if r.status_code != 200:
                LOG.warning("VLM 调用失败 %s: %s", r.status_code, r.text[:200])
                results.append(None)
                continue
            content = r.json()["choices"][0]["message"]["content"]
            parsed = _extract_json(content)
            results.append(parsed or {"raw": content[:300]})
            LOG.info("镜头 %d 描述完成", it["n"])
        except Exception as e:  # noqa: BLE001
            LOG.warning("镜头 %d VLM 失败: %s", it["n"], e)
            results.append(None)
    return results
