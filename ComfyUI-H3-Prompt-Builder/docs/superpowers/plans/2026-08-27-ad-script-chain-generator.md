# 广告剧本生成器 + Contex-Loop 对接 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `ComfyUI-H3-Prompt-Builder` 插件中新增三个节点（批量生成剧本 → 序号选择 → 转 Contex-Loop plan JSON），对接 `ComfyUI-MiniMaxH3-Contex-Loop` 的 `plan_json_input` 输入口，实现"输入店铺需求 → 一次出 N 个剧本 → 选一个 → 直接驱动 30s+ 广告视频生成"。

**Architecture:** 新增独立文件 `ad_script_nodes.py`（纯标准库），复用 `nodes.py` 的 `call_llm`/`load_config`/`read_text_file` 与 `manju_nodes.py` 的 `resolve_llm_config`/`build_manju_system_prompt`；两个规则文件 `rules/ad_script.txt`、`rules/ad_chain_plan.txt` 承载 LLM 指令；帧网格（17k+5）、时长分配、seed、JSON 组装全部由代码完成，保证输出严格合法。

**Tech Stack:** Python 3（标准库 json/os/math）、ComfyUI 自定义节点（纯 STRING/INT 输入输出，不依赖其他节点包）、unittest 离线自测。

---

## 前置约定

- 插件仓库根目录：`C:\Users\Administrator\Documents\ChatGPT\minimax h3 prompt\comfyui-h3-prompt-builder`
- 所有测试命令在插件仓库根目录运行，用 ComfyUI 的 Python：
  ```powershell
  & "F:\comfyui\ComfyUI-aki-v2\python\python.exe" self_test.py
  ```
- 基线：当前 `self_test.py` 共 90 项测试全部通过（`Ran 90 tests ... OK`）。
- 每次提交都在插件仓库根目录执行 `git add ...` 与 `git commit ...`。
- 部署副本：`F:\comfyui\ComfyUI-aki-v2\ComfyUI\custom_nodes\ComfyUI-H3-Prompt-Builder`（最终任务同步）。

## 文件结构

| 文件 | 动作 | 职责 |
|---|---|---|
| `ad_script_nodes.py` | 新建 | 帧网格/时长/plan 组装工具函数 + 三个节点类 |
| `rules/ad_script.txt` | 新建 | 批量剧本生成 LLM 规则 |
| `rules/ad_chain_plan.txt` | 新建 | 剧本→plan JSON 转换 LLM 规则 |
| `__init__.py` | 修改 | 注册三个新节点与展示名 |
| `self_test.py` | 修改 | 新增离线测试类 |
| `README.md` | 修改 | 新节点用法 + Contex-Loop 接线说明 |

---

### Task 1: 帧网格与时长分配工具函数

**Files:**
- Create: `comfyui-h3-prompt-builder/ad_script_nodes.py`
- Test: `comfyui-h3-prompt-builder/self_test.py`

- [ ] **Step 1: 写失败测试**

在 `self_test.py` 末尾追加：

```python
class TestAdScriptGrid(unittest.TestCase):
    def test_h3_length_grid(self):
        from ad_script_nodes import h3_length_for_seconds
        cases = {0.2: 5, 5: 124, 10: 243, 12: 294, 15: 362, 30: 736}
        for seconds, expected in cases.items():
            self.assertEqual(h3_length_for_seconds(seconds), expected, "seconds=%s" % seconds)

    def test_h3_length_valid(self):
        from ad_script_nodes import h3_length_for_seconds
        for seconds in (1, 3, 7, 11, 16, 40, 60):
            length = h3_length_for_seconds(seconds)
            self.assertTrue(5 <= length <= 3592)
            self.assertEqual(length % 17, 5, "length=%s" % length)

    def test_distribute_scene_seconds(self):
        from ad_script_nodes import distribute_scene_seconds
        self.assertEqual(distribute_scene_seconds(30, 3), [10, 10, 10])
        self.assertEqual(distribute_scene_seconds(32, 3), [10, 10, 12])
        self.assertEqual(distribute_scene_seconds(30, 1), [30])
```

- [ ] **Step 2: 运行测试确认失败**

运行：`& "F:\comfyui\ComfyUI-aki-v2\python\python.exe" self_test.py`
预期：失败，报 `ModuleNotFoundError: No module named 'ad_script_nodes'`。

- [ ] **Step 3: 新建 `ad_script_nodes.py` 并实现工具函数**

创建文件，内容：

```python
"""广告剧本生成器 + Contex-Loop plan JSON 组装 — 纯标准库实现。"""

import json
import math
import os

try:
    from .nodes import RULES_DIR, call_llm, load_config, read_text_file
    from .manju_nodes import build_manju_system_prompt, resolve_llm_config
except ImportError:
    from nodes import RULES_DIR, call_llm, load_config, read_text_file
    from manju_nodes import build_manju_system_prompt, resolve_llm_config

H3_LENGTH_MIN = 5
H3_LENGTH_MAX = 3592
H3_GRID = 17
H3_REMAINDER = 5

SCRIPT_COUNT_OPTIONS = [1, 2, 3, 4, 5]
DURATION_OPTIONS = [15, 30, 45, 60]
SCENE_COUNT_OPTIONS = [2, 3, 4, 5, 6]
STYLE_TENDENCIES = ["自动", "搞笑", "悬疑", "温情", "抽象"]
SCALE_LEVELS = ["保守", "标准", "抽象"]


def h3_length_for_seconds(seconds):
    """把秒数换算为 H3 合法原始帧数（17k+5 网格），向上取整。"""
    seconds = max(0.1, float(seconds))
    requested = int(math.ceil(seconds * 24))
    length = requested
    while length % H3_GRID != H3_REMAINDER:
        length += 1
    return max(H3_LENGTH_MIN, min(H3_LENGTH_MAX, length))


def distribute_scene_seconds(total_seconds, scene_count):
    """均分总时长，最后一场吸收余数。"""
    total_seconds = max(5, int(total_seconds or 30))
    scene_count = max(1, int(scene_count or 1))
    base = total_seconds // scene_count
    last = total_seconds - base * (scene_count - 1)
    return [base] * (scene_count - 1) + [last]
```

