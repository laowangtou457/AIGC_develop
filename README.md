# AIGC 影视智能创作平台（AI-NovelFlow 全家桶）

基于 **AI-NovelFlow**（FastAPI + React）本地化改造的影视智能创作平台，集成 **ComfyUI-H3-Prompt-Builder** 漫剧引擎与 **ManjuToSplitFrameAndProperty** 视频解析管线，提供三条可独立运行的创作工作流：

1. **小说生成视频**：导入小说 → AI 解析角色/场景/道具 → H3 分镜 → 关键帧 → 音频 → 视频成片
2. **AI 武术指导**：一句话需求 → 16 宫格武打分镜 → 角色形象图/分镜图 → 武打视频
3. **视频资源替换**：上传参考成片 → 切镜/抽资产 → 替换原创形象 → 重导出多平台逐镜提示词

全部能力本地运行（Ollama + ComfyUI），数据不出本机。

---

## 功能特性

### ① 小说生成视频工作流

```
导入小说 → AI解析角色/场景/道具 → 编辑章节 → H3生成文字分镜 → 生成分镜提示词(H3)
                                                  │
                    AI解析角色/场景/道具（并行）──► 素材注入
                                                  │
                        生成主分镜图/关键帧 → 音频/台词合成 → 视频生成 → Shot合并 → 章节成片
```

- H3 漫剧导演规则自动拆镜头（节拍分析、运镜、景别、情绪外化、边界锁）
- 逐镜生成 **MiniMax H3 六段式提示词**（subject / action / atmosphere / camera / lighting / meta），兼容 Seedance、Kling、Veo、即梦、海螺
- 角色自动去重（同名/别名合并，如"古月方源=方源"；同年龄段只保留一个形象）
- 角色/场景/道具外观描述支持 AI 重生成、保存后自动中译英
- 关键帧规划 + 多关键帧支持 >15s 镜头；Shot 合并生成章节成片

### ② AI 武术指导并生成视频工作流

```
一句话需求（+参考图）→ 需求解析 → 人物锚定卡（多角色）→ 16宫格武打分镜提示词
  → 角色形象图（文生图）→ 分镜图（图生图）→ 武打视频（图生视频）
```

- 参考图由 qwen2.5vl 视觉模型解析生成资产
- 人物锚定卡支持多角色定义（性别/年龄/服装/武器/形象细节），全英文输出
- 16 个独立镜头块：递增时间戳、动作不重复、机位逐镜变化、面部清晰约束
- 视频单段 15s（模型上限），自动分段拼接 30/45/60s 成片
- 历史任务持久化，刷新不丢，可回看产物

### ③ 视频导入解析替换资产并生成视频工作流

```
上传参考成片 → 镜头切分 + 关键帧 → ASR台词 → VLM画面描述 → 资产抽取
  → 多平台逐镜提示词导出 → 替换原创形象 → 重新导出 → ZIP打包下载
```

- 场景切分（scenedetect）+ 关键帧抽取 + faster-whisper 台词转录
- 资产抽取：角色人脸裁片 / 场景代表帧 / 道具
- 产物含 `prompt_pack.md`（seedance/kling/veo/jimeng 通用）与 `minimax_h3/` 专用包（六段式提示词 + 逐镜 payload + 资产映射 + 合规校验）
- **资产替换**：把参考图换成无版权问题的原创形象（3D 扫描脸型、原创立绘），重跑导出 → 得到"剧情结构、台词、音乐与原片接近，但人物、场景、服装全部替换"的成片
- 内置合规自检清单（自有/授权素材、实质性改写、非盈利使用）

---

## 架构

