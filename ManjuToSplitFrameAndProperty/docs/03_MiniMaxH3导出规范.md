# 03 MiniMax H3 导出规范

> 本工程 H3 导出模块（`pipeline/h3_export.py`）的规格说明，含官方 API 约束、六段式结构、payload 样例与限制校验。
> 官方文档复核源：https://platform.minimaxi.com/docs/api-reference/video-generation-v2-create （2026-09 抓取）

---

## 1. 官方 API 约束（已核实）

| 项 | 约束 |
|---|---|
| 接口 | `POST /v2/video_generation`（异步），查询 `GET /v2/query/video_generation/{task_id}` |
| 模型 | `MiniMax-H3`：4~15 秒，768P / 2K，支持多模态 reference 与 i2v |
|  | `MiniMax-H3-Max`：5~15 秒，480P / 768P，**不支持 reference**（图生视频） |
| content 数组 | 多模态元素：`text` / `image_url` / `video_url` / `audio_url` |
| 图片 | JPG/JPEG/PNG/WEBP/HEIC/HEIF；单张 ≤30MB；宽高 ∈ [256, 5760]；宽高比 ∈ [0.4, 2.5] |
| reference_image | 每请求 ≤ 9 张 |
| 互斥 | 图生视频（first_frame）与多模态 reference **互斥** |
| 文生视频 | `ratio` 必填（非 adaptive）；图生视频 `ratio` 自动为 adaptive |
| ratio 可选值 | `adaptive / 21:9 / 16:9 / 4:3 / 1:1 / 3:4 / 9:16` |
| 限制 | 请求体 ≤64MB；提示词 ≤7000 字符；时长整数秒 |

## 2. 两种导出模式（config.yaml → h3.mode）

| 模式 | 用途 | content 组装 | 提示词结构 |
|---|---|---|---|
| `reference`（默认） | 资产替换再生成（换角色/场景/服装） | text(Ref2VA 六段式) + image_url[](role=reference_image) | 六段式（官方 ref-en.txt） |
| `i2v` | 让原镜头动起来（保真还原） | text(I2VA 三字段) + image_url(role=first_frame) | 三字段（官方 base-en.txt） |

## 3. 提示词结构（官方 MiniMax-H3 技能规范）

对齐 **MiniMax 官方 `skills/h3-prompt-writing`**（原文存档于 `docs/references/{base-en.txt, ref-en.txt, SKILL.md}`，
仓库：https://github.com/MiniMax-AI/MiniMax-H3/tree/main/skills/h3-prompt-writing ）：

### 3.1 reference 模式 → Ref2VA 六段式（ref-en.txt）

```
subject_definitions:
<Subject 1> is the character 角色0（角色描述）, whose appearance comes from <Picture 1>.
<Subject 2> is the environment 场景描述, whose appearance comes from <Picture 2>.

summary:
[reference generation] The target video shows 主体 in 场景, 动作, lasting N seconds at R aspect.

retention_analysis:
<Subject 1> (appears in [Shot N]): fully_preserved - the ... keeps the appearance and consistency defined in subject_definitions.

detailed_description:
The target video is in 风格 style. [Shot 1] 场景. [Shot 2] At 00:04.000, the shot cuts to ...
A speaker (S1) says: <d>[中文/English] 原文</d>
The frame shows no subtitles, text, titles, watermarks, or decorative captions.

overall_soundscape:
环境音 1-4 句（无对白/无信息时 N/A）

non_diegetic_music:
配乐描述；策略含"无BGM"时 N/A
```

- **标签体系**：`<Subject N>` = 可复用内容（角色/场景，subject_definitions 定义）；
  `<Picture N>` = 图片锚点（对应 payload 图片顺序 / assets_manifest.json references 顺序）。
- **保留档位**：`retention_analysis` 的标记由 `h3.retention_level` 控制（默认 fully_preserved，
  可选 partially_preserved / attribute_transfer / weak_reference）——换装/换景场景改用 partially_preserved 或 attribute_transfer。
- **任务类型**：`summary` 的 `[...]` 前缀由 `h3.task_type` 控制（默认 reference generation，
  官方 6 类可 `+` 组合：keyframe completion / reference generation / video editing / video continuation / audio reuse / audio reference）。
- **说话人**：`(S1)(S2)...` 按目标视频实际发声事件顺序全局分配，跨镜头复用；台词原文逐字保留进 `<d>[语言]</d>`。
- **时间戳**：首镜无时间戳，后续镜头 `At MM:SS.mmm, the shot cuts to ...`。
- 字幕/文字策略由 `h3.audio_text_policy` 控制（默认"禁字幕+无BGM"）。

### 3.2 i2v 模式 → I2VA 三字段（base-en.txt）

```
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description:
[Shot 1] 风格 style, 场景. [Shot 2] At 00:04.000, the shot cuts to ...
A speaker (S1) says: <d>[中文/English] 原文</d>

overall_soundscape:
环境音 1-4 句（无对白/无信息时 N/A）

non_diegetic_music:
配乐描述；策略含"无BGM"时 N/A
```

- Part One = 首帧对齐指令（固定句，必须第一行 + 空行后接字段）；
- `<Picture 1>` 恒指本镜首帧（payload role=first_frame）；ratio 固定 adaptive（官方约束）。

## 4. payload 样例（reference 模式）

```json
{
  "model": "MiniMax-H3",
  "content": [
    {"type": "text", "text": "subject_definitions:\n..."},
    {"type": "image_url", "image_url": {"url": "assets/faces/shot_000_t000.0_f0.jpg"}, "role": "reference_image"},
    {"type": "image_url", "image_url": {"url": "assets/scenes/shot_000.jpg"}, "role": "reference_image"}
  ],
  "resolution": "768P",
  "duration": 4,
  "ratio": "9:16",
  "aigc_watermark": false,
  "_meta": {"shot_id": 0}
}
```

- `_meta` 为工程内部字段，提交工具剥离后发送。
- 提交工具会把相对图片路径自动转 base64 data URL。

## 5. 限制校验（validation_report.json）

逐资产运行 `validate_image()`，对照第 1 节图片约束：
- 文件存在性、扩展名（jpg/jpeg/png/webp/heic/heif）
- 文件体积 ≤30MB、请求体总量 ≤64MB（提示）
- 宽高 ∈ [256, 5760]、宽高比 ∈ [0.4, 2.5]
- reference_image 数量 ≤9（超限截断并警告）

报告格式：`{ "<资产id>": ["警告1", ...], "_notes": [...] }`，空数组 = 通过。

## 6. 提交工具（tools/submit_minimax_h3.py）

```powershell
$env:MINIMAX_API_KEY = "sk-..."
python tools\submit_minimax_h3.py output\<视频名>\minimax_h3\payloads --poll --poll-interval 10
```

- `--key` 优先级高于环境变量；`--base-url` 可切换国际站 `https://api.minimax.io`。
- 结果写入 `submit_results.json`：`{shot_id: {task_id, status, video_url, error}}`。
- status：submitted → Processing/Success → succeeded（含 video_url）或 failed。

## 7. 常见拒绝码对照

| 现象 | 原因 | 处理 |
|---|---|---|
| 400 图片格式 | HEIC 等未解码 | 转 JPG/PNG 后重试 |
| 413 | 单图 >30MB 或请求体 >64MB | 压图（byted-mediakit-image 或 ffmpeg） |
| 422 参数 | ratio 与模式冲突 / 时长非整数 | i2v 模式勿传 ratio；时长取整 |
| 提示词超限 | >7000 字符 | 精简 detailed_description / 台词 |