- [ ] **Step 4: 运行测试确认通过**

运行同上，预期：`TestAdScriptGrid` 全部 PASS，总测试数 96。

- [ ] **Step 5: 提交**

```powershell
git add ad_script_nodes.py self_test.py
git commit -m "feat: ad script frame grid and duration helpers"
```

---

### Task 2: plan JSON 组装函数

**Files:**
- Modify: `comfyui-h3-prompt-builder/ad_script_nodes.py`
- Test: `comfyui-h3-prompt-builder/self_test.py`

- [ ] **Step 1: 写失败测试**

在 `self_test.py` 末尾追加：

```python
class TestAdScriptPlan(unittest.TestCase):
    def test_build_plan_json_valid(self):
        import json as _json
        from ad_script_nodes import build_plan_json
        plan = build_plan_json(
            ["<Subject 1> keeps the same face.", ""],
            [["Open the scene.", "End mid-step."], ["Continue the stride."]],
            [10, 10],
            123,
        )
        data = _json.loads(plan)
        self.assertIn("shots", data)
        self.assertIn("prompt_prefix", data)
        self.assertEqual(data["prompt_prefix"], ["<Subject 1> keeps the same face."])
        self.assertEqual(len(data["shots"]), 2)
        self.assertEqual(data["shots"][0]["id"], "scene_01")
        self.assertEqual(data["shots"][0]["duration_seconds"], 10)
        self.assertEqual(data["shots"][0]["seed"], 124)
        self.assertEqual(data["shots"][1]["id"], "scene_02")
        self.assertEqual(data["shots"][1]["seed"], 125)
        self.assertIn("defaults", data)

    def test_build_plan_json_mismatch(self):
        from ad_script_nodes import build_plan_json
        with self.assertRaises(ValueError):
            build_plan_json(["P"], [["A"]], [10, 10], 1)

    def test_build_plan_json_empty_scenes(self):
        from ad_script_nodes import build_plan_json
        with self.assertRaises(ValueError):
            build_plan_json(["P"], [], [], 1)
```

- [ ] **Step 2: 运行测试确认失败**

运行：`& "F:\comfyui\ComfyUI-aki-v2\python\python.exe" self_test.py`
预期：失败，报 `ImportError: cannot import name 'build_plan_json'`。

- [ ] **Step 3: 实现 `build_plan_json`**

在 `ad_script_nodes.py` 的 `distribute_scene_seconds` 后追加：

```python
def build_plan_json(prefix_lines, scene_prompts, scene_seconds, base_seed=20260827):
    """把全局规则与各场 prompt 组装成 Contex-Loop plan JSON 字符串。"""
    prefix_lines = [str(x) for x in (prefix_lines or []) if str(x).strip()]
    scene_prompts = list(scene_prompts or [])
    scene_seconds = list(scene_seconds or [])
    if not scene_prompts:
        raise ValueError("缺少场景提示词")
    if len(scene_prompts) != len(scene_seconds):
        raise ValueError("场景数量与时长数量不一致")
    shots = []
    for idx, (prompt_lines, seconds) in enumerate(zip(scene_prompts, scene_seconds), start=1):
        shots.append({
            "id": "scene_%02d" % idx,
            "prompt": [str(x) for x in prompt_lines],
            "duration_seconds": int(seconds),
            "seed": (int(base_seed) + idx) % (2 ** 64),
        })
    plan = {
        "prompt_prefix": prefix_lines,
        "defaults": {"duration_seconds": int(scene_seconds[0]), "steps": 20},
        "shots": shots,
    }
    return json.dumps(plan, ensure_ascii=False, indent=2)
```

- [ ] **Step 4: 运行测试确认通过**

运行同上，预期：`TestAdScriptPlan` 全部 PASS。

- [ ] **Step 5: 提交**

```powershell
git add ad_script_nodes.py self_test.py
git commit -m "feat: assemble Contex-Loop plan JSON with strict grid"
```

---

### Task 3: 解析与选择工具函数

**Files:**
- Modify: `comfyui-h3-prompt-builder/ad_script_nodes.py`
- Test: `comfyui-h3-prompt-builder/self_test.py`

- [ ] **Step 1: 写失败测试**

在 `self_test.py` 末尾追加：

