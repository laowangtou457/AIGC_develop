# 平台模型下载与部署清单（含 MiniMax H3）

> 更新日期：2026-09-30 ｜ 范围：AI-NovelFlow 平台全部本地模型（LLM / 生图 / 生视频 / ASR）
> 部署根目录：`F:\Develop\ComfyUI\models\`（ComfyUI）＋ `F:\Develop\VLM\models`（Ollama）＋ HuggingFace 缓存（ASR）
> 状态说明：✅ = 已下载并部署；⬜ = 未下载（按需再下）

---

## 一、MiniMax H3 视频模型（ComfyUI，必下 5 个）

> 下载源：夸克网盘 https://pan.quark.cn/s/762097a36829 （AI-NovelFlow 官方提供，文件名与工作流完全匹配）
> 备选源：HuggingFace `Comfy-Org/MiniMax-H3`（官方 pruned int8 优化版，体积更小；但文件名不同，需改工作流模板，不建议新手用）

| # | 模型文件 | 类型 | 放置目录 | 实际大小 | 用途 | 状态 |
|---|---|---|---|---|---|---|
| 1 | `minimax_h3_ref2va_bf16.safetensors` | Diffusion 主模型 | `models\diffusion_models\` | 63.2GB | H3 参考图生视频核心模型 | ✅ |
| 2 | `qwen3vl_32b_minimax_h3_int8_convrot.safetensors` | 文本/视觉编码器 | `models\text_encoders\` | 25.9GB | 提示词与参考图编码 | ✅ |
| 3 | `minimax_h3_video_vae_fp16.safetensors` | 视频 VAE | `models\vae\` | 5.0GB | 视频帧编解码 | ✅ |
| 4 | `minimax_h3_audio_vae_fp32.safetensors` | 音频 VAE | `models\vae\` | 577MB | 音频编解码 | ✅ |
| 5 | `minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy.safetensors` | 加速 LoRA | `models\loras\` | 1.9GB | 4 步加速 | ✅ |

---

## 二、生图模型（ComfyUI）

### 2.1 Flux2-Klein 系列（主用）

| # | 模型文件 | 放置目录 | 实际大小 | 用途 | 状态 |
|---|---|---|---|---|---|
| 1 | `flux-2-klein-4b.safetensors` | `models\unet\` | 7.4GB | **默认生图模型**（角色/场景/道具/武指形象图/分镜图） | ✅ |
| 2 | `flux-2-klein-9b.safetensors` | `models\unet\` | 17.3GB | 9B 备选（显存占用过高，已弃用默认） | ✅ |
| 3 | `flux2-vae.safetensors` | `models\vae\` | 321MB | Flux2 图像 VAE | ✅ |
| 4 | `qwen_3_8b.safetensors` | `models\clip\` | 15.6GB | Flux2 文本编码器（大） | ✅ |
| 5 | `qwen_3_4b.safetensors` | `models\clip\` | 7.7GB | Flux2 文本编码器（小，可选） | ✅ |

### 2.2 SDXL 底模（动漫 / 写实备选）

| # | 模型文件 | 放置目录 | 实际大小 | 用途 | 状态 |
|---|---|---|---|---|---|
| 1 | `animagine-xl-3.1.safetensors` | `models\checkpoints\` | 6.6GB | 动漫风格底模（角色/场景/道具动漫风） | ✅ |
| 2 | `RealVisXL_V5.0_fp16.safetensors` | `models\checkpoints\` | 6.6GB | 写实风格底模 | ✅ |
| 3 | `sdxl_vae.safetensors` | `models\vae\` | 319MB | SDXL VAE | ✅ |

---

## 三、Ollama LLM / VLM（`F:\Develop\VLM\models`，blobs 共 43.9GB）

| 模型 | 实际大小 | 用途 | 状态 |
|---|---|---|---|
| `qwen3:8b` | 5.2GB | 解析类（角色/场景/道具、H3 提示词）、轻任务 | ✅ |
| `qwen3:30b-a3b` | 18GB | 推理/规划类（H3 文字分镜、关键帧规划、武指需求解析） | ✅ |
| `qwen3:32b` | 20GB | 效果备选（对比测试用，日常可停用省显存） | ✅ |
| `qwen2.5vl:3b` | 3.2GB | 视觉理解（武指参考图解析、视频管线画面描述） | ✅ |
| `gpt-oss:120b-cloud` | 云端 API | 云端引用（本地不占空间，默认已切本地 qwen3） | ⬜ 无需下载 |

> 说明：Ollama 模型目录由 `一键启动全家桶.bat` 的 `OLLAMA_MODELS=F:\Develop\VLM\models` 指定；拉取命令 `ollama pull <模型名>`。

---

## 四、ASR 台词转录（faster-whisper，HuggingFace 自动缓存）

| 模型 | 用途 | 状态 |
|---|---|---|
| `Systran/faster-whisper-tiny` | 视频资源替换管线默认台词转录（~75MB，自动下载至 `~\.cache\huggingface\hub`） | ✅ |
| `base` / `small` | 提升准确率（更慢，管线传参 `--asr-model base/small` 时自动下载） | ⬜ 按需 |

---

## 五、模型与任务分工速查

| 任务 | 模型 | 说明 |
|---|---|---|
| 解析角色/场景/道具、H3 分镜提示词 | qwen3:8b（Ollama） | 轻量稳定，串行调度 |
| H3 生成文字分镜、关键帧规划、武指需求 | qwen3:30b-a3b（Ollama） | 长文输出更丰满 |
| 参考图/画面理解 | qwen2.5vl:3b（Ollama） | 本地 VLM，Ollama 未启时降级留空不阻断 |
| 角色/场景/道具/武指形象图/分镜图 | Flux2-Klein-4B（ComfyUI unet） | 默认；9B 已弃用 |
| 动漫风生图（备选） | SDXL + animagine-xl-3.1 | 模板选「动漫人设」时用 |
| 写实风生图（备选） | SDXL + RealVisXL V5.0 | 模板选「写实人设」时用 |
| 视频生成 | MiniMax H3（ref2va / 首尾帧 / 多关键帧） | 15s 单段上限，分段拼接 30/45/60s |
| ASR 台词 | faster-whisper-tiny | 视频管线默认 |

---

## 六、下载后验证步骤

1. 文件放好后**重启 ComfyUI**（刷新模型列表）：
   ```powershell
   cd F:\Develop\ComfyUI
   .\venv\Scripts\python main.py
   ```
2. ComfyUI 里验证模型可见：
   - UNETLoader → 出现 `minimax_h3_ref2va_bf16`、`flux-2-klein-4b`
   - CLIPLoader（type=minimax）→ 出现 `qwen3vl_32b_minimax_h3_int8_convrot`
   - VAELoader → 出现 `minimax_h3_video_vae_fp16`、`minimax_h3_audio_vae_fp32`、`flux2-vae`
   - CheckpointLoader → 出现 `animagine-xl-3.1`、`RealVisXL_V5.0_fp16`
   - LoraLoaderModelOnly → 出现 turbo LoRA
3. Ollama 验证：`ollama list` 能看到 qwen3:8b / qwen3:30b-a3b / qwen2.5vl:3b
4. 用 AI-NovelFlow 验证：
   - 前端 → 小说生成页 → H3 分镜/提示词（LLM）
   - 前端 → 视频生成 → 首尾帧/多关键帧（H3 视频）
   - 前端 → 武术指导 → 形象图/分镜图/武打视频（Flux2-4B + H3）
   - 前端 → 视频资源替换 → 上传视频跑管线（ASR + VLM）

---

## 七、性能预期与显存注意（RTX 4090 32GB + 64GB 内存，已实测）

| 分辨率 | 建议 | 说明 |
|---|---|---|
| 608×352 | 首选测试档 | 20s 约 6-8 分钟（依赖 offload） |
| 640×384 | 测试后可尝试 | 清晰度略升 |
| 720×416+ | 谨慎使用 | 显存接近上限，速度明显变慢 |

**注意**：
- H3 主模型 60GB+ 装不进 32GB 显存，ComfyUI 自动 offload 到内存（64GB 充裕，稳定但稍慢）
- 平台已做 GPU 串行调度：一次只跑一个模型任务，勿手动同时跑多个大模型任务
- 生图与 H3 视频之间自动卸载 Ollama / 释放缓存（`/free`），避免 OOM 死锁
- 首次生成建议 124 帧（~5s）短片段测试，确认效果后再加长

## 若显存/内存紧张（备选）

改用 HuggingFace `Comfy-Org/MiniMax-H3` pruned int8 版（模型足迹 ~42.5GB，RTX 3060 级别都能跑），但需同步修改 AI-NovelFlow 工作流模板里的模型文件名。
