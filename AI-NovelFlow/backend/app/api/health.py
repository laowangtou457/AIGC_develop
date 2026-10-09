"""
健康检查路由 - 系统状态检查相关接口
"""
from fastapi import APIRouter, HTTPException
import asyncio
import httpx
from urllib.parse import urlparse

from app.core.config import get_settings
from app.services.comfyui_monitor import get_monitor, init_monitor
from app.services.llm_service import LLMService

router = APIRouter()
settings = get_settings()

def get_gpu_monitor_host() -> str:
    """根据当前 ComfyUI 地址推导 Windows GPU 监控地址。"""
    parsed = urlparse(settings.COMFYUI_HOST)
    scheme = parsed.scheme or "http"
    hostname = parsed.hostname or "127.0.0.1"
    return f"{scheme}://{hostname}:9999"


async def get_real_gpu_stats():
    """从 Windows GPU 监控服务获取真实 GPU 和系统数据"""
    gpu_monitor_host = get_gpu_monitor_host()
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{gpu_monitor_host}/gpu-stats",
                timeout=2.0  # 短超时，快速失败
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "ok":
                    return {
                        "gpu_usage": data.get("gpu_usage", 0),
                        "temperature": data.get("temperature"),
                        "vram_used": data.get("vram_used", 0),
                        "vram_total": data.get("vram_total", 32),
                        "gpu_name": data.get("gpu_name", "NVIDIA GPU"),  # 显卡型号
                        "ram_used": data.get("ram_used"),      # 内存使用 (GB)
                        "ram_total": data.get("ram_total"),    # 内存总量 (GB)
                        "ram_percent": data.get("ram_percent"), # 内存使用率 (%)
                        "source": "real"  # 标记为真实数据
                    }
    except Exception as e:
        print(f"[GPU Monitor] 获取真实 GPU 数据失败 ({gpu_monitor_host}): {e}")
    return None


async def get_comfyui_system_stats():
    """获取 ComfyUI 状态、系统信息和队列信息"""
    system_info = {"gpu_usage": 0, "vram_used": 0, "vram_total": 16, "device_name": "Unknown GPU"}
    queue_running = 0
    queue_pending = 0

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{settings.COMFYUI_HOST}/system_stats",
            timeout=10.0
        )

        if response.status_code != 200:
            raise HTTPException(status_code=503, detail=f"ComfyUI 返回错误: {response.status_code}")

        data = response.json()
        devices = data.get("devices", [])
        if devices:
            device = devices[0]
            system_info["device_name"] = device.get("name", "Unknown GPU")

            vram_total = device.get("vram_total", 0)
            torch_vram_total = device.get("torch_vram_total", 0)

            if vram_total > 0:
                system_info["vram_total"] = round(vram_total / (1024**3), 1)
                system_info["vram_used"] = round(torch_vram_total / (1024**3), 1) if torch_vram_total > 0 else 0

        try:
            queue_response = await client.get(
                f"{settings.COMFYUI_HOST}/queue",
                timeout=3.0
            )
            if queue_response.status_code == 200:
                queue_data = queue_response.json()
                queue_running = len(queue_data.get("queue_running", []))
                queue_pending = len(queue_data.get("queue_pending", []))
        except Exception as e:
            print(f"[ComfyUI] 获取队列失败: {e}")

    return {
        "system_info": system_info,
        "queue_running": queue_running,
        "queue_pending": queue_pending,
    }


def build_system_status_response(system_info: dict, queue_running: int, queue_pending: int, gpu_source: str, temperature=None, ram_used=None, ram_total=None, ram_percent=None):
    return {
        "status": "ok",
        "message": "ComfyUI 连接正常",
        "data": {
            "device_name": system_info.get("device_name", "Unknown GPU"),
            "gpu_usage": system_info.get("gpu_usage", 0),
            "vram_used": system_info.get("vram_used", 0),
            "vram_total": system_info.get("vram_total", 16),
            "queue_running": queue_running,
            "queue_pending": queue_pending,
            "temperature": temperature,
            "gpu_source": gpu_source,
            "ram_used": ram_used,
            "ram_total": ram_total,
            "ram_percent": ram_percent,
        }
    }