```python
class TestAdScriptParsers(unittest.TestCase):
    def test_parse_scripts(self):
        from ad_script_nodes import parse_scripts
        raw = '[{"title": "A", "script": "第一段"}, {"title": "B", "script": "第二段"}]'
        scripts = parse_scripts(raw)
        self.assertEqual(len(scripts), 2)
        self.assertEqual(scripts[0]["title"], "A")
        self.assertEqual(scripts[1]["script"], "第二段")

    def test_parse_scripts_fence(self):
        from ad_script_nodes import parse_scripts
        raw = '```json\n[{"title": "A", "script": "正文"}]\n```'
        self.assertEqual(len(parse_scripts(raw)), 1)

    def test_parse_scripts_invalid(self):
        from ad_script_nodes import parse_scripts
        self.assertIsNone(parse_scripts("不是 JSON"))
        self.assertIsNone(parse_scripts('[{"title": "A"}]'))

    def test_build_preview(self):
        from ad_script_nodes import build_preview
        scripts = [{"title": "A", "script": "第一行\n第二行"}, {"title": "B", "script": "另一段"}]
        preview = build_preview(scripts)
        self.assertIn("[0] A｜第一行", preview)
        self.assertIn("[1] B｜另一段", preview)

    def test_select_script(self):
        from ad_script_nodes import select_script
        scripts = '[{"title": "A", "script": "剧本A"}, {"title": "B", "script": "剧本B"}]'
        self.assertEqual(select_script(scripts, 1), ("剧本B", "B"))
        self.assertEqual(select_script(scripts, 0), ("剧本A", "A"))
        self.assertEqual(select_script(scripts, 9), (None, None))
        self.assertEqual(select_script("[]", 0), (None, None))
        self.assertEqual(select_script("bad", 0), (None, None))

    def test_parse_scene_pack(self):
        from ad_script_nodes import parse_scene_pack
        raw = '{"prompt_prefix": ["P1", "P2"], "scenes": [{"prompt": ["S1a", "S1b"]}, {"prompt": "S2"}]}'
        prefix, prompts = parse_scene_pack(raw)
        self.assertEqual(prefix, ["P1", "P2"])
        self.assertEqual(len(prompts), 2)
        self.assertEqual(prompts[0], ["S1a", "S1b"])
        self.assertEqual(prompts[1], ["S2"])

    def test_parse_scene_pack_invalid(self):
        from ad_script_nodes import parse_scene_pack
        self.assertIsNone(parse_scene_pack("bad"))
        self.assertIsNone(parse_scene_pack('{"scenes": []}'))
        self.assertIsNone(parse_scene_pack('{"prompt_prefix": [], "scenes": [{}]}'))
```

- [ ] **Step 2: 运行测试确认失败**

运行：`& "F:\comfyui\ComfyUI-aki-v2\python\python.exe" self_test.py`
预期：失败，报 `ImportError`（函数不存在）。

- [ ] **Step 3: 实现解析与选择工具**

在 `ad_script_nodes.py` 的 `build_plan_json` 后追加：

```python
def _strip_fence(text):
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def parse_scripts(raw):
    """解析批量生成节点的 LLM 输出：JSON 数组 [{"title","script"}]，容忍代码围栏。"""
    text = _strip_fence(raw)
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except Exception:
        return None
    if not isinstance(data, list) or not data:
        return None
    scripts = []
    for item in data:
        if not isinstance(item, dict):
            return None
        title = str(item.get("title", "")).strip()
        script = str(item.get("script", "")).strip()
        if not title or not script:
            return None
        scripts.append({"title": title, "script": script})
    return scripts


def build_preview(scripts):
    """生成供人选择的一行一个剧本的预览文本。"""
    lines = []
    for idx, item in enumerate(scripts):
        first_line = item["script"].splitlines()[0] if item["script"].splitlines() else ""
        lines.append("[%d] %s｜%s" % (idx, item["title"], first_line))
    return "\n".join(lines)


def select_script(scripts_json, index):
    """按序号取剧本，返回 (script, title)；失败返回 (None, None)。"""
    try:
        data = json.loads(scripts_json or "[]")
    except Exception:
        return None, None
    if not isinstance(data, list) or not data:
        return None, None
    try:
        item = data[int(index)]
    except (IndexError, TypeError, ValueError):
        return None, None
    if not isinstance(item, dict):
        return None, None
    return str(item.get("script", "")).strip(), str(item.get("title", "")).strip()


def parse_scene_pack(raw):
    """解析转换节点的 LLM 输出，返回 (prefix_lines, prompts)；失败返回 None。"""
    text = _strip_fence(raw)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    prefix = data.get("prompt_prefix") or data.get("global_prompt") or []
    if isinstance(prefix, str):
        prefix = [prefix]
    prefix = [str(x) for x in prefix if str(x).strip()]
    scenes = data.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        return None
    prompts = []
    for scene in scenes:
        if isinstance(scene, str):
            prompts.append([scene])
            continue
        if not isinstance(scene, dict):
            return None
        p = scene.get("prompt") or scene.get("prompt_lines") or []
        if isinstance(p, str):
            p = [p]
        p = [str(x) for x in p]
        if not p:
            return None
        prompts.append(p)
    return prefix, prompts
```

- [ ] **Step 4: 运行测试确认通过**

运行同上，预期：`TestAdScriptParsers` 全部 PASS。

- [ ] **Step 5: 提交**

```powershell
git add ad_script_nodes.py self_test.py
git commit -m "feat: parse scripts and scene packs, select by index"
```

---

### Task 4: LLM 规则文件

