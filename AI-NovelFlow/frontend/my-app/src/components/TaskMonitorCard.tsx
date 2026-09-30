/**
 * TaskMonitorCard - 生成界面右侧栏的"任务进程监控"入口卡片
 *
 * 5 秒轮询监控聚合接口，展示：
 * - ComfyUI / Ollama 在线状态
 * - GPU 显存占用
 * - 任务运行数 / 队列数
 * - 异常告警数（critical 红 / warning 黄）
 *
 * 点击卡片跳转到完整监控页 /monitor
 */
import { useEffect, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Activity, AlertTriangle, AlertOctagon, ChevronRight, Server, Cpu } from 'lucide-react';
import { monitorApi, type SystemMonitor } from '../api/monitor';

export default function TaskMonitorCard() {
  const navigate = useNavigate();
  const [data, setData] = useState<SystemMonitor | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchMonitor = useCallback(async () => {
    try {
      const res = await monitorApi.fetchMonitor();
      if (res.success && res.data) setData(res.data);
    } catch {
      // 静默失败，保持上一次数据
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchMonitor();
    const interval = setInterval(fetchMonitor, 5000);
    return () => clearInterval(interval);
  }, [fetchMonitor]);

  const criticalCount = data?.alerts.filter((a) => a.level === 'critical').length ?? 0;
  const warningCount = data?.alerts.filter((a) => a.level === 'warning').length ?? 0;
  const runningTasks = data?.tasks.running ?? 0;
  const pendingTasks = data?.tasks.pending ?? 0;
  const queueSize = (data?.services.comfyui.queue_running ?? 0) + (data?.services.comfyui.queue_pending ?? 0);
  const vramPercent = data?.gpu.vram_percent ?? 0;

  return (
    <button
      onClick={() => navigate('/monitor')}
      className="w-full bg-white rounded-2xl p-4 shadow-sm border border-gray-100 hover:border-primary-300 hover:shadow-md transition-all text-left"
      title="点击进入任务进程监控页"
    >
      {/* 标题行 */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <div className={`p-1.5 rounded-lg ${loading ? 'bg-gray-50' : 'bg-primary-50'}`}>
            <Activity className={`h-4 w-4 ${loading ? 'text-gray-400' : 'text-primary-600'}`} />
          </div>
          <span className="text-sm font-semibold text-gray-900">任务进程监控</span>
        </div>
        <ChevronRight className="h-4 w-4 text-gray-400" />
      </div>

      {/* 服务状态 */}
      <div className="grid grid-cols-2 gap-2 mb-3">
        <div className="flex items-center gap-2 rounded-lg bg-gray-50 px-2.5 py-1.5">
          <Server className="h-3.5 w-3.5 text-gray-500" />
          <span className="text-xs text-gray-600">ComfyUI</span>
          <span className={`ml-auto w-2 h-2 rounded-full ${data?.services.comfyui.status === 'ok' ? 'bg-green-500' : 'bg-red-500'}`} />
        </div>
        <div className="flex items-center gap-2 rounded-lg bg-gray-50 px-2.5 py-1.5">
          <Cpu className="h-3.5 w-3.5 text-gray-500" />
          <span className="text-xs text-gray-600">Ollama</span>
          <span className={`ml-auto w-2 h-2 rounded-full ${data?.services.ollama.status === 'ok' ? 'bg-green-500' : 'bg-red-500'}`} />
        </div>
      </div>

      {/* 指标行 */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3 text-xs text-gray-600">
          <span>运行 <span className="font-semibold text-blue-600">{runningTasks}</span></span>
          <span>等待 <span className="font-semibold text-amber-600">{pendingTasks}</span></span>
          <span>队列 <span className="font-semibold text-gray-800">{queueSize}</span></span>
          <span>显存 <span className={`font-semibold ${vramPercent > 90 ? 'text-red-600' : 'text-gray-800'}`}>{vramPercent}%</span></span>
        </div>
        {(criticalCount > 0 || warningCount > 0) ? (
          <div className="flex items-center gap-1.5">
            {criticalCount > 0 && (
              <span className="inline-flex items-center gap-1 rounded-full bg-red-100 px-2 py-0.5 text-xs font-medium text-red-700">
                <AlertOctagon className="h-3 w-3" />
                {criticalCount}
              </span>
            )}
            {warningCount > 0 && (
              <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-700">
                <AlertTriangle className="h-3 w-3" />
                {warningCount}
              </span>
            )}
          </div>
        ) : (
          <span className="inline-flex items-center gap-1 rounded-full bg-green-50 px-2 py-0.5 text-xs font-medium text-green-600">
            正常
          </span>
        )}
      </div>
    </button>
  );
}
