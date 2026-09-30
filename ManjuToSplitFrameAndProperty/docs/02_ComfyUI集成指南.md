# 02 ComfyUI 集成指南（漫剧抽帧与属性管线）

> 面向 ComfyUI 用户。本工程可整体以自定义节点形式接入 ComfyUI，与
> `ComfyUI-H3-Prompt-Builder`（LLM 版 H3 提示词引擎）并存协作。

---

## 1. 节点清单

分类（CATEGORY）：**`MiniMax H3 / 漫剧 / 抽帧与属性`**

| 节点 | 输入 | 输出 | 说明 |
|---|---|---|---|
| ManjuSplitFrameParse | video 路径, config | shots_json, workdir | 阶段1：镜头切分+关键帧 |
| ManjuStructurize | video 路径, config, asr_model, vlm_provider | storyboard_json, workdir | 阶段2：ASR+VLM 分镜表 |
| ManjuAssetExtract | video 路径, config | assets_json, workdir | 阶段3：角色/场景资产 |
| ManjuFullPipeline | video 路径, config, platform, asr_model | shots/storyboard/assets JSON, prompt_pack_text, workdir | 一键 1-4 阶段 |
| ManjuH3Export | storyboard_json, assets_json, workdir, model, resolution, ratio, mode, duration_auto, duration_seconds, audio_text_policy | h3_prompts_md, payloads_json, manifest_json, warnings | 纯 JSON→JSON，H3 六段式 + payload |

**ManjuH3Export 是纯内存节点**：不读写磁盘（workdir 仅用于可选资产校验），
输入来自上游节点输出的 JSON 文本即可工作——这是它与 `ComfyUI-H3-Prompt-Builder` 中
"读文件/读目录"节点最大的差别，适合套在任意"分镜表/资产库"数据流后面。

---

## 2. 安装步骤

```powershell
# 1) 复制插件包（只复制 comfyui 目录本身）
Copy-Item "F:\Develop\NewAIProductionWorkflow\ManjuToSplitFrameAndProperty\comfyui" `
          "F:\Develop\ComfyUI\custom_nodes\ManjuToSplitFrameAndProperty" -Recurse

# 2) 依赖装进 ComfyUI 的 Python 环境（在 ComfyUI 根目录的 venv 里执行）
& "F:\Develop\ComfyUI\python_embeded\python.exe" -m pip install -r "F:\Develop\NewAIProductionWorkflow\ManjuToSplitFrameAndProperty\requirements.txt"

# 3) 重启 ComfyUI
```

重启后，搜索 "漫剧" 或 "Manju" 即可看到节点。

> 提示：若 ComfyUI 使用独立嵌入 Python（python_embeded），需确保其 pip 可用；
> 与 ComfyUI 自带依赖（numpy/opencv）冲突时，以 requirements.txt 固定版本为准。

---

## 3. 示例工作流

`workflows/manju_splitframe_workflow.json`（导入 ComfyUI 即用）：

```
PrimitiveNode(视频路径)
   ├─▶ ManjuSplitFrameParse ─▶ (shots_json)
   ├─▶ ManjuStructurize   ─▶ (storyboard_json) ─▶ ManjuH3Export ─▶ prompts_md
   └─▶ ManjuAssetExtract  ─▶ (assets_json)    ─┘                 ─▶ payloads_json
                                                                ─▶ warnings
```

组合玩法：
- **上游接 LLM 分镜**：若你的分镜表来自 `AI-NovelFlow` 或 H3-Prompt-Builder 的文本节点，
  直接把 JSON 接进 `ManjuH3Export` 的 storyboard_json 即可（无需先跑视频阶段）。
- **下游接 SaveText**：把 `prompts_md` / `payloads_json` 接到 SaveText/PreviewText 保存成文件。
- **下游接 H3 API 节点**：`payloads_json` 字符串可转发给自定义 API 调用节点（或本工程提交工具）。

---

## 4. 与 ComfyUI-H3-Prompt-Builder 的协同

| 维度 | H3-Prompt-Builder | 本工程 |
|---|---|---|
| 输入 | LLM 文本分镜 + 素材目录 | 视频逆向 JSON（或外部 JSON） |
| 提示词 | LLM 六段式（可对话迭代） | 确定性六段式模板（可审计） |
| 资产引用 | 手动指定 <Picture N> | 自动映射（assets_manifest） |
| 定位 | 创意发散、提示词打磨 | 批量逆向、资产提取、合规校验 |

推荐串法：**本工程抽资产/出初稿 → H3-Prompt-Builder 用 LLM 精修文案 → 统一 payload 提交**。
两者提示词结构一致（subject_definitions/summary/retention_analysis/detailed_description/overall_soundscape/non_diegetic_music），
可无缝互换。

---

## 5. 节点开发约定（给开发者）

- 节点类实现 `INPUT_TYPES/RETURN_TYPES/RETURN_NAMES/FUNCTION/CATEGORY`，与官方规范一致。
- 所有跨节点数据用 JSON 字符串（`STRING`），保持与既有生态一致、可预览、可编辑。
- 新增节点：在 `comfyui_nodes.py` 注册到 `NODE_CLASS_MAPPINGS`；`comfyui/__init__.py` 无需改动。
- 脱离 ComfyUI 测试：`python -m pipeline.comfyui_nodes --self-test`。

---

## 6. 故障排查

| 现象 | 处理 |
|---|---|
| 节点列表里找不到"漫剧" | 确认 comfyui 目录结构（`comfyui\__init__.py` 在根）；看 ComfyUI 启动日志有无 import 报错 |
| 节点报 `pipeline` 不存在 | 插件包被装错层级；`__init__.py` 会自动把工程根加入 sys.path，需保持 comfyui 与 pipeline 同级 |
| ManjuSplitFrameParse 报视频找不到 | video 输入必须是**本机绝对路径**（ComfyUI 不解析相对路径） |
| H3 输出 warnings 非空 | 按 warnings 提示修正资产图（格式/尺寸/体积），参考 docs/03 |


---

## 附：当前部署现状（2026-09-30）

- 本机 ComfyUI 位于 `F:\Develop\ComfyUI`（全家桶一键启动拉起，端口 :8188）
- 平台「视频资源替换」已通过**子进程隔离**调用本管线，无需把 `comfyui/` 插件装入 ComfyUI 即可使用
- 若仍希望以 ComfyUI 节点方式使用：按上文复制 `comfyui/` 到 `F:\Develop\ComfyUI\custom_nodes\` 并重启
