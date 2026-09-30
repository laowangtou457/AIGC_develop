# -*- coding: utf-8 -*-
"""
ComfyUI 节点适配层：把漫剧抽帧与属性管线包装为 ComfyUI 自定义节点。

节点清单（CATEGORY = "MiniMax H3 / 漫剧 / 抽帧与属性"）：
- ManjuSplitFrameParse   视频 → shots_json（镜头+关键帧）
- ManjuStructurize       视频 → storyboard_json（台词+画面描述）
- ManjuAssetExtract      视频 → assets_json（角色/场景资产）
- ManjuFullPipeline      一键跑 1-4 阶段（含 H3 导出开关）
- ManjuH3Export          storyboard+assets → H3 六段式提示词 + API payload（纯 JSON 进出，无文件依赖）

独立运行（无 ComfyUI）：
    python -m pipeline.comfyui_nodes --self-test

安装进 ComfyUI：
    复制 comfyui/ 目录到 ComfyUI/custom_nodes/ 下（__init__.py 已含入口映射），
    并在 ComfyUI 的 Python 环境安装本工程依赖（见 docs/02_ComfyUI集成指南.md）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import stage1_parse, stage2_structurize, stage3_assets, stage4_generate
from .common import load_config, load_json

_CATEGORY = "MiniMax H3 / 漫剧 / 抽帧与属性"


def _out_root(video: str, config: str) -> Path:
    cfg = load_config(config)
    return cfg["workdir"] / Path(video).stem


def _read(out_root: Path, name: str) -> str:
    p = out_root / name
    if not p.exists():
        raise FileNotFoundError(f"缺少 {p}，请先运行前置阶段")
    return p.read_text(encoding="utf-8")


def _run_stage(fn, video: str, config: str, extra: list[str] | None = None) -> Path:
    rc = fn([video, "--config", config] + (extra or []))
    if rc:
        raise RuntimeError(f"阶段执行失败（exit={rc}）")
    return _out_root(video, config)


class ManjuSplitFrameParse:
    """漫剧抽帧：视频 → 镜头切分 + 关键帧（阶段1）。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {"video": ("STRING", {"default": ""})},
            "optional": {"config": ("STRING", {"default": "config.yaml"})},
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("shots_json", "workdir")
    FUNCTION = "run"
    CATEGORY = _CATEGORY

    def run(self, video: str, config: str = "config.yaml"):
        if not (video or "").strip():
            raise ValueError("请填写视频路径")
        out = _run_stage(stage1_parse.main, video.strip(), config)
        return (_read(out, "shots.json"), str(out))


class ManjuStructurize:
    """漫剧结构化：ASR 台词 + VLM 画面描述 → storyboard（阶段2）。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {"video": ("STRING", {"default": ""})},
            "optional": {
                "config": ("STRING", {"default": "config.yaml"}),
                "asr_model": ("STRING", {"default": ""}),
                "vlm_provider": ("STRING", {"default": ""}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("storyboard_json", "workdir")
    FUNCTION = "run"
    CATEGORY = _CATEGORY

    def run(self, video: str, config: str = "config.yaml", asr_model: str = "", vlm_provider: str = ""):
        if not (video or "").strip():
            raise ValueError("请填写视频路径")
        extra = []
        if asr_model:
            extra += ["--asr-model", asr_model]
        if vlm_provider:
            extra += ["--vlm-provider", vlm_provider]
        out = _run_stage(stage2_structurize.main, video.strip(), config, extra)
        return (_read(out, "storyboard.json"), str(out))


class ManjuAssetExtract:
    """漫剧资产抽取：人脸/角色聚类 + 场景资产（阶段3）。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {"video": ("STRING", {"default": ""})},
            "optional": {"config": ("STRING", {"default": "config.yaml"})},
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("assets_json", "workdir")
    FUNCTION = "run"
    CATEGORY = _CATEGORY

    def run(self, video: str, config: str = "config.yaml"):
        if not (video or "").strip():
            raise ValueError("请填写视频路径")
        out = _run_stage(stage3_assets.main, video.strip(), config)
        return (_read(out, "assets.json"), str(out))