@router.get("/llm")
async def check_llm():
    """检查 LLM API 连接状态（支持多厂商）——轻量探测，不发起真实对话生成"""
    from app.core.config import get_settings
    settings = get_settings()

    llm_service = LLMService()

    # 调试信息
    debug_info = {
        "provider": llm_service.provider,
        "model": llm_service.model,
        "api_url": llm_service.api_url,
        "api_key_configured": bool(llm_service.api_key),
        "proxy_enabled": llm_service.proxy_enabled,
    }

    # Ollama 和自定义 API 通常不需要 API Key
    if not llm_service.api_key and llm_service.provider not in ("ollama", "custom"):
        raise HTTPException(
            status_code=503,
            detail={
                "message": "LLM API Key 未配置",
                "debug": debug_info
            }
        )

    # Ollama：轻量探测 /api/tags（只查模型列表，不加载模型、不生成），秒回
    if llm_service.provider == "ollama":
        base = (llm_service.api_url or "").rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        tags_url = base + "/api/tags"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(tags_url, timeout=3.0)
            if resp.status_code == 200:
                return {
                    "status": "ok",
                    "message": "OLLAMA API 连接正常",
                    "provider": "ollama",
                    "model": llm_service.model,
                }
            raise HTTPException(
                status_code=503,
                detail={"message": f"Ollama 返回错误: HTTP {resp.status_code}", "debug": debug_info}
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=503,
                detail={"message": f"Ollama 连接失败: {str(e)}", "debug": debug_info}
            )

    # 其他厂商：真实对话但限制 5 秒，避免首页卡死
    try:
        result = await asyncio.wait_for(
            llm_service.chat_completion(
                system_prompt="You are a helpful assistant.",
                user_content="Hi",
                max_tokens=10
            ),
            timeout=5
        )
        if result["success"]:
            return {
                "status": "ok",
                "message": f"{llm_service.provider.upper()} API 连接正常",
                "provider": llm_service.provider,
                "model": llm_service.model
            }
        raise HTTPException(
            status_code=503,
            detail={
                "message": "LLM API 连接失败",
                "error": result.get("error", "未知错误"),
                "debug": debug_info
            }
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=503,
            detail={"message": "LLM API 连接超时（5s 限制）", "debug": debug_info}
        )
    except Exception as e:
        import traceback
        print(f"[Health Check] LLM 连接失败: {e}")
        print(traceback.format_exc())
        raise HTTPException(
            status_code=503,
            detail={
                "message": f"LLM API 连接失败: {str(e)}",
                "debug": debug_info
            }
        )

# 兼容旧接口
@router.get("/deepseek")
async def check_deepseek():
    """检查 DeepSeek API 连接状态（兼容旧接口）"""
    return await check_llm()


@router.get("/comfyui")
async def check_comfyui():
    """检查 ComfyUI 连接状态并获取系统信息 - 必须先验证 ComfyUI 连接"""
    import traceback

    try:
        print(f"[ComfyUI] 检查连接: {settings.COMFYUI_HOST}/system_stats")
        comfyui_stats = await get_comfyui_system_stats()
    except httpx.ConnectError as e:
        print(f"[ComfyUI] 连接错误: {e}")
        raise HTTPException(status_code=503, detail=f"无法连接到 ComfyUI ({settings.COMFYUI_HOST})，请确认服务是否启动")
    except HTTPException:
        raise
    except Exception as e:
        print(f"[ComfyUI] 异常: {e}")
        print(traceback.format_exc())
        raise HTTPException(status_code=503, detail=f"ComfyUI 连接失败: {str(e)}")

    real_gpu_stats = await get_real_gpu_stats()
    system_info = comfyui_stats["system_info"]
    queue_running = comfyui_stats["queue_running"]
    queue_pending = comfyui_stats["queue_pending"]

    if real_gpu_stats:
        print(f"[ComfyUI] 使用真实 GPU 数据: {real_gpu_stats['gpu_usage']}%, VRAM={real_gpu_stats['vram_used']}/{real_gpu_stats['vram_total']}GB")
        return build_system_status_response(
            {
                "device_name": real_gpu_stats.get("gpu_name", system_info["device_name"]),
                "gpu_usage": real_gpu_stats["gpu_usage"],
                "vram_used": real_gpu_stats["vram_used"],
                "vram_total": real_gpu_stats["vram_total"],
            },
            queue_running,
            queue_pending,
            "real",
            temperature=real_gpu_stats.get("temperature"),
            ram_used=real_gpu_stats.get("ram_used"),
            ram_total=real_gpu_stats.get("ram_total"),
            ram_percent=real_gpu_stats.get("ram_percent"),
        )

    monitor = get_monitor()
    if monitor:
        stats = monitor.get_stats()
        if stats["status"] == "online":
            print(f"[ComfyUI] 使用估算数据: GPU={stats['gpu_usage']}%, VRAM={stats['vram_used']}/{stats['vram_total']}GB")
            return build_system_status_response(
                {
                    "device_name": system_info.get("device_name", "NVIDIA GPU"),
                    "gpu_usage": stats["gpu_usage"],
                    "vram_used": stats["vram_used"],
                    "vram_total": stats["vram_total"],
                },
                queue_running,
                queue_pending,
                "estimated",
                temperature=stats.get("temperature"),
            )

    return build_system_status_response(system_info, queue_running, queue_pending, "comfyui")