**Files:**
- Create: `comfyui-h3-prompt-builder/rules/ad_script.txt`
- Create: `comfyui-h3-prompt-builder/rules/ad_chain_plan.txt`
- Test: `comfyui-h3-prompt-builder/self_test.py`

- [ ] **Step 1: 写失败测试**

在 `self_test.py` 末尾追加：

```python
class TestAdScriptRules(unittest.TestCase):
    def test_ad_script_rules_exist(self):
        import nodes as _nodes
        for fname in ("ad_script.txt", "ad_chain_plan.txt"):
            content = _nodes.read_text_file(os.path.join(_nodes.RULES_DIR, fname))
            self.assertTrue(content and len(content) > 100, fname + " missing or too short")
```

- [ ] **Step 2: 运行测试确认失败**

运行：`& "F:\comfyui\ComfyUI-aki-v2\python\python.exe" self_test.py`
预期：失败，断言 `ad_script.txt missing or too short`。

- [ ] **Step 3: 创建 `rules/ad_script.txt`**

内容：

```text
你是短视频「反差悬疑」广告编剧。任务：根据商家信息生成 N 个中文广告剧本。

【硬性要求】
- 每个剧本 15-30 秒，四段式结构：钩子（开头 0-3 秒或按比例）→ 展开 → 反转揭晓店铺 → 广告落点（广告语必须原样出现一次）。
- 反差感：前半段（前 2/3）完全不能出现店铺名、产品、广告语，要让观众以为是悬疑/剧情内容；结尾突然揭晓是店铺广告，制造「无语感」。
- 悬疑氛围：写清人物表情、光线、音效、BGM 提示；氛围要立得住（如深夜短信、失踪调查、奇怪声响）。
- 抽象搞笑为主，但三观正常：不涉伦理擦边、不低俗、不违法、不攻击特定人群、不让人反感。
- 角色 2-3 个，中文昵称；台词总量按时长控制（约 4-5 字/秒），每段标注大致时长。
- 每个剧本内部用【第1段 0-3秒】这类段落标题，写场景/动作/对白/氛围；不写镜头与运镜（下游处理）。

【输出格式】
只输出一个严格 JSON 数组，不要任何解释或代码块标记：
[
  {"title": "剧本标题", "script": "完整剧本文本"},
  ...
]
数量必须等于用户要求的剧本数量；标题要短（≤12 字）；剧本之间要有明显差异。
```

- [ ] **Step 4: 创建 `rules/ad_chain_plan.txt`**

内容：

```text
你是 MiniMax H3 Contex-Loop 提示词工程师。任务：把中文广告剧本拆成多场英文提示词，供 H3 Chain Plan 使用。

【输入】
- 剧本原文（中文，含四段结构）
- 场次配置：共 N 场，各场时长（秒）

【输出要求】
只输出一个严格 JSON 对象，不要任何解释或代码块标记：
{
  "prompt_prefix": ["全局规则行1", "全局规则行2", ...],
  "scenes": [
    {"prompt": ["本场提示词行1", "本场提示词行2", ...]},
    ...
  ]
}

【prompt_prefix 要求】
- 全局角色定义：<Subject 1>/<Subject 2> 的外貌、服装、身份，要求跨场不变。
- 全局风格与氛围：写实、悬疑氛围、光影、镜头偏好。
- 台词规则：台词用 <d>[Chinese] 原文</d> 包裹并逐字保留。
- 若剧本用到参考图，另加 <Subject N>'s facial identity comes from <Picture N>. 这类行。

【每场 prompt 要求】
- 场数与场次配置一致；每场时长对齐配置的秒数。
- 稳定信息不重复写（放 prefix）；每场只写本场变化：地点、动作、镜头运动、氛围、BGM 提示、台词。
- 台词格式：<d>[Chinese] 中文原文</d>，逐字保留，标注说话人。
- 场景衔接：上一场结尾留一个未完成的动作，下一场开头明确承接该动作、机位、光线。
- 最后一场必须包含广告落点：店铺揭晓 + 广告语原样作为台词或旁白。
- 悬疑氛围细节：表情、光线、音效、BGM 写清楚。
- 禁止编造剧本里没有的信息；信息不足用标签或模糊指代。
```

- [ ] **Step 5: 运行测试确认通过**

运行同上，预期：`TestAdScriptRules` PASS。

- [ ] **Step 6: 提交**

```powershell
git add rules/ad_script.txt rules/ad_chain_plan.txt self_test.py
git commit -m "feat: ad script and chain plan LLM rules"
```

---

### Task 5: 广告剧本批量生成节点（`AdScriptBatch`）

**Files:**
- Modify: `comfyui-h3-prompt-builder/ad_script_nodes.py`
- Test: `comfyui-h3-prompt-builder/self_test.py`

- [ ] **Step 1: 写失败测试**

在 `self_test.py` 末尾追加：

