# -*- coding: utf-8 -*-
"""
阶段3：资产抽取
- 人脸检测（opencv Haar 级联，轻量）→ 人脸裁片 assets/faces/
- 感知哈希（dHash）聚类 → 角色分组（角色0/角色1/...）
- 场景资产：每个镜头的代表关键帧 assets/scenes/
- 输出 assets.json（供阶段4引用）

用法: python -m pipeline.stage3_assets <视频> [--min-face 60] [--config config.yaml]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2

from .common import LOG, ensure_dir, imread_unicode, imwrite_unicode, load_config, load_json, save_json, setup_logger


def _bbox_iou(a: tuple, b: tuple) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def detect_faces(img, min_size: int) -> list[tuple[int, int, int, int]]:
    """Haar 级联检测正脸+侧脸，跨检测器去重。"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    dets: list[tuple[int, int, int, int]] = []
    for cascade_name in ("haarcascade_frontalface_default.xml", "haarcascade_profileface.xml"):
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + cascade_name)
        for (x, y, w, h) in cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(min_size, min_size)):
            box = (x, y, w, h)
            if not any(_bbox_iou(box, m) > 0.3 for m in dets):
                dets.append(box)
    return dets


def dhash(img, size: int = 8) -> int:
    """感知哈希：比较相邻像素亮度，返回 size*size 位整数。"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    resized = cv2.resize(gray, (size + 1, size))
    diff = resized[:, 1:] > resized[:, :-1]
    bits = 0
    for i, b in enumerate(diff.flatten()):
        if b:
            bits |= 1 << i
    return bits


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def cluster_faces(records: list[dict], threshold: int) -> list[list[dict]]:
    """贪心聚类：与已有簇代表哈希距离 <= threshold 则并入，否则新建簇。"""
    clusters: list[list[dict]] = []
    for rec in records:
        best_cluster, best_dist = None, threshold + 1
        for cl in clusters:
            d = hamming(cl[0]["hash"], rec["hash"])
            if d < best_dist:
                best_cluster, best_dist = cl, d
        if best_cluster is not None and best_dist <= threshold:
            best_cluster.append(rec)
        else:
            clusters.append([rec])
    return clusters


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="阶段3 资产抽取")
    parser.add_argument("video")
    parser.add_argument("--min-face", type=int, default=None)
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args(argv)
    setup_logger()

    cfg = load_config(args.config)
    acfg = cfg["assets"]
    min_face = args.min_face if args.min_face is not None else acfg["min_face_size"]
    threshold = acfg["cluster_hamming_threshold"]

    video = Path(args.video).resolve()
    if not video.exists():
        LOG.error("视频不存在: %s", video)
        return 1
    out_root = ensure_dir(cfg["workdir"] / video.stem)
    shots_path = out_root / "shots.json"
    if not shots_path.exists():
        LOG.error("缺少 %s，请先运行阶段1", shots_path)
        return 1
    shots = load_json(shots_path)["shots"]

    faces_dir = ensure_dir(out_root / "assets" / "faces")
    scenes_dir = ensure_dir(out_root / "assets" / "scenes")
    LOG.info("=== 阶段3 资产抽取: %s（%d 镜头）===", video, len(shots))

    face_records: list[dict] = []
    for s in shots:
        kf_rel = (s.get("keyframes") or [None])[0]
        if not kf_rel:
            continue
        kf_path = out_root / kf_rel
        if not kf_path.exists():
            continue
        img = imread_unicode(kf_path)
        if img is None:
            continue
        # 场景资产：每个镜头保留代表关键帧
        scene_name = f"shot_{s['id']:03d}.jpg"
        if not (scenes_dir / scene_name).exists():
            imwrite_unicode(scenes_dir / scene_name, img)
        # 人脸资产
        faces = detect_faces(img, min_face)
        for fi, (x, y, w, h) in enumerate(faces):
            crop = img[y : y + h, x : x + w]
            fname = f"shot_{s['id']:03d}_t{s['start']:05.1f}_f{fi}.jpg"
            imwrite_unicode(faces_dir / fname, crop)
            face_records.append({"path": f"assets/faces/{fname}", "shot": s["id"], "time": s["start"], "hash": dhash(crop)})
        if faces:
            LOG.info("镜头 %d: 检出 %d 张人脸", s["id"], len(faces))

    characters: list[dict] = []
    if face_records:
        clusters = cluster_faces(face_records, threshold)
        for ci, cl in enumerate(sorted(clusters, key=len, reverse=True)):
            characters.append({
                "id": f"c{ci}",
                "name": f"角色{ci}",
                "ref_images": [r["path"] for r in cl],
                "source_shots": sorted({r["shot"] for r in cl}),
                "ref_time": cl[0]["time"],
            })
        LOG.info("人脸聚类: %d 个角色候选（阈值 %d）", len(characters), threshold)

    scenes = [{
        "id": f"s{i}",
        "shot_ids": [s["id"]],
        "ref_images": [f"assets/scenes/shot_{s['id']:03d}.jpg"],
    } for i, s in enumerate(shots)]

    assets = {
        "video": str(video),
        "characters": characters,
        "scenes": scenes,
        "props": [],  # 道具/服装自动抽取暂缺，可人工补充
    }
    save_json(assets, out_root / "assets.json")

    LOG.info("阶段3完成: %d 角色候选，%d 场景资产，产物 %s", len(characters), len(scenes), out_root / "assets.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
