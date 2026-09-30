# 漫剧抽帧与属性管线（ManjuToSplitFrameAndProperty）

从一段视频中**抽取镜头结构（抽帧/分镜）、结构化内容（台词/画面描述）、抽取资产（角色/场景）**，
并导出面向多平台（MiniMax H3 / Seedance / Kling / Veo / 即梦）的**再生成提示词包与 API payload**。

属于 `New_AI_Production_Workflow` 漫剧工作流生态的一员：
- 上游：`AI-NovelFlow`（剧本→文字分镜）产出的成片或参考片
- 平级：`ComfyUI-H3-Prompt-Builder`（LLM 版 H3 提示词引擎，本工程提示词结构与 MiniMax 官方规范对齐）
- 本工程定位：**从视频逆向出分镜表 + 资产库 + 逐镜生成提示词**，既可独立 CLI 运行，
  也可作为 ComfyUI 自定义节点接入工作流（`pipeline/comfyui_nodes.py`）。

> ⚠️ **合规前提（使用前必读）**
> 本管线只处理**你有权使用的内容**（自有拍摄 / 授权素材 / 公有领域）。
> 产出用于"借鉴镜头语言、重建原创内容"，不得复刻他人作品的剧情、台词、音乐，
> 不得对真人形象未经授权换脸。每次产出的提示词包末尾均附合规自检清单。

---

## 一、快速开始

```powershell
# 1. 建环境（Python 3.12）
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt

# 2. 一键跑全流程（默认导出 MiniMax H3 包）
.\.venv\Scripts\python run_all.py "D:\素材\my_video.mp4"

# 3. 常用参数
#    --asr-model off       关闭台词转录（如无对白）
#    --platform seedance   改导其他平台提示词包
#    --vlm-provider dashscope   开启视觉理解（需 DASHSCOPE_API_KEY）
.\.venv\Scripts\python run_all.py "my_video.mp4" --asr-model off --platform minimax_h3
```

产物位于 `output/<视频文件名>/`：
```
├── video_info.json        # 元信息（时长/分辨率/fps/音轨）
├── shots.json             # 镜头表（start/end/关键帧）
├── storyboard.json        # 分镜表（镜头+台词+画面描述）
├── assets.json            # 资产库（角色/场景/道具）
├── prompt_pack.md/.json   # 通用平台提示词包（seedance/kling/veo/jimeng）
└── minimax_h3/            # MiniMax H3 专用导出（见下）
    ├── prompts.md                 # 逐镜提示词（Ref2VA 六段式 / I2VA 三字段，可人工提交海螺/平台）
    ├── payloads/shot_XXX.json     # 逐镜 API payload（POST /v2/video_generation）
    ├── assets_manifest.json       # 资产清单 + 资源映射（角色=图N）
    ├── mapping.txt
    └── validation_report.json     # 资产合规校验（格式/体积/尺寸/数量）
```

---

## 二、管线结构（四阶段 + 双出口）

```
视频 ──▶ ① 解析 ──▶ ② 结构化 ──▶ ③ 资产抽取 ──▶ ④ 生成
       抽帧/镜头切分   ASR台词+VLM描述   角色/场景资产   ├─ 通用平台提示词包
       关键帧          → 分镜表          → 资产库        └─ MiniMax H3 导出
```

| 阶段 | 模块 | 产出 | 关键工具 |
|---|---|---|---|
| ① 解析 | `pipeline/stage1_parse.py` | `shots.json` / `keyframes/` | ffmpeg、scenedetect（回退 cv2） |
| ② 结构化 | `pipeline/stage2_structurize.py` | `storyboard.json` / `subtitles.srt` | faster-whisper、VLM（可选） |
| ③ 资产 | `pipeline/stage3_assets.py` | `assets.json` / `assets/` | OpenCV Haar + 感知哈希聚类 |
| ④ 生成 | `pipeline/stage4_generate.py` + `pipeline/h3_export.py` | 多平台提示词包 / H3 payload | 模板化组装 |
| H3 提交 | `tools/submit_minimax_h3.py` | `submit_results.json` | requests（POST+轮询） |