```python
class TestAdScriptNodes(unittest.TestCase):
    def test_batch_input_types(self):
        from ad_script_nodes import AdScriptBatch
        types = AdScriptBatch.INPUT_TYPES()
        self.assertIn("business", types["required"])
        self.assertIn("ad_copy", types["required"])
        self.assertIn("script_count", types["required"])

    def test_batch_empty_input(self):
        from ad_script_nodes import AdScriptBatch
        out = AdScriptBatch().build("", "")
        self.assertIn("请输入主营业务和广告语", out[0])
        self.assertEqual(out[2], 0)

    def test_batch_mock_llm(self):
        import unittest.mock
        from ad_script_nodes import AdScriptBatch
        raw = '[{"title": "A", "script": "剧本A"}, {"title": "B", "script": "剧本B"}]'
        with unittest.mock.patch("ad_script_nodes.call_llm", return_value=raw):
            scripts_json, preview, count = AdScriptBatch().build(
                "拼豆", "拼豆团购有优惠", script_count=2, api_key="k"
            )
        self.assertEqual(count, 2)
        self.assertIn("剧本A", scripts_json)
        self.assertIn("[0]", preview)
        self.assertIn("[1]", preview)
```

- [ ] **Step 2: 运行测试确认失败**

运行：`& "F:\comfyui\ComfyUI-aki-v2\python\python.exe" self_test.py`
预期：失败，报 `ImportError: cannot import name 'AdScriptBatch'`。

- [ ] **Step 3: 实现 `AdScriptBatch`**

在 `ad_script_nodes.py` 的 `parse_scene_pack` 后追加：

```python
class AdScriptBatch:
    """广告剧本：批量生成。一次 LLM 调用产出 N 个剧本。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "business": ("STRING", {"multiline": True, "default": "例如：拼豆手工店"}),
                "ad_copy": ("STRING", {"multiline": True, "default": "拼豆团购有优惠"}),
                "script_count": (SCRIPT_COUNT_OPTIONS, {"default": 3}),
                "duration_seconds": (DURATION_OPTIONS, {"default": 30}),
            },
            "optional": {
                "shop_name": ("STRING", {"default": ""}),
                "target_audience": ("STRING", {"default": "大众"}),
                "style_tendency": (STYLE_TENDENCIES, {"default": "自动"}),
                "scale_level": (SCALE_LEVELS, {"default": "标准"}),
                "api_key": ("STRING", {"default": ""}),
                "model": ("STRING", {"default": ""}),
                "base_url": ("STRING", {"default": ""}),
                "llm_config": ("STRING", {"multiline": True, "default": ""}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "INT")
    RETURN_NAMES = ("scripts_json", "preview", "script_count")
    FUNCTION = "build"
    CATEGORY = "MiniMax H3 / 广告剧本"

    def build(self, business, ad_copy, script_count=3, duration_seconds=30, shop_name="",
              target_audience="大众", style_tendency="自动", scale_level="标准",
              api_key="", model="", base_url="", llm_config=""):
        business = (business or "").strip()
        ad_copy = (ad_copy or "").strip()
        if not business or not ad_copy:
            msg = "请输入主营业务和广告语。"
            return (msg, msg, 0)
        count = max(1, min(5, int(script_count or 3)))
        key, model_name, endpoint, temperature, config, warning = resolve_llm_config(
            api_key, model, base_url, llm_config
        )
        if not key:
            msg = "未配置 API Key：请在节点 api_key 输入框或 config.json 中填写。"
            return (msg, msg, 0)
        system = build_manju_system_prompt("ad_script.txt", "")
        user_msg = (
            "【需求】\n主营业务：" + business
            + "\n广告语：" + ad_copy
            + "\n店名：" + (shop_name or "未提供")
            + "\n目标人群：" + (target_audience or "大众")
            + "\n风格倾向：" + (style_tendency or "自动")
            + "\n尺度档位：" + (scale_level or "标准")
            + "\n目标时长：" + str(duration_seconds) + " 秒"
            + "\n剧本数量：" + str(count)
            + "\n\n请按要求输出剧本 JSON 数组。"
        )
        temp = temperature if temperature is not None else config.get("manju_temperature", 0.2)
        try:
            raw = call_llm(
                endpoint, key, model_name, system, user_msg,
                temperature=temp,
                max_tokens=config.get("max_tokens", 32768),
            )
        except Exception as exc:
            msg = "LLM 调用失败：%s" % (exc,)
            return (msg, msg, 0)
        scripts = parse_scripts(raw)
        if scripts is None:
            msg = "剧本解析失败，请重试。模型输出：%s" % raw[:300]
            return (msg, msg, 0)
        if len(scripts) > count:
            scripts = scripts[:count]
        scripts_json = json.dumps(scripts, ensure_ascii=False, indent=2)
        return (scripts_json, build_preview(scripts), len(scripts))
```

- [ ] **Step 4: 运行测试确认通过**

运行同上，预期：`TestAdScriptNodes.test_batch_*` 全部 PASS。

- [ ] **Step 5: 提交**

```powershell
git add ad_script_nodes.py self_test.py
git commit -m "feat: AdScriptBatch node generates N scripts"
```

---

### Task 6: 剧本选择节点（`AdScriptSelect`）

**Files:**
- Modify: `comfyui-h3-prompt-builder/ad_script_nodes.py`
- Test: `comfyui-h3-prompt-builder/self_test.py`

- [ ] **Step 1: 写失败测试**

在 `TestAdScriptNodes` 类内追加：

```python
    def test_select_node(self):
        from ad_script_nodes import AdScriptSelect
        out = AdScriptSelect().build('[{"title": "A", "script": "剧本A"}]', 0)
        self.assertEqual(out[0], "剧本A")
        self.assertEqual(out[1], "A")

    def test_select_node_empty(self):
        from ad_script_nodes import AdScriptSelect
        out = AdScriptSelect().build("[]", 0)
        self.assertIn("为空或序号越界", out[0])

    def test_select_node_out_of_range(self):
        from ad_script_nodes import AdScriptSelect
        out = AdScriptSelect().build('[{"title": "A", "script": "剧本A"}]', 5)
        self.assertIn("为空或序号越界", out[0])
```