@router.get("/system-status")
async def get_system_status():
    """按系统配置返回系统状态数据源"""
    import traceback

    try:
        comfyui_stats = await get_comfyui_system_stats()
    except httpx.ConnectError as e:
        print(f"[SystemStatus] ComfyUI 连接错误: {e}")
        raise HTTPException(status_code=503, detail=f"无法连接到 ComfyUI ({settings.COMFYUI_HOST})，请确认服务是否启动")
    except HTTPException:
        raise
    except Exception as e:
        print(f"[SystemStatus] 异常: {e}")
        print(traceback.format_exc())
        raise HTTPException(status_code=503, detail=f"系统状态获取失败: {str(e)}")

    system_info = comfyui_stats["system_info"]
    queue_running = comfyui_stats["queue_running"]
    queue_pending = comfyui_stats["queue_pending"]
    source = getattr(settings, "SYSTEM_STATUS_SOURCE", "comfyui") or "comfyui"

    if source == "windows_gpu_monitor":
        real_gpu_stats = await get_real_gpu_stats()
        if real_gpu_stats:
            return build_system_status_response(
                {
                    "device_name": real_gpu_stats.get("gpu_name", system_info["device_name"]),
                    "gpu_usage": real_gpu_stats.get("gpu_usage", 0),
                    "vram_used": real_gpu_stats.get("vram_used", 0),
                    "vram_total": real_gpu_stats.get("vram_total", system_info.get("vram_total", 16)),
                },
                queue_running,
                queue_pending,
                "windows_gpu_monitor",
                temperature=real_gpu_stats.get("temperature"),
                ram_used=real_gpu_stats.get("ram_used"),
                ram_total=real_gpu_stats.get("ram_total"),
                ram_percent=real_gpu_stats.get("ram_percent"),
            )

        return build_system_status_response(system_info, queue_running, queue_pending, "comfyui_fallback")

    return build_system_status_response(system_info, queue_running, queue_pending, "comfyui")