```
┌─────────────────────────── 浏览器（React :5173）───────────────────────────┐
│  欢迎页 / 小说管理 / 武术指导 / 视频资源替换 / 任务进程监控                    │
└───────────────────────────────┬────────────────────────────────────────────┘
                                 │ /api 代理
┌───────────────────────────────▼────────────────────────────────────────────┐
│                    AI-NovelFlow 后端（FastAPI :8000）                        │
│  h3_workflow 路由 ── H3ManjuService ── h3_prompt_builder 规则库              │
│  martial_arts 路由 ── 锚定卡/16宫格/生图/生视频编排                           │
│  video_asset 路由 ── 子进程调用 ManjuToSplitFrameAndProperty 管线            │
│  gpu_scheduler（GPU 串行调度） / 任务监控 / 历史任务持久化                    │
└───────┬──────────────────┬──────────────────────┬───────────────────────────┘
        │                  │                      │
   Ollama :11434     ComfyUI :8188         ManjuToSplitFrameAndProperty
   qwen3:8b          Flux2-Klein-4B        视频解析管线（独立 venv）
   qwen3:30b-a3b     MiniMax H3 工作流
   qwen2.5vl
```

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.12、FastAPI、SQLAlchemy 2.0（SQLite）、Uvicorn |
| 前端 | React 18、TypeScript、Vite 5、Zustand、Tailwind CSS |
| LLM | Ollama（qwen3:8b / qwen3:30b-a3b / qwen2.5vl） |
| 生图 | ComfyUI + Flux2-Klein-4B（另备 SDXL 动漫底模） |
| 视频 | ComfyUI + MiniMax H3 工作流（ref2va / 首尾帧 / 多关键帧） |
| 视频解析 | scenedetect、faster-whisper、OpenCV、qwen2.5vl VLM |

---

## 目录结构

```
NewAIProductionWorkflow/
├── AI-NovelFlow/                        # 主程序（FastAPI + React）
│   ├── backend/app/
│   │   ├── api/                         # h3_workflow / martial_arts / video_asset 等路由
│   │   ├── services/                    # H3 引擎 / 武术指导编排 / GPU 串行调度
│   │   └── models/                      # 数据模型
│   └── frontend/my-app/src/
│       ├── pages/                       # Welcome / ChapterGenerate / MartialArts / VideoAssetSwap / Monitor
│       └── components/  api/  stores/
├── ManjuToSplitFrameAndProperty/        # 视频解析替换管线（独立 venv）
│   ├── run_all.py                       # 四阶段主入口
│   ├── pipeline/  tools/  config.yaml   # 切镜/ASR/资产/提示词导出
│   ├── docs/                            # 开发文档 / 操作手册 / H3 导出规范
│   └── output/<视频名>/                 # 管线产物（分镜/资产/提示词包）
├── ComfyUI-H3-Prompt-Builder/           # H3 漫剧规则来源（备份，未改动）
├── docs/                                # 平台级开发文档（UML/变更/对比）
├── 一键启动全家桶.bat                   # 一键拉起 Ollama/ComfyUI/后端/前端
└── MiniMaxH3模型下载清单.md
```

---

## 环境要求

| 项 | 最低 | 推荐 |
|---|---|---|
| 系统 | Windows 10/11 | Windows 11 |
| GPU | RTX 30 系 12GB | RTX 4090 32GB |
| 内存 | 32GB | 64GB |
| 磁盘 | 50GB 可用（模型另算） | 100GB+ |
| 软件 | Python 3.12、Node.js 18+、Ollama、ComfyUI、ffmpeg | 同左 |

> 未配置 NVIDIA GPU 时，文字分镜/提示词链路仍可用；生图/生视频依赖 ComfyUI + GPU。

## 快速开始

### 方式一：一键启动（推荐）

双击根目录 `一键启动全家桶.bat`，依次拉起 4 个服务并健康检查：

| 服务 | 端口 | 说明 |
|---|---|---|
| Ollama | 11434 | 本地 LLM（qwen3 系列 / qwen2.5vl） |
| ComfyUI | 8188 | 生图（Flux2-Klein-4B）/ 生视频（MiniMax H3） |
| AI-NovelFlow 后端 | 8000 | FastAPI |
| AI-NovelFlow 前端 | 5173 | React，浏览器访问 http://localhost:5173 |

