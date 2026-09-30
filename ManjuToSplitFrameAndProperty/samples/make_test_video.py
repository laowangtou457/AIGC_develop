# -*- coding: utf-8 -*-
"""生成 12 秒合成测试视频（三段不同画面 = 3 个镜头 + 440Hz 音轨），用于管线冒烟测试。"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.common import find_ffmpeg  # noqa: E402


def main() -> int:
    ffmpeg = find_ffmpeg()
    out = Path(__file__).resolve().parent / "sample_12s.mp4"
    tmp = out.with_suffix(".tmp.mp4")
    clips = []
    srcs = [
        "testsrc2=size=640x360:rate=25:duration=4",
        "smptebars=size=640x360:rate=25:duration=4",
        "gradients=size=640x360:rate=25:duration=4",
    ]
    for i, src in enumerate(srcs):
        clip = out.parent / f"_clip{i}.mp4"
        r = subprocess.run(
            [ffmpeg, "-y", "-f", "lavfi", "-i", src, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)],
            check=False, capture_output=True,
        )
        if r.returncode != 0:
            print(r.stderr.decode("utf-8", "replace")[-600:])
            raise SystemExit(f"clip{i} failed: {r.returncode}")
        clips.append(clip)
    listfile = out.parent / "_list.txt"
    listfile.write_text("\n".join(f"file '{c.as_posix()}'" for c in clips), encoding="utf-8")
    subprocess.run(
        [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(listfile), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(tmp)],
        check=True, capture_output=True,
    )
    subprocess.run(
        [ffmpeg, "-y", "-i", str(tmp), "-f", "lavfi", "-i", "sine=frequency=440:duration=12",
         "-c:v", "copy", "-c:a", "aac", "-shortest", str(out)],
        check=True, capture_output=True,
    )
    for c in clips:
        c.unlink(missing_ok=True)
    listfile.unlink(missing_ok=True)
    tmp.unlink(missing_ok=True)
    print(f"test video: {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