class ManjuFullPipeline:
    """漫剧全流程：视频 → shots/storyboard/assets + 提示词包（阶段1-4）。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {"video": ("STRING", {"default": ""})},
            "optional": {
                "config": ("STRING", {"default": "config.yaml"}),
                "platform": ("STRING", {"default": "minimax_h3"}),
                "asr_model": ("STRING", {"default": ""}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("shots_json", "storyboard_json", "assets_json", "prompt_pack_text", "workdir")
    FUNCTION = "run"
    CATEGORY = _CATEGORY

    def run(self, video: str, config: str = "config.yaml", platform: str = "minimax_h3", asr_model: str = ""):
        if not (video or "").strip():
            raise ValueError("请填写视频路径")
        v = video.strip()
        _run_stage(stage1_parse.main, v, config)
        extra2 = []
        if asr_model:
            extra2 += ["--asr-model", asr_model]
        _run_stage(stage2_structurize.main, v, config, extra2)
        _run_stage(stage3_assets.main, v, config)
        _run_stage(stage4_generate.main, v, config, ["--platform", platform])
        out = _out_root(v, config)
        prompt_text = ""
        if platform == "minimax_h3":
            md = out / "minimax_h3" / "prompts.md"
            if md.exists():
                prompt_text = md.read_text(encoding="utf-8")
        else:
            md = out / "prompt_pack.md"
            if md.exists():
                prompt_text = md.read_text(encoding="utf-8")
        return (
            _read(out, "shots.json"),
            _read(out, "storyboard.json"),
            _read(out, "assets.json"),
            prompt_text,
            str(out),
        )


class ManjuH3Export:
    """漫剧 H3 导出：storyboard+assets → 六段式提示词 + API payload（纯 JSON 进出）。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "storyboard_json": ("STRING", {"multiline": True, "default": "{}"}),
                "assets_json": ("STRING", {"multiline": True, "default": "{}"}),
            },
            "optional": {
                "workdir": ("STRING", {"default": ""}),
                "model": (["MiniMax-H3", "MiniMax-H3-Max"], {"default": "MiniMax-H3"}),
                "resolution": (["768P", "2K"], {"default": "768P"}),
                "ratio": (["9:16", "16:9", "4:3", "1:1", "3:4", "21:9", "adaptive"], {"default": "9:16"}),
                "mode": (["reference", "i2v"], {"default": "reference"}),
                "duration_auto": ("BOOLEAN", {"default": True}),
                "duration_seconds": ("INT", {"default": 5, "min": 4, "max": 15}),
                "audio_text_policy": (["禁字幕+无BGM", "仅禁BGM", "仅禁字幕", "保留默认"], {"default": "禁字幕+无BGM"}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("h3_prompts_md", "payloads_json", "manifest_json", "warnings")
    FUNCTION = "build"
    CATEGORY = _CATEGORY

    def build(
        self,
        storyboard_json: str,
        assets_json: str,
        workdir: str = "",
        model: str = "MiniMax-H3",
        resolution: str = "768P",
        ratio: str = "9:16",
        mode: str = "reference",
        duration_auto: bool = True,
        duration_seconds: int = 5,
        audio_text_policy: str = "禁字幕+无BGM",
    ):
        from . import h3_export

        def _parse(text: str, what: str) -> dict:
            try:
                data = json.loads((text or "").strip() or "{}")
                if not isinstance(data, dict):
                    raise ValueError("不是对象")
                return data
            except Exception as e:  # noqa: BLE001
                raise ValueError(f"{what} 不是合法 JSON: {e}") from e

        storyboard = _parse(storyboard_json, "storyboard_json")
        assets = _parse(assets_json, "assets_json")
        cfg = {
            "model": model,
            "resolution": resolution,
            "ratio": ratio,
            "mode": mode,
            "duration_auto": bool(duration_auto),
            "duration_seconds": int(duration_seconds),
            "audio_text_policy": audio_text_policy,
            "style": "写实电影质感，漫剧风格，浅景深",
        }
        pack = h3_export.build_h3_pack(storyboard, assets, cfg, mode)

        # 可选的资产校验（需要 workdir 定位图片）
        warnings = list(pack["warnings"])
        if (workdir or "").strip():
            root = Path(workdir)
            for aid, img in pack["manifest"]["references"]:
                warns = h3_export.validate_image(img, root)
                warnings += [f"{aid}: {w}" for w in warns]

        # prompts.md 文本
        lines = [
            f"# MiniMax H3 提示词包（{mode} 模式）",
            f"- 模型 {model} | 分辨率 {resolution} | 画幅 {ratio}",
            "## 资源映射",
            pack["mapping_text"],
            "## 逐镜头提示词",
        ]
        for i, (shot, prompt) in enumerate(zip(storyboard.get("shots", []), pack["prompts"])):
            lines += [f"### Shot {shot.get('id', i) + 1}", prompt]
        prompts_md = "\n\n".join(lines)

        payloads_json = json.dumps(pack["payloads"], ensure_ascii=False, indent=2)
        manifest_json = json.dumps(pack["manifest"], ensure_ascii=False, indent=2)
        warnings_text = "\n".join(warnings) or "无"
        return (prompts_md, payloads_json, manifest_json, warnings_text)


NODE_CLASS_MAPPINGS = {
    "ManjuSplitFrameParse": ManjuSplitFrameParse,
    "ManjuStructurize": ManjuStructurize,
    "ManjuAssetExtract": ManjuAssetExtract,
    "ManjuFullPipeline": ManjuFullPipeline,
    "ManjuH3Export": ManjuH3Export,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "ManjuSplitFrameParse": "漫剧抽帧·镜头切分",
    "ManjuStructurize": "漫剧结构化·台词+描述",
    "ManjuAssetExtract": "漫剧资产抽取·角色/场景",
    "ManjuFullPipeline": "漫剧全流程（1-4阶段）",
    "ManjuH3Export": "漫剧 H3 导出（六段式+payload）",
}


def _self_test() -> int:
    """无 ComfyUI 环境下的节点逻辑自检：用合成数据跑 ManjuH3Export。"""
    storyboard = {
        "shots": [
            {
                "id": 0, "start": 0.0, "end": 5.0,
                "keyframes": ["keyframes/shot_000.jpg"],
                "dialogue": [{"start": 0.5, "end": 2.0, "text": "你终于来了。"}],
                "visual": {"scene": "雨夜旧街巷", "characters": ["黑发青年，深色大衣"], "action": "撑伞转身",
                           "costume": "深色大衣", "props": ["雨伞"], "camera": "中景，缓慢推进",
                           "lighting": "冷色调，路灯逆光", "music_note": ""},
            }
        ]
    }
    assets = {
        "characters": [{"id": "c0", "name": "角色0", "ref_images": ["assets/faces/shot_000_t00.0_f0.jpg"], "source_shots": [0]}],
        "scenes": [{"id": "s0", "shot_ids": [0], "ref_images": ["assets/scenes/shot_000.jpg"]}],
        "props": [],
    }
    node = ManjuH3Export()
    prompts_md, payloads_json, manifest_json, warnings = node.build(
        json.dumps(storyboard, ensure_ascii=False),
        json.dumps(assets, ensure_ascii=False),
    )
    payloads = json.loads(payloads_json)
    assert payloads and payloads[0]["content"][0]["type"] == "text", "payload 缺少 text"
    assert payloads[0]["content"][1]["role"] == "reference_image", "reference 模式 role 错误"
    assert "subject_definitions" in prompts_md, "缺少六段式结构"
    # i2v 模式：官方 I2VA 三字段 + 首帧对齐指令 + adaptive
    prompts_md_i2v, payloads_i2v_json, _, _ = node.build(
        json.dumps(storyboard, ensure_ascii=False),
        json.dumps(assets, ensure_ascii=False),
        mode="i2v",
    )
    payloads_i2v = json.loads(payloads_i2v_json)
    assert "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced." in prompts_md_i2v, "缺少 I2VA 首帧对齐指令"
    assert "integrated_multimodal_description" in prompts_md_i2v, "缺少 I2VA 三字段"
    assert "subject_definitions" not in prompts_md_i2v, "i2v 不应出现六段式"
    assert payloads_i2v[0]["ratio"] == "adaptive", "i2v 模式 ratio 应固定 adaptive"
    assert payloads_i2v[0]["content"][1]["role"] == "first_frame", "i2v 模式 role 错误"
    print("self-test OK: 六段式结构、I2VA 三字段、payload role、资源映射均通过")
    print(prompts_md[:800])
    return 0


if __name__ == "__main__":
    sys.exit(_self_test())
