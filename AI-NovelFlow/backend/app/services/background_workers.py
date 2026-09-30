"""Small in-process async workers for serial background queues."""
import asyncio
from typing import Awaitable, Callable, Dict, Optional

from app.services.gpu_scheduler import gpu_lock


JobFactory = Callable[[], Awaitable[None]]


class AsyncWorker:
    """FIFO worker that runs one async job at a time."""

    def __init__(self, name: str):
        self.name = name
        self._queue: asyncio.Queue[JobFactory] = asyncio.Queue()
        self._runner: Optional[asyncio.Task] = None

    def enqueue(self, job_factory: JobFactory) -> None:
        self._ensure_started()
        self._queue.put_nowait(job_factory)

    def _ensure_started(self) -> None:
        if self._runner and not self._runner.done():
            return
        self._runner = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while True:
            job_factory = await self._queue.get()
            try:
                # 全局 GPU 串行：所有 worker 的任务体同一时刻只允许一个执行，
                # 避免 LLM(8b) 与 ComfyUI(flux/H3) 并行抢 GPU 导致互相拖慢/OOM。
                async with gpu_lock:
                    print(f"[Worker:{self.name}] job 开始（独占 GPU）")
                    await job_factory()
                    print(f"[Worker:{self.name}] job 完成（释放 GPU）")
            except Exception as exc:
                print(f"[Worker:{self.name}] job failed: {exc}")
            finally:
                self._queue.task_done()


class WorkerManager:
    def __init__(self):
        self._workers: Dict[str, AsyncWorker] = {}

    def worker(self, name: str) -> AsyncWorker:
        if name not in self._workers:
            self._workers[name] = AsyncWorker(name)
        return self._workers[name]


worker_manager = WorkerManager()