@router.get("/comfyui-queue")
async def get_comfyui_queue():
    """获取 ComfyUI 队列信息"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{settings.COMFYUI_HOST}/queue",
                timeout=5.0
            )
            if response.status_code == 200:
                data = response.json()
                queue_running = data.get("queue_running", [])
                queue_pending = data.get("queue_pending", [])
                return {
                    "status": "ok",
                    "queue_running": len(queue_running),
                    "queue_pending": len(queue_pending),
                    "queue_size": len(queue_running) + len(queue_pending)
                }
            else:
                return {"status": "ok", "queue_size": 0, "error": "无法获取队列信息"}
    except Exception as e:
        return {"status": "error", "queue_size": 0, "error": str(e)}


@router.get("/comfyui-test")
async def test_comfyui_connection():
    """测试 ComfyUI 连接并返回原始数据"""
    import httpx
    
    try:
        async with httpx.AsyncClient() as client:
            # 测试 system_stats
            stats_response = await client.get(
                f"{settings.COMFYUI_HOST}/system_stats",
                timeout=10.0
            )
            
            # 测试 queue
            queue_response = await client.get(
                f"{settings.COMFYUI_HOST}/queue",
                timeout=10.0
            )
            
            return {
                "status": "ok",
                "comfyui_host": settings.COMFYUI_HOST,
                "system_stats": {
                    "status_code": stats_response.status_code,
                    "data": stats_response.json() if stats_response.status_code == 200 else None
                },
                "queue": {
                    "status_code": queue_response.status_code,
                    "data": queue_response.json() if queue_response.status_code == 200 else None
                }
            }
    except Exception as e:
        return {
            "status": "error",
            "comfyui_host": settings.COMFYUI_HOST,
            "error": str(e)
        }


@router.get("/")
async def health_check():
    """基础健康检查"""
    return {"status": "ok", "service": "FXAI API"}


@router.get("/system/gpu-stats")
async def get_system_gpu_stats():
    """获取系统 GPU 状态（供前端系统状态面板使用）"""
    from app.core.config import get_settings
    settings = get_settings()
    
    try:
        # 优先尝试获取真实 GPU 数据
        real_gpu_stats = await get_real_gpu_stats()
        
        # 获取 ComfyUI 队列信息
        queue_size = 0
        try:
            async with httpx.AsyncClient() as client:
                queue_response = await client.get(
                    f"{settings.COMFYUI_HOST}/queue",
                    timeout=3.0
                )
                if queue_response.status_code == 200:
                    queue_data = queue_response.json()
                    queue_size = len(queue_data.get("queue_running", [])) + len(queue_data.get("queue_pending", []))
        except Exception:
            pass
        
        if real_gpu_stats:
            return {
                "success": True,
                "data": {
                    "status": "online",
                    "gpuUsage": real_gpu_stats.get("gpu_usage", 0),
                    "vramUsed": real_gpu_stats.get("vram_used", 0),
                    "vramTotal": real_gpu_stats.get("vram_total", 32),
                    "vramPercent": (real_gpu_stats.get("vram_used", 0) / real_gpu_stats.get("vram_total", 32)) * 100 if real_gpu_stats.get("vram_total", 32) > 0 else 0,
                    "queueSize": queue_size,
                    "temperature": real_gpu_stats.get("temperature"),
                    "gpuSource": "real",
                    "gpuName": real_gpu_stats.get("gpu_name"),
                    "ramUsed": real_gpu_stats.get("ram_used"),
                    "ramTotal": real_gpu_stats.get("ram_total"),
                    "ramPercent": real_gpu_stats.get("ram_percent")
                }
            }
        
        # 回退到监控器数据
        monitor = get_monitor()
        if monitor:
            stats = monitor.get_stats()
            if stats["status"] == "online":
                return {
                    "success": True,
                    "data": {
                        "status": "online",
                        "gpuUsage": stats.get("gpu_usage", 0),
                        "vramUsed": stats.get("vram_used", 0),
                        "vramTotal": stats.get("vram_total", 16),
                        "vramPercent": (stats.get("vram_used", 0) / stats.get("vram_total", 16)) * 100 if stats.get("vram_total", 16) > 0 else 0,
                        "queueSize": queue_size,
                        "temperature": stats.get("temperature"),
                        "gpuSource": "estimated"
                    }
                }
        
        # 无法获取数据
        return {
            "success": True,
            "data": {
                "status": "offline",
                "gpuUsage": 0,
                "vramUsed": 0,
                "vramTotal": 16,
                "vramPercent": 0,
                "queueSize": queue_size
            }
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "data": {
                "status": "offline",
                "gpuUsage": 0,
                "vramUsed": 0,
                "vramTotal": 16,
                "vramPercent": 0,
                "queueSize": 0
            }
        }


@router.get("/system/monitor")
async def get_system_monitor():
    """任务进程监控聚合接口

    聚合服务状态（ComfyUI / Ollama / GPU）+ 任务状态 + 易卡死异常检测：
    - LLM 调用 pending 超时（llm_logs 中 pending 超过 3 分钟）
    - 任务长时间停滞（tasks 中 running/pending 超过 5 分钟未更新）
    - ComfyUI / Ollama 离线
    - 显存占用超过 95%
    """
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import func as safunc
    from app.core.database import SessionLocal
    from app.models.task import Task
    from app.models.llm_log import LLMLog

    now = datetime.now()

    async def probe(url, timeout=10.0):
        """探测服务是否在线，返回 (ok, detail)"""
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(url, timeout=timeout)
                if r.status_code == 200:
                    return True, r.json()
                return False, {"http_status": r.status_code}
        except Exception as e:
            return False, {"error": str(e)}

    # ── 服务探测（并发）──
    comfy_task = asyncio.create_task(probe(f"{settings.COMFYUI_HOST}/system_stats"))
    comfy_queue_task = asyncio.create_task(probe(f"{settings.COMFYUI_HOST}/queue", timeout=10.0))
    ollama_task = asyncio.create_task(probe("http://127.0.0.1:11434/api/ps", timeout=8.0))
    comfy_ok, comfy_stats = await comfy_task
    comfy_queue_ok, comfy_queue = await comfy_queue_task
    ollama_ok, ollama_ps = await ollama_task

    comfy_status = "ok" if comfy_ok else "offline"
    ollama_status = "ok" if ollama_ok else "offline"

    queue_running = 0
    queue_pending = 0
    if comfy_queue_ok and isinstance(comfy_queue, dict):
        queue_running = len(comfy_queue.get("queue_running") or [])
        queue_pending = len(comfy_queue.get("queue_pending") or [])

    # GPU 数据（优先真实采样）
    gpu = {"gpu_usage": 0, "vram_used": 0, "vram_total": 0, "vram_percent": 0, "device_name": "Unknown", "source": "estimated"}
    if comfy_ok and isinstance(comfy_stats, dict):
        dev = (comfy_stats.get("devices") or [{}])[0]
        gpu["vram_total"] = round(dev.get("vram_total", 0) / (1024 ** 3), 1)
        gpu["vram_used"] = round((dev.get("vram_total", 0) - dev.get("vram_free", 0)) / (1024 ** 3), 1)
        gpu["device_name"] = dev.get("name", "Unknown")
        if gpu["vram_total"] > 0:
            gpu["vram_percent"] = round(gpu["vram_used"] / gpu["vram_total"] * 100, 1)
    real_gpu = await get_real_gpu_stats()
    if real_gpu:
        gpu["gpu_usage"] = real_gpu.get("gpu_usage", 0)
        gpu["source"] = "real"
        if real_gpu.get("vram_total"):
            gpu["vram_total"] = real_gpu.get("vram_total")
            gpu["vram_used"] = real_gpu.get("vram_used", 0)
            gpu["vram_percent"] = round(gpu["vram_used"] / gpu["vram_total"] * 100, 1)
        gpu["device_name"] = real_gpu.get("gpu_name", gpu["device_name"])

    # Ollama 驻留模型
    ollama_models = []
    if ollama_ok and isinstance(ollama_ps, dict):
        for m in (ollama_ps.get("models") or []):
            ollama_models.append({
                "name": m.get("name"),
                "vram_gb": round((m.get("size_vram") or 0) / (1024 ** 3), 1),
            })

    # ── 任务聚合 ──
    db = SessionLocal()
    try:
        tasks = db.query(Task).order_by(Task.created_at.desc()).limit(200).all()
        task_stats = {"total": 0, "running": 0, "pending": 0, "failed": 0, "completed": 0, "cancelled": 0}
        stalled = []
        recent = []
        for t in tasks:
            task_stats["total"] += 1
            if t.status in task_stats:
                task_stats[t.status] += 1
            # 停滞检测：running/pending 且 5 分钟未更新
            if t.status in ("running", "pending"):
                last = t.updated_at or t.created_at
                if last:
                    age_sec = (now - last.replace(tzinfo=None)).total_seconds()
                    if age_sec > 300:
                        stalled.append({
                            "id": t.id, "name": t.name, "type": t.type, "status": t.status,
                            "progress": t.progress, "current_step": t.current_step,
                            "updated_at": t.updated_at.isoformat() if t.updated_at else None,
                            "minutes_since_update": round(age_sec / 60, 1),
                        })
            if len(recent) < 12:
                recent.append({
                    "id": t.id, "name": t.name, "type": t.type, "status": t.status,
                    "progress": t.progress, "current_step": t.current_step,
                    "error_message": (t.error_message or "")[:200],
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                    "updated_at": t.updated_at.isoformat() if t.updated_at else None,
                })

        # ── LLM 调用 pending 超时检测 ──
        # 先清理僵尸 pending（超过 LLM_TIMEOUT 未完成的调用，请求早已中断），避免告警一直挂
        try:
            from app.api.llm_logs import reconcile_stale_pending_llm_logs
            reconcile_stale_pending_llm_logs(db)
        except Exception:
            pass
        llm_pending = db.query(LLMLog).filter(LLMLog.status == "pending").order_by(LLMLog.created_at.asc()).limit(20).all()
        llm_pending_timeout = []
        for log in llm_pending:
            created = log.created_at
            age_sec = (now - created.replace(tzinfo=None)).total_seconds() if created else 0
            llm_pending_timeout.append({
                "id": log.id, "task_type": log.task_type, "model": log.model,
                "created_at": created.isoformat() if created else None,
                "minutes_pending": round(age_sec / 60, 1),
                "timeout": age_sec > 600,
            })
    finally:
        db.close()

    # ── 告警汇总 ──
    alerts = []
    if comfy_status == "offline":
        alerts.append({"level": "critical", "type": "comfyui_offline", "message": "ComfyUI 无响应，生图/生视频任务会卡住"})
    if ollama_status == "offline":
        alerts.append({"level": "critical", "type": "ollama_offline", "message": "Ollama 无响应，LLM 任务会卡住"})
    if gpu["vram_percent"] >= 95:
        alerts.append({"level": "warning", "type": "vram_full", "message": f"显存占用 {gpu['vram_percent']}%，接近满载，易 OOM 死锁"})
    for s in stalled:
        alerts.append({"level": "warning", "type": "task_stalled", "message": f"任务「{s['name']}」{s['minutes_since_update']} 分钟无进展（{s['status']}）"})
    for p in llm_pending_timeout:
        if p["timeout"]:
            alerts.append({"level": "warning", "type": "llm_pending", "message": f"LLM 调用 {p['task_type'] or '未知任务'}（{p['model']}）pending {p['minutes_pending']} 分钟"})

    return {
        "success": True,
        "data": {
            "status": "ok",
            "generated_at": datetime.now().isoformat(),
            "services": {
                "comfyui": {"status": comfy_status, "queue_running": queue_running, "queue_pending": queue_pending},
                "ollama": {"status": ollama_status, "models": ollama_models},
            },
            "gpu": gpu,
            "tasks": {**task_stats, "stalled": stalled, "recent": recent},
            "llm": {"pending_count": len(llm_pending_timeout), "pending": llm_pending_timeout},
            "alerts": alerts,
        },
    }


@router.post("/system/free-vram")
async def free_vram():
    """释放显存：ComfyUI 卸载未锁定模型缓存 + Ollama 卸载非活跃模型"""
    from app.core.database import SessionLocal

    result = {"comfyui": "not_tried", "ollama": "not_tried", "details": []}

    # 1. ComfyUI /free：卸载模型缓存 + 释放内存
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{settings.COMFYUI_HOST}/free",
                json={"unload_models": True, "free_memory": True},
                timeout=30.0,
            )
            if r.status_code == 200:
                result["comfyui"] = "ok"
                result["details"].append("ComfyUI 模型缓存已释放（下次任务自动重新加载）")
            else:
                result["comfyui"] = f"http_{r.status_code}"
    except Exception as e:
        result["comfyui"] = f"error: {e}"

    # 2. Ollama：卸载所有非活跃驻留模型（keep_alive=0）
    try:
        async with httpx.AsyncClient() as client:
            ps = await client.get("http://127.0.0.1:11434/api/ps", timeout=5.0)
            if ps.status_code == 200:
                for m in (ps.json().get("models") or []):
                    name = m.get("name")
                    if name:
                        await client.post(
                            "http://127.0.0.1:11434/api/generate",
                            json={"model": name, "keep_alive": 0},
                            timeout=10.0,
                        )
                        result["details"].append(f"Ollama 已卸载驻留模型：{name}")
                result["ollama"] = "ok"
            else:
                result["ollama"] = f"http_{ps.status_code}"
    except Exception as e:
        result["ollama"] = f"error: {e}"

    # 3. 释放后显存读数
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{settings.COMFYUI_HOST}/system_stats", timeout=5.0)
            if r.status_code == 200:
                dev = (r.json().get("devices") or [{}])[0]
                free_gb = round(dev.get("vram_free", 0) / (1024 ** 3), 1)
                result["vram_free_gb"] = free_gb
                result["details"].append(f"释放后显存空闲：{free_gb}GB")
    except Exception:
        pass

    return {"success": True, "data": result}
