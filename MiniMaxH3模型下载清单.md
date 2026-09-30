# MiniMax H3 模型下载清单

> 部署到：`F:\Develop\ComfyUI\models\`
> 下载源：夸克网盘 https://pan.quark.cn/s/762097a36829 （AI-NovelFlow 官方提供，文件名与工作流完全匹配）
> 备选源：HuggingFace `Comfy-Org/MiniMax-H3`（官方 pruned int8 优化版，体积更小；但文件名不同，需改工作流模板，不建议新手用）

## 必下模型（5 个，合计约 90GB+）

| # | 模型文件 | 类型 | 放置目录 | 预计大小 | 用途 |
|---|---|---|---|---|---|
| 1 | `minimax_h3_ref2va_bf16.safetensors` | Diffusion 主模型 | `models\diffusion_models\` | ~60GB | H3 参考图生视频核心模型 |
| 2 | `qwen3vl_32b_minimax_h3_int8_convrot.safetensors` | 文本/视觉编码器 | `models\text_encoders\` | ~32GB | 提示词与参考图编码 |
| 3 | `minimax_h3_video_vae_fp16.safetensors` | 视频 VAE | `models\vae\` | ~2GB | 视频帧编解码 |
| 4 | `minimax_h3_audio_vae_fp32.safetensors` | 音频 VAE | `models\vae\` | ~1GB | 音频编解码 |
| 5 | `minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy.safetensors` | 加速 LoRA | `models\loras\` | ~1GB | 4 步加速（可选但推荐） |

## 下载后验证步骤

1. 文件放好后**重启 ComfyUI**（刷新模型列表）：
   ```powershell
   # 若 ComfyUI 未运行：
   cd F:\Develop\ComfyUI
   .\venv\Scripts\python main.py
   ```
2. ComfyUI 里验证模型可见：
   - 新建节点 → UNETLoader → 模型列表出现 `minimax_h3_ref2va_bf16`
   - CLIPLoader（type=minimax）→ 出现 `qwen3vl_32b_minimax_h3_int8_convrot`
   - VAELoader → 出现两个 `minimax_h3_*_vae`
   - LoraLoaderModelOnly → 出现 turbo LoRA
3. 用 AI-NovelFlow 验证：
   - 前端 → Video Director → 选"首尾帧视频"或"四关键帧视频"
   - 提交一个分镜 → 后端调 ComfyUI H3 工作流生成视频

## 性能预期（RTX 4090 32GB + 64GB 内存，已评估）

| 分辨率 | 建议 | 说明 |
|---|---|---|
| 608×352 | 首选测试档 | 20s 约 6-8 分钟（依赖 offload） |
| 640×384 | 测试后可尝试 | 清晰度略升 |
| 720×416+ | 谨慎使用 | 显存接近上限，速度明显变慢 |

**注意**：
- 模型 60GB 装不进 32GB 显存，ComfyUI 自动 offload 到内存（64GB 充裕，稳定但稍慢）
- 一次只跑一个 H3 任务，勿同时跑其他大模型（如角色图生成）
- 首次生成建议 124 帧（~5s）短片段测试，确认效果后再加长

## 若显存/内存紧张（备选）

改用 HuggingFace `Comfy-Org/MiniMax-H3` pruned int8 版（模型足迹 ~42.5GB，RTX 3060 级别都能跑），但需同步修改 AI-NovelFlow 工作流模板里的模型文件名（找我帮你改）。
