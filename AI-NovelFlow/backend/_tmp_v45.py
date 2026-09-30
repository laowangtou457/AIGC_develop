# -*- coding: utf-8 -*-
"""45 秒 H3 视频生成测试"""
import json, sys, urllib.request
sys.stdout.reconfigure(encoding='utf-8')

with urllib.request.urlopen('http://127.0.0.1:8000/api/martial-arts/history', timeout=30) as r:
    hist = json.loads(r.read().decode('utf-8'))['data']
row = next((h for h in hist if h.get('storyboardImageUrl') and h.get('videoPrompt')), None)
if not row:
    print('无可用历史'); sys.exit(1)
print('历史:', row['id'][:8], '| 分镜图:', row['storyboardImageUrl'][:70])

body = {
    "prompt": row['videoPrompt'],
    "image_url": row['storyboardImageUrl'],
    "mode": "ref2va",
    "duration_seconds": 45,
    "history_id": row['id'],
    "image_is_storyboard": True,
}
req = urllib.request.Request('http://127.0.0.1:8000/api/martial-arts/generate-video',
                             data=json.dumps(body).encode('utf-8'),
                             headers={'Content-Type': 'application/json'})
try:
    with urllib.request.urlopen(req, timeout=3600) as r:
        res = json.loads(r.read().decode('utf-8'))
    print('success:', res.get('success'))
    print('message:', res.get('message'))
    d = res.get('data') or {}
    print('video_url:', d.get('video_url'))
    print('h3_prompt 长度:', len(d.get('h3_prompt') or ''))
except Exception as e:
    print('EXC:', repr(e))
