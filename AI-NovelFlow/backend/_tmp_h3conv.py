# -*- coding: utf-8 -*-
"""模拟 generate-video 的 H3 转换，看动作信息是否在转换中丢失"""
import sys, json, io, urllib.request
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, r'F:\Develop\NewAIProductionWorkflow\AI-NovelFlow\backend')
import asyncio

from app.api.martial_arts import _to_h3_video_prompt

with urllib.request.urlopen('http://127.0.0.1:8000/api/martial-arts/history', timeout=30) as r:
    hist = json.loads(r.read().decode('utf-8'))['data']
row = next((h for h in hist if (h.get('videoPrompt') or '').strip()), None)
vp = row['videoPrompt']
print('=== 原始中文视频提示词（前 800 字）===')
print(vp[:800])
print()

out = asyncio.run(_to_h3_video_prompt(vp))
print('=== H3 转换结果 ===')
print('长度:', len(out))
print(out)
