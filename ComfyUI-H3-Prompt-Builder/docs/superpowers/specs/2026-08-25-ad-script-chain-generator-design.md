# 广告剧本生成器 + Contex-Loop 对接设计文档

- 日期：2026-08-25
- 状态：待用户审阅（已确认：扩展现有插件、批量生成+序号选择、LLM 转换+程序组装）
- 载体：现有插件 `ComfyUI-H3-Prompt-Builder`，新增节点集中在新文件 `ad_script_nodes.py`，配套 `rules/ad_script.txt`、`rules/ad_chain_plan.txt`、`self_test.py`、`README.md`
- 对接插件：`ComfyUI-MiniMaxH3-Contex-Loop`（只消费其公开的 `plan_json_input` STRING 输入口，不改其代码）

## 1. 背景

1. 用户要为商家批量制作 15-30 秒宣传短视频（如拼豆店、烧烤店），剧情走「反差悬疑」路线：前半段与店铺无关、氛围拉满，结尾突然揭晓是店铺广告，制造「无语感」。
2. 用户已使用 `ComfyUI-MiniMaxH3-Contex-Loop` 把单段 15s 扩展为 30s+（多场景链式生成）。该插件的 `MiniMaxH3ChainPlan` 节点提供公开的 `plan_json_input`（STRING）输入口：非空连接值会覆盖内置 `plan_json` 并走同一套校验，这正是本插件对接的官方入口。
3. 用户需要一个「剧本生成器」：输入 API 配置与店铺需求 → 一次生成多个剧本 → 用户选择其中一个 → 转成 Contex-Loop 可用的 plan JSON → 直接驱动生成。

## 2. 范围

### 做
- 新增节点「广告剧本批量生成」`AdScriptBatch`：一次 LLM 调用生成 N 个剧本（默认 3，上限 5），输出剧本列表 JSON 与选择预览。
- 新增节点「剧本选择」`AdScriptSelect`：输入列表 + 序号，输出选中的剧本与标题。
- 新增节点「剧本转 Chain Plan JSON」`AdScriptToChainPlan`：LLM 产出 `prompt_prefix` 与每场英文 prompt，程序组装为严格合法的 plan JSON（17k+5 帧网格、seeds、defaults）。
- 新增规则文件 `rules/ad_script.txt`（批量剧本规则）与 `rules/ad_chain_plan.txt`（剧本→plan JSON 转换规则）。
- 扩展 `self_test.py`：帧网格计算、JSON 合法性、节点字段、空输入/越界提示等离线用例。
- 更新 `README.md`：新节点用法与 Contex-Loop 接线说明。

### 不做
- 不改 `ComfyUI-MiniMaxH3-Contex-Loop` 的任何代码；只输出它接受的 STRING。
- 不自动触发整个 workflow（用户自行把 `plan_json` 连到 `plan_json_input`）。
- 不做多剧本自动打分/挑选（本期只做手动序号选择）。
- 不做批量 API 封装（本期保证节点接口简单，批量由 ComfyUI API 逐请求替换输入实现）。
- 不接生图模型；角色/场景参考图由用户在 workflow 中自行连接。

## 3. 节点 1：广告剧本批量生成（`AdScriptBatch`）

### 输入 / 输出
- 输入（required）：`business`（主营业务，多行 STRING）、`ad_copy`（广告语，多行 STRING）
- 输入（optional，带默认）：`shop_name`（店名，空）、`target_audience`（目标人群，默认「大众」）、`style_tendency`（自动/搞笑/悬疑/温情/抽象，默认自动）、`scale_level`（保守/标准/抽象，默认标准）、`duration_seconds`（15/30/45/60，默认 30）、`script_count`（1-5，默认 3）、`api_key`/`model`/`base_url`/`llm_config`（复用 `resolve_llm_config` 与 `call_llm`）
- 输出：`scripts_json`（STRING，JSON 数组 `[{"title": "...", "script": "..."}]`）、`preview`（STRING，编号+标题+开头，供人选择）、`script_count`（INT）

### 规则（`rules/ad_script.txt`）
- 角色：短视频抽象编剧；输出「反差悬疑」为主的 15-30 秒广告剧本。
- 四段结构：钩子（0-3s 或按比例）→ 展开 → 反转揭晓店铺 → 广告落点（广告语原样出现）。
- 前半段严禁提前出现店铺/产品信息；结尾制造「无语感」反转。
- 悬疑氛围细节：人物表情、光线、音效、BGM 提示写清楚；抽象搞笑但不低俗、不涉伦理擦边、不让人反感。
- 角色 2-3 个、中文昵称；台词总量按时长控制（约 4-5 字/秒）；每场标注大致时长。
- 输出必须是严格 JSON 数组（`[{"title", "script"}]`），`script_count` 个元素，禁止额外解释。