- [ ] **Step 2: 运行测试确认失败**

运行：`& "F:\comfyui\ComfyUI-aki-v2\python\python.exe" self_test.py`
预期：失败，报 `ImportError: cannot import name 'AdScriptSelect'`。

- [ ] **Step 3: 实现 `AdScriptSelect`**

在 `ad_script_nodes.py` 的 `AdScriptBatch` 后追加：

```python
class AdScriptSelect:
    """广告剧本：选择。按序号从列表中取一个剧本。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "scripts_json": ("STRING", {"multiline": True, "default": "[]"}),
                "index": ("INT", {"default": 0, "min": 0, "max": 20, "step": 1}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("selected_script", "selected_title")
    FUNCTION = "build"
    CATEGORY = "MiniMax H3 / 广告剧本"

    def build(self, scripts_json, index=0):
        script, title = select_script(scripts_json, index)
        if script is None:
            msg = "剧本列表为空或序号越界，请先运行批量生成节点。"
            return (msg, "")
        return (script, title)
```

- [ ] **Step 4: 运行测试确认通过**

运行同上，预期：`TestAdScriptNodes.test_select_*` 全部 PASS。

- [ ] **Step 5: 提交**

```powershell
git add ad_script_nodes.py self_test.py
git commit -m "feat: AdScriptSelect node picks one script by index"
```

---

### Task 7: 剧本转 Chain Plan JSON 节点（`AdScriptToChainPlan`）

**Files:**
- Modify: `comfyui-h3-prompt-builder/ad_script_nodes.py`
- Test: `comfyui-h3-prompt-builder/self_test.py`

- [ ] **Step 1: 写失败测试**

在 `TestAdScriptNodes` 类内追加：

```python
    def test_chain_empty_input(self):
        from ad_script_nodes import AdScriptToChainPlan
        out = AdScriptToChainPlan().build("")
        self.assertIn("请输入剧本", out[0])

    def test_chain_mock_llm(self):
        import json as _json
        import unittest.mock
        from ad_script_nodes import AdScriptToChainPlan
        raw = (
            '{"prompt_prefix": ["P"], "scenes": '
            '[{"prompt": ["A1", "A2"]}, {"prompt": ["B"]}, {"prompt": ["C"]}]}'
        )
        with unittest.mock.patch("ad_script_nodes.call_llm", return_value=raw):
            plan, preview = AdScriptToChainPlan().build(
                "剧本", duration_seconds=30, scene_count=3, base_seed=1, api_key="k"
            )
        data = _json.loads(plan)
        self.assertEqual(len(data["shots"]), 3)
        self.assertEqual(data["shots"][0]["id"], "scene_01")
        self.assertEqual(data["shots"][0]["duration_seconds"], 10)
        self.assertEqual(data["shots"][0]["prompt"], ["A1", "A2"])
        self.assertEqual(data["shots"][1]["duration_seconds"], 10)
        self.assertEqual(data["shots"][2]["duration_seconds"], 10)
        self.assertIn("prompt_prefix", data)
        self.assertIsInstance(preview, str)

    def test_chain_wrong_scene_count(self):
        import unittest.mock
        from ad_script_nodes import AdScriptToChainPlan
        raw = '{"prompt_prefix": ["P"], "scenes": [{"prompt": ["A"]}]}'
        with unittest.mock.patch("ad_script_nodes.call_llm", return_value=raw):
            plan, preview = AdScriptToChainPlan().build(
                "剧本", duration_seconds=30, scene_count=3, base_seed=1, api_key="k"
            )
        self.assertIn("要求 3 场", plan)
```

- [ ] **Step 2: 运行测试确认失败**

运行：`& "F:\comfyui\ComfyUI-aki-v2\python\python.exe" self_test.py`
预期：失败，报 `ImportError: cannot import name 'AdScriptToChainPlan'`。

- [ ] **Step 3: 实现 `AdScriptToChainPlan`**

在 `ad_script_nodes.py` 的 `AdScriptSelect` 后追加：

