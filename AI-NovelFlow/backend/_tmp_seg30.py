# -*- coding: utf-8 -*-
"""分段拼接 30s 端到端验证：16 拍拆 8+8 → 两段各 15s → 段间首帧衔接 → 拼接"""
import sys, json, sqlite3, httpx, time
sys.stdout.reconfigure(encoding='utf-8')

db = r'F:\Develop\NewAIProductionWorkflow\AI-NovelFlow\backend\novelflow.db'
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row
cur = conn.cursor()
cur.execute("SELECT video_prompt, storyboard_image_url FROM martial_arts_history WHERE id=?", ('92879fc4-bdbf-4212-8eaf-7c2eb1b0304c',))
row = cur.fetchone()
conn.close()
if not row:
    print('history not found'); sys.exit(1)

payload = {
    "prompt": row['video_prompt'],
    "image_url": row['storyboard_image_url'],
    "mode": "ref2va",
    "duration_seconds": 30,
    "history_id": "92879fc4-bdbf-4212-8eaf-7c2eb1b0304c",
    "image_is_storyboard": True,
    "split_segments": True,
}

t0 = time.time()
with httpx.Client(timeout=7200) as client:
    r = client.post('http://127.0.0.1:8000/api/martial-arts/generate-video', json=payload)
    print('HTTP', r.status_code)
    print('耗时 %.1f 分钟' % ((time.time()-t0)/60))
    try:
        body = r.json()
        print(json.dumps(body, ensure_ascii=False)[:1500])
    except Exception:
        print(r.text[:1500])