### 方式二：手动启动

```bash
# 后端
cd AI-NovelFlow/backend
venv/Scripts/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# 前端
cd AI-NovelFlow/frontend/my-app
npm run dev
```

### 首次配置

1. **系统设置 → LLM**：厂商选 `Ollama`，API URL `http://127.0.0.1:11434/v1`
2. **系统配置 → ComfyUI 工作流**：确认生图/生视频工作流映射完整（含提示词输入节点与图片保存节点）
3. 拉取模型（见 `MiniMaxH3模型下载清单.md`）：
   - Ollama：`qwen3:8b`、`qwen3:30b-a3b`、`qwen2.5vl`
   - ComfyUI：Flux2-Klein-4B（含 VAE）、MiniMax H3 相关节点/模型

---

## 使用入口

| 功能 | 入口 | 说明 |
|---|---|---|
| 小说生成视频 | 小说管理 → 章节生成页 | 分镜拆分 → 分镜提示词(H3) → 分镜图 → 音频 → 视频 → 合并 |
| AI 武术指导 | 侧边栏【武术指导】 | 一句话需求 → 16 宫格分镜 → 形象图/分镜图/武打视频 |
| 视频资源替换 | 侧边栏【视频资源替换】 | 上传成片 → 浏览分镜/资产 → 替换 → 下载提示词包 |
| 任务监控 | 侧边栏【任务进程监控】 | 任务状态、显存占用、LLM pending 时长 |

## 模型分工

| 任务 | 模型 | 说明 |
|---|---|---|
| 解析类（角色/场景/道具、H3 提示词） | qwen3:8b | 轻量稳定 |
| 推理/规划（H3 文字分镜、关键帧规划） | qwen3:30b-a3b | 长文输出更丰满 |
| 视觉理解（参考图、画面描述） | qwen2.5vl | 本地 VLM |
| 生图 | Flux2-Klein-4B | 默认；9B 显存占用过高已弃用 |
| 动漫风格生图 | SDXL + Animagine XL / anything-v5 | 备用底模 |
| 视频 | MiniMax H3 工作流 | ref2va / 首尾帧 / 多关键帧 |

## 显存治理（RTX 4090 32GB 实测）

1. **GPU 串行调度**：全局锁保证同刻只跑一个模型任务
2. **Ollama 自动卸载**：ComfyUI 排队时 `keep_alive:0` 卸载 LLM
3. **视频前释放**：视频生成前调 ComfyUI `/free` 释放缓存
4. 解析 → 卸载 LLM → 生图 串行，避免 OOM 死锁

---

## 合规声明

本项目的"视频资源替换"工作流用于**个人学习与非盈利展示**。使用前请逐项确认：

- 输入视频为自有 / 已授权 / 公有领域内容
- 剧情、台词、音乐已实质性改写（未逐帧复刻）
- 人物形象已替换为无版权问题的原创形象（原创/授权/自扫描）
- 生成结果不用于盈利或侵权用途

## 相关文档

| 文档 | 位置 |
|---|---|
| 三工作流集成说明 | `H3漫剧工作流集成说明.md`（根目录） |
| 平台开发文档（UML/变更） | `docs/` |
| 管线开发文档/操作手册 | `ManjuToSplitFrameAndProperty/docs/` |
| H3 六段式提示词框架控制 | `ManjuToSplitFrameAndProperty/docs/六段式提示词框架控制.md` |
| 模型下载清单 | `MiniMaxH3模型下载清单.md` |

## License

本项目基于开源项目 [AI-NovelFlow](https://github.com/qzw881130/AI-NovelFlow)、[ComfyUI-H3-Prompt-Builder](https://github.com/jiel71365-commits/ComfyUI-H3-Prompt-Builder) 本地化改造。请遵循各上游项目开源协议；本项目自定义部分版权归作者所有，仅供学习交流。
