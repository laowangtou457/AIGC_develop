"""
全局 GPU 串行调度器

背景：
- LLM（Ollama 8b 推理）与 ComfyUI（flux 生图 / H3 视频）都独占 GPU。
- 不同 worker（shot_image / keyframe_image / shot_video / character 等）各自串行，
  但 worker 之间并行 → 8b 推理与 flux/H3 同时抢 GPU → 互相拖慢、接近 OOM。

方案：
- 全局 gpu_lock（asyncio.Lock，公平 FIFO）：所有 GPU 密集任务（LLM 推理、ComfyUI 提交）
  在同一时刻只允许一个在执行。
- AsyncWorker._run 统一包锁 → 所有 worker 任务全局串行；
- API 端点直接 await LLM/ComfyUI 的（如关键帧规划）用 gpu_serial 包锁。

注意（防死锁）：
- 持有 gpu_lock 的协程内不得 await 其他会获取 gpu_lock 的任务；
- worker 任务内部 enqueue（fire-and-forget）是安全的（不 await）。
"""
import asyncio
from typing import Any, Awaitable, Callable

# 全局互斥锁：所有 GPU 密集任务共享
gpu_lock = asyncio.Lock()


async def gpu_serial(name: str, coro: Awaitable) -> Any:
    """以全局串行方式执行一个 GPU 密集协程，返回其结果。"""
    async with gpu_lock:
        print(f"[GPUSerial] {name} 开始（独占 GPU）")
        try:
            return await coro
        finally:
            print(f"[GPUSerial] {name} 完成（释放 GPU）")


def gpu_serial_factory(name: str) -> Callable[[Callable[[], Awaitable]], Awaitable]:
    """返回一个装饰器/包装器：把任务体函数包成全局串行执行。"""
    async def _wrap(task_body: Callable[[], Awaitable]) -> None:
        await gpu_serial(name, task_body())
    return _wrap