```python
class AdScriptToChainPlan:
    """广告剧本：转 Chain Plan JSON。LLM 产提示词，程序组装 JSON。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "script": ("STRING", {"multiline": True, "default": "粘贴选中的剧本"}),
                "duration_seconds": (DURATION_OPTIONS, {"default": 30}),
                "scene_count": (SCENE_COUNT_OPTIONS, {"default": 3}),
            },
            "optional": {
                "base_seed": ("INT", {"default": 20260827, "min": 0, "max": 2 ** 64 - 1}),
                "api_key": ("STRING", {"default": ""}),
                "model": ("STRING", {"default": ""}),
                "base_url": ("STRING", {"default": ""}),
                "llm_config": ("STRING", {"multiline": True, "default": ""}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("plan_json", "plan_json_preview")
    FUNCTION = "build"
    CATEGORY = "MiniMax H3 / 广告剧本"

    def build(self, script, duration_seconds=30, scene_count=3, base_seed=20260827,
              api_key="", model="", base_url="", llm_config=""):
        script = (script or "").strip()
        if not script:
            msg = "请输入剧本（可接剧本选择节点）。"
            return (msg, msg)
        total = int(duration_seconds or 30)
        count = max(1, min(6, int(scene_count or 3)))
        scene_seconds = distribute_scene_seconds(total, count)
        key, model_name, endpoint, temperature, config, warning = resolve_llm_config(
            api_key, model, base_url, llm_config
        )
        if not key:
            msg = "未配置 API Key：请在节点 api_key 输入框或 config.json 中填写。"
            return (msg, msg)
        system = build_manju_system_prompt("ad_chain_plan.txt", "")
        user_msg = (
            "【剧本】\n" + script
            + "\n\n【场次配置】共 " + str(count) + " 场，各场时长（秒）："
            + json.dumps(scene_seconds, ensure_ascii=False)
            + "\n\n请把剧本拆成 " + str(count) + " 场，输出 prompt_prefix 与每场 prompt。"
        )
        temp = temperature if temperature is not None else config.get("manju_temperature", 0.2)
        try:
            raw = call_llm(
                endpoint, key, model_name, system, user_msg,
                temperature=temp,
                max_tokens=config.get("max_tokens", 32768),
            )
        except Exception as exc:
            msg = "LLM 调用失败：%s" % (exc,)
            return (msg, msg)
        pack = parse_scene_pack(raw)
        if pack is None:
            msg = "场景提示词解析失败，请重试。模型输出：%s" % raw[:300]
            return (msg, msg)
        prefix, prompts = pack
        if len(prompts) != count:
            msg = "LLM 返回 %d 场，但配置要求 %d 场，请重试。" % (len(prompts), count)
            return (msg, msg)
        try:
            plan_json = build_plan_json(prefix, prompts, scene_seconds, base_seed)
        except Exception as exc:
            msg = "plan JSON 组装失败：%s" % (exc,)
            return (msg, msg)
        pretty = json.dumps(json.loads(plan_json), ensure_ascii=False, indent=2)
        if warning:
            pretty = warning + "\n" + pretty
        return (plan_json, pretty)
```

- [ ] **Step 4: 运行测试确认通过**

运行同上，预期：`TestAdScriptNodes.test_chain_*` 全部 PASS。

- [ ] **Step 5: 提交**

```powershell
git add ad_script_nodes.py self_test.py
git commit -m "feat: AdScriptToChainPlan converts script to plan JSON"
```

---

### Task 8: 注册新节点

**Files:**
- Modify: `comfyui-h3-prompt-builder/__init__.py`
- Test: `comfyui-h3-prompt-builder/self_test.py`

- [ ] **Step 1: 写失败测试**

在 `TestAdScriptNodes` 类内追加：

```python
    def test_new_nodes_registered_in_init(self):
        init_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "__init__.py")
        with open(init_path, encoding="utf-8") as f:
            text = f.read()
        for name in ("AdScriptBatch", "AdScriptSelect", "AdScriptToChainPlan"):
            self.assertIn(name, text)
```

- [ ] **Step 2: 运行测试确认失败**

运行：`& "F:\comfyui\ComfyUI-aki-v2\python\python.exe" self_test.py`
预期：失败，断言 `AdScriptBatch` 不在 `__init__.py` 中。

- [ ] **Step 3: 修改 `__init__.py`**

将文件改为：

```python
from .ad_script_nodes import AdScriptBatch, AdScriptSelect, AdScriptToChainPlan
from .manju_nodes import (
    ManjuDirectorReview,
    ManjuImagePrompt,
    ManjuLlmConfig,
    ManjuPreset,
    ManjuResourceMapping,
    ManjuScriptToStoryboard,
    ManjuShotPrompt,
)
from .nodes import H3PromptBuilder

NODE_CLASS_MAPPINGS = {
    "H3PromptBuilder": H3PromptBuilder,
    "AdScriptBatch": AdScriptBatch,
    "AdScriptSelect": AdScriptSelect,
    "AdScriptToChainPlan": AdScriptToChainPlan,
    "ManjuDirectorReview": ManjuDirectorReview,
    "ManjuImagePrompt": ManjuImagePrompt,
    "ManjuPreset": ManjuPreset,
    "ManjuScriptToStoryboard": ManjuScriptToStoryboard,
    "ManjuResourceMapping": ManjuResourceMapping,
    "ManjuShotPrompt": ManjuShotPrompt,
    "ManjuLlmConfig": ManjuLlmConfig,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "H3PromptBuilder": "H3 Prompt Builder（提示词生成）",
    "AdScriptBatch": "广告剧本：批量生成",
    "AdScriptSelect": "广告剧本：选择",
    "AdScriptToChainPlan": "广告剧本：转 Chain Plan JSON",
    "ManjuDirectorReview": "漫剧：导演审阅",
    "ManjuImagePrompt": "漫剧：设定图提示词",
    "ManjuPreset": "漫剧预设",
    "ManjuScriptToStoryboard": "漫剧：剧本→分镜",
    "ManjuResourceMapping": "漫剧：资源映射",
    "ManjuShotPrompt": "漫剧：分镜→镜头提示词",
    "ManjuLlmConfig": "漫剧：LLM 配置",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
```

- [ ] **Step 4: 运行测试确认通过**

运行同上，预期：`test_new_nodes_registered_in_init` PASS，全部测试通过。

- [ ] **Step 5: 提交**

```powershell
git add __init__.py self_test.py
git commit -m "feat: register ad script nodes in plugin mappings"
```