### 失败处理
- 必填为空、LLM 失败、JSON 解析失败 → 输出中文提示文本（`scripts_json` 与 `preview` 同文案），不中断 ComfyUI。

## 4. 节点 2：剧本选择（`AdScriptSelect`）

### 输入 / 输出
- 输入：`scripts_json`（STRING）、`index`（INT，默认 0，范围 0~script_count-1）
- 输出：`selected_script`（STRING）、`selected_title`（STRING）

### 行为
- 解析数组按序号取；越界或解析失败返回中文提示。
- 切换 index 重跑时，上游批量生成节点被 ComfyUI 缓存，几乎秒出。

## 5. 节点 3：剧本转 Chain Plan JSON（`AdScriptToChainPlan`）

### 输入 / 输出
- 输入：`script`（STRING，选中剧本）、`duration_seconds`（INT，默认 30，可选 15-60）、`scene_count`（INT，默认 3，2-6）、`api_key`/`model`/`base_url`/`llm_config`
- 输出：`plan_json`（STRING，严格 JSON，可直接接 `plan_json_input`）、`plan_json_preview`（STRING，格式化 JSON 便于检查/复制）

### 转换规则（`rules/ad_chain_plan.txt`）
- LLM 输出两部分：`prompt_prefix`（全局角色/风格/氛围/连续性规则）与每场英文 `prompt` 行数组。
- 每场 prompt 遵守 Contex-Loop 写作纪律：稳定信息放 prefix、每场只写变化；上一场结尾留未完成动作、下一场开头承接该动作；台词用 `<d>[Chinese] 原文</d>` 逐字保留；写清表情/光线/音效/BGM。
- 程序组装保证格式合法：
  - 每场时长换算：`requested = ceil(seconds * 24)`，取下一个满足 `length % 17 == 5` 的帧数（10s→243、12s→294、15s→362、30s→736）。
  - `defaults.duration_seconds` 与每场 `duration_seconds`/`length` 按用户参数分配：默认 30s 分 3 场，代码按 `duration_seconds / scene_count` 均分，每场换算到 17k+5 网格，最后一场吸收向上取整的差额（示例 12+12+10，交付约 32.8s；10+10+10 交付约 28.5s）。
  - 每场生成稳定 `seed`（基于 `base_seed + index`），`id` 唯一（`scene_01`…）。
  - 输出经过 `json.dumps(ensure_ascii=False)`，确保严格 JSON。

### 失败处理
- 输入为空、LLM 失败、组装失败 → 中文提示文本。

## 6. 数据流

```
主营业务/广告语 + API 配置
  → AdScriptBatch（一次 LLM 生成 N 个剧本）
  → preview 供用户查看
  → AdScriptSelect（index 选择）→ selected_script
  → AdScriptToChainPlan（LLM 转提示词 + 程序组装）
  → plan_json ──wire──→ MiniMaxH3ChainPlan.plan_json_input
  → Contex-Loop 生成 30s+ 视频
```

## 7. Contex-Loop 对接约定（写入 README）
- 把 `AdScriptToChainPlan.plan_json` 接到 Chain Plan 节点的 `plan_json_input`（optional STRING 输入口）；非空时覆盖内置 JSON。
- 推荐 Chain Plan 设置：`run_name` 每次新 run 换名；`width/height` 512×896（竖屏广告）或 960×544；`transition_policy` Guide；`encode_mode` video；`anchor_mode` head；`audio_mode` generated_audio（让 H3 生成人声与 BGM）；`default_steps` 20 起步。
- 参考图：如需人物参考图，在 `prompt_prefix` 增加 `<Subject N>'s facial identity comes from <Picture N>.`，并在 workflow 中连接对应 Scheduled/核心 Ref2VA 引用。

## 8. 测试（`self_test.py` 离线用例）
- 规则文件 `ad_script.txt`、`ad_chain_plan.txt` 存在且非空。
- 三个节点的 INPUT_TYPES 字段齐全；空输入返回中文提示。
- `AdScriptSelect`：index 越界返回提示；正常列表取对元素。
- 帧网格函数：5/22/39/56/73…3592 命中；120/240/360 拒绝；10s→243、12s→294、15s→362、30s→736。
- `AdScriptToChainPlan` 组装结果可被 `json.loads` 解析，且含 `shots`、`prompt_prefix`（或 `global_prompt`）、每场 `id`/`prompt`/时长字段；seeds 稳定（同输入同输出）。

## 9. 范围外（后续迭代）
- 多剧本自动打分/挑选。
- 批量 API 封装（每店一请求替换输入）。
- plan_json 自动写盘并一键触发 workflow。
