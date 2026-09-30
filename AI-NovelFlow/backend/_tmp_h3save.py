# -*- coding: utf-8 -*-
"""临时脚本：h3_prompt 入库 + 接口返回 + 前端展示"""
import io, sys
sys.stdout.reconfigure(encoding='utf-8')
n = 0
def rep(p, old, new, tag):
    global n
    with io.open(p, 'r', encoding='utf-8') as f:
        s = f.read()
    assert old in s, f'MISS {tag}'
    s = s.replace(old, new, 1)
    with io.open(p, 'w', encoding='utf-8') as f:
        f.write(s)
    n += 1
    print('OK', tag)

# 1) model 加字段
rep(r'F:\Develop\NewAIProductionWorkflow\AI-NovelFlow\backend\app\models\martial_arts_history.py',
    '''    video_prompt = Column(Text, nullable=True)          # 视频提示词
    raw = Column(Text, nullable=True)                   # LLM 完整原始输出''',
    '''    video_prompt = Column(Text, nullable=True)          # 视频提示词（中文）
    h3_prompt = Column(Text, nullable=True)             # 实际执行用 H3 英文提示词（逐分镜独立镜头块）
    raw = Column(Text, nullable=True)                   # LLM 完整原始输出''',
    'model col')

# 2) _history_to_dict 返回 h3Prompt
rep(r'F:\Develop\NewAIProductionWorkflow\AI-NovelFlow\backend\app\api\martial_arts.py',
    '''        "videoPrompt": h.video_prompt or "",
        "raw": h.raw or "",''',
    '''        "videoPrompt": h.video_prompt or "",
        "h3Prompt": h.h3_prompt or "",
        "raw": h.raw or "",''',
    'history dict')

# 3) generate-video 成功时回写 h3_prompt（转换后英文模板）
rep(r'F:\Develop\NewAIProductionWorkflow\AI-NovelFlow\backend\app\api\martial_arts.py',
    '''    _patch_history_url(db, data.history_id, video_url=video_url)
    _martial_finish(_tid)

    return {
        "success": True,
        "data": {"video_url": video_url, "mode": mode, "duration_seconds": data.duration_seconds},
        "message": "视频生成成功",
    }''',
    '''    _patch_history_url(db, data.history_id, video_url=video_url, h3_prompt=prompt_h3 if prompt_h3 != prompt else "")
    _martial_finish(_tid)

    return {
        "success": True,
        "data": {"video_url": video_url, "mode": mode, "duration_seconds": data.duration_seconds},
        "message": "视频生成成功",
    }''',
    'save h3 prompt')

print('total', n)
