# -*- coding: utf-8 -*-
"""对比 qwen3:30b-a3b 与 qwen3:32b 生成效果与速度"""
import json, time, urllib.request

URL = "http://127.0.0.1:11434/v1/chat/completions"

PROMPT = """你是一位短剧编剧。根据以下要求写一个5个分镜的短视频分镜脚本：
题材：都市悬疑，主角深夜回家发现家里门开着。
要求：每个分镜包含：镜头号、景别、画面描述（50字内）、台词（如有）。
直接输出分镜，不要多余解释。"""

def ask(model):
    body = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "stream": False,
        "temperature": 0.7,
    }
    req = urllib.request.Request(URL, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    dt = time.time() - t0
    content = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    return dt, content, usage

for model in ["qwen3:30b-a3b", "qwen3:32b"]:
    print("=" * 60)
    print(f"模型: {model}")
    try:
        dt, content, usage = ask(model)
        print(f"耗时: {dt:.1f}s | 输入token: {usage.get('prompt_tokens')} | 输出token: {usage.get('completion_tokens')} | 速度: {usage.get('completion_tokens',0)/dt:.1f} tok/s")
        print("-" * 40)
        print(content[:600])
    except Exception as e:
        print(f"调用失败: {e}")
    print()