---

### Task 9: README 更新

**Files:**
- Modify: `comfyui-h3-prompt-builder/README.md`

- [ ] **Step 1: 在 README 中追加「广告剧本生成」章节**

在 README 末尾追加：

```markdown
## 广告剧本生成（对接 MiniMax H3 Contex-Loop）

三个节点组成「需求 → 多剧本 → 选择 → plan JSON → 生成」链路：

1. **广告剧本：批量生成**（`AdScriptBatch`）：输入主营业务、广告语（必填），可选店名/目标人群/风格倾向/尺度档位/时长/剧本数量；一次生成 N 个「反差悬疑」广告剧本，输出剧本列表 JSON 与选择预览。
2. **广告剧本：选择**（`AdScriptSelect`）：输入剧本列表与序号，输出选中的剧本与标题。改序号重跑即可切换，上游被缓存。
3. **广告剧本：转 Chain Plan JSON**（`AdScriptToChainPlan`）：输入选中剧本、总时长（默认 30 秒）、场数（默认 3），输出可直接使用的 plan JSON（含 prompt_prefix、各场英文提示词、17k+5 帧网格时长与 seed）。

### 与 Contex-Loop 接线

- 把 `AdScriptToChainPlan.plan_json` 接到 **MiniMax H3 Chain Plan** 节点的 `plan_json_input`（optional STRING 输入口）。非空连接值会覆盖内置 plan_json，走同一套校验。
- Chain Plan 推荐设置：`run_name` 每次新 run 换名；`width/height` 512×896（竖屏广告）或 960×544；`transition_policy` Guide；`encode_mode` video；`anchor_mode` head；`audio_mode` generated_audio（H3 生成人声与 BGM）；`default_steps` 20。
- 时长提示：30 秒分 3 场按 10/10/10 分配，交付约 28.5 秒；设 32 秒则 10/10/12，交付约 30.7 秒。
- 参考图：需要人物参考图时，在转换节点输出的 prompt_prefix 中补充 `<Subject N>'s facial identity comes from <Picture N>.`，并在 workflow 中连接 Scheduled/核心 Ref2VA 引用。
```

- [ ] **Step 2: 运行全量测试**

运行：`& "F:\comfyui\ComfyUI-aki-v2\python\python.exe" self_test.py`
预期：全部 PASS（原 90 项 + 新增）。

- [ ] **Step 3: 提交**

```powershell
git add README.md
git commit -m "docs: ad script generator usage and Contex-Loop wiring"
```

---

### Task 10: 全量验证、同步部署、推送

**Files:**
- Sync: `F:\comfyui\ComfyUI-aki-v2\ComfyUI\custom_nodes\ComfyUI-H3-Prompt-Builder\`

- [ ] **Step 1: 全量自测**

运行：`& "F:\comfyui\ComfyUI-aki-v2\python\python.exe" self_test.py`
预期：`Ran N tests ... OK`（N = 原 90 + 新增约 24）。

- [ ] **Step 2: 同步部署副本**

```powershell
$src = "C:\Users\Administrator\Documents\ChatGPT\minimax h3 prompt\comfyui-h3-prompt-builder"
$dst = "F:\comfyui\ComfyUI-aki-v2\ComfyUI\custom_nodes\ComfyUI-H3-Prompt-Builder"
Copy-Item -LiteralPath (Join-Path $src "ad_script_nodes.py") -Destination (Join-Path $dst "ad_script_nodes.py") -Force
Copy-Item -LiteralPath (Join-Path $src "__init__.py") -Destination (Join-Path $dst "__init__.py") -Force
Copy-Item -LiteralPath (Join-Path $src "self_test.py") -Destination (Join-Path $dst "self_test.py") -Force
Copy-Item -LiteralPath (Join-Path $src "rules\ad_script.txt") -Destination (Join-Path $dst "rules\ad_script.txt") -Force
Copy-Item -LiteralPath (Join-Path $src "rules\ad_chain_plan.txt") -Destination (Join-Path $dst "rules\ad_chain_plan.txt") -Force
foreach ($f in @("ad_script_nodes.py","__init__.py","self_test.py","rules\ad_script.txt","rules\ad_chain_plan.txt")) {
  $ha = (Get-FileHash (Join-Path $src $f) -Algorithm MD5).Hash
  $hb = (Get-FileHash (Join-Path $dst $f) -Algorithm MD5).Hash
  "$f : $(if ($ha -eq $hb) {'已同步'} else {'不一致!'})"
}
```

预期：5 个文件全部显示「已同步」。

- [ ] **Step 3: 推送 GitHub**

```powershell
git push origin HEAD
```

预期：`master -> master` 推送成功。随后告知用户重启 ComfyUI 生效。

---

## 自审记录

- 规格覆盖：三个节点（Task 5/6/7）、两个规则文件（Task 4）、注册（Task 8）、README（Task 9）、部署与推送（Task 10）、测试（各 Task 的失败→通过步骤）。
- 类型一致性：`parse_scene_pack` 返回 `(prefix, prompts)`，Task 7 解包一致；`select_script` 返回 `(script, title)`，Task 6 一致；`build_plan_json` 参数顺序 `(prefix_lines, scene_prompts, scene_seconds, base_seed)` 在 Task 2 定义、Task 7 调用一致。
- 无占位符：所有代码步骤均含完整代码。