每个阶段可独立运行：`python -m pipeline.stageX <视频>`；产物为结构化 JSON，可在任意一步人工修订后续跑。

---

## 三、MiniMax H3 导出说明

H3 导出（`--platform minimax_h3`）生成两种模式（`config.yaml → h3.mode`）：

- **reference（默认，多模态参考生视频）**：镜头内的角色裁片/场景图作为 `reference_image`
  传入，提示词用 `<Picture N>` 标签引用 → 适合"资产替换再生成"（换人物/场景/服装）。
- **i2v（首帧图生视频）**：镜头关键帧作为 `first_frame` 传入 → 让原画面"动起来"。

提示词结构对齐 **MiniMax 官方 `skills/h3-prompt-writing` 规范**（官方原文存档于 `docs/references/`）：

**reference 模式 → Ref2VA 六段式**（官方 `ref-en.txt`）：
```
subject_definitions:      <Subject N> is ... whose appearance comes from <Picture N>
summary:                  [reference generation] 一句话概括（时长/画幅）
retention_analysis:       <Subject N> (appears in [Shot N]): fully_preserved - ...
detailed_description:     风格开场 + [Shot N]（后续镜头 At 00:SS.mmm 切点）+ 机位/动作/对白 <d>[语言] 原文</d> + speaker (S1)
overall_soundscape:       环境音（1-4句，无则 N/A）
non_diegetic_music:       配乐（策略含无BGM时写 N/A）
```

**i2v 模式 → I2VA 三字段**（官方 `base-en.txt`）：
```
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.
integrated_multimodal_description: [Shot N] ...
overall_soundscape: ...
non_diegetic_music: ...
```

提交成片：`python tools/submit_minimax_h3.py output\<视频名>\minimax_h3\payloads --poll`
（需环境变量 `MINIMAX_API_KEY`；本地图片自动转 base64 data URL）。
官方规格与限制校验见 `docs/03_MiniMaxH3导出规范.md`。

---

## 四、ComfyUI 集成

节点分类 `MiniMax H3 / 漫剧 / 抽帧与属性`，共 5 个节点（含一键全流程节点）：
`ManjuSplitFrameParse` / `ManjuStructurize` / `ManjuAssetExtract` / `ManjuFullPipeline` / `ManjuH3Export`。

安装：复制 `comfyui/` 目录到 `ComfyUI/custom_nodes/`，重启 ComfyUI；
示例工作流见 `workflows/manju_splitframe_workflow.json`。详见 `docs/02_ComfyUI集成指南.md`。

---

## 五、目录结构

```
ManjuToSplitFrameAndProperty\
├── run_all.py / config.yaml / requirements.txt / README.md
├── pipeline\                # 核心模块（四阶段 + h3_export + comfyui_nodes）
├── comfyui\                 # ComfyUI 插件入口（复制到 custom_nodes 即可）
├── workflows\               # ComfyUI 示例工作流 JSON
├── tools\submit_minimax_h3.py   # H3 提交工具
├── schemas\                 # storyboard/assets JSON Schema
├── samples\                 # 合成测试视频生成脚本
├── docs\                    # 全生命周期开发文档 + 用户手册
└── output\                  # 运行产物
```

## 六、文档索引

| 文档 | 内容 |
|---|---|
| `docs/00_全生命周期开发文档.md` | 需求→架构→模块→数据契约→测试→部署→运维→路线图 |
| `docs/01_用户操作手册.md` | 安装、快速开始、参数、H3 提交、故障排查 |
| `docs/02_ComfyUI集成指南.md` | 节点清单、安装、工作流、与 H3-Prompt-Builder 协同 |
| `docs/03_MiniMaxH3导出规范.md` | 官方 API 规格、六段式结构、payload、限制校验 |
| `docs/04_变更记录.md` | 版本变更记录 |
