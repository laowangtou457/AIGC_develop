/**
 * 任务进程监控页面
 *
 * 聚合展示：服务状态（ComfyUI / Ollama / GPU）+ 任务状态 + 易卡死异常告警
 * 自动刷新（5 秒），可手动刷新
 */
import { useEffect, useState, useCallback } from 'react';
import {
  Activity, Server, Cpu, MemoryStick, RefreshCw, AlertTriangle, AlertOctagon,
  CheckCircle2, Loader2, ListTodo, Clock, FileWarning, Swords,
} from 'lucide-react';
import { useTranslation } from '../../stores/i18nStore';
import { monitorApi, type SystemMonitor } from '../../api/monitor';
import { martialArtsApi, type MartialMonitorData } from '../../api/martialArts';

const statusColor: Record<string, string> = {
  completed: 'bg-green-100 text-green-700 border-green-200',
  running: 'bg-blue-100 text-blue-700 border-blue-200',
  pending: 'bg-amber-100 text-amber-700 border-amber-200',
  failed: 'bg-red-100 text-red-700 border-red-200',
  cancelled: 'bg-gray-100 text-gray-600 border-gray-200',
};

const statusLabel: Record<string, string> = {
  completed: '已完成',
  running: '运行中',
  pending: '等待中',
  failed: '失败',
  cancelled: '已取消',
};

export default function Monitor() {
  const { t } = useTranslation();
  const [data, setData] = useState<SystemMonitor | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [freeing, setFreeing] = useState(false);
  const [freeResult, setFreeResult] = useState<string | null>(null);
  const [martial, setMartial] = useState<MartialMonitorData | null>(null);

  const handleFreeVram = async () => {
    setFreeing(true);
    setFreeResult(null);
    try {
      const res = await monitorApi.freeVram();
      if (res.success && res.data) {
        const d = res.data;
        const summary = `ComfyUI: ${d.comfyui === 'ok' ? '已释放' : d.comfyui} | Ollama: ${d.ollama === 'ok' ? '已卸载' : d.ollama}`;
        setFreeResult(summary + (d.vram_free_gb != null ? ` | 空闲 ${d.vram_free_gb}GB` : ''));
      } else {
        setFreeResult('释放失败：' + (res.message || '未知错误'));
      }
    } catch (e) {
      setFreeResult('释放失败：' + String(e));
    } finally {
      setFreeing(false);
      fetchMonitor();
    }
  };

  const fetchMonitor = useCallback(async () => {
    try {
      const res = await monitorApi.fetchMonitor();
      if (res.success && res.data) {
        setData(res.data);
        setError(null);
      } else {
        setError(res.message || '获取监控数据失败');
      }
    } catch (e) {
      setError(String(e));
    }
    // 武打任务进程监控（独立接口，失败不影响主监控展示）
    try {
      const mres = await martialArtsApi.fetchMonitor();
      if (mres.success && mres.data) {
        setMartial(mres.data);
      }
    } catch {
      /* 忽略武打监控拉取失败 */
    } finally {
      setLoading(false);
      setLastRefresh(new Date());
    }
  }, []);

  useEffect(() => {
    fetchMonitor();
    const interval = setInterval(() => {
      if (autoRefresh) fetchMonitor();
    }, 5000);
    return () => clearInterval(interval);
  }, [fetchMonitor, autoRefresh]);

  const criticalCount = data?.alerts.filter((a) => a.level === 'critical').length ?? 0;
  const warningCount = data?.alerts.filter((a) => a.level === 'warning').length ?? 0;

  return (
    <div className="px-4 py-6 max-w-7xl mx-auto">
      {/* 标题栏 */}
      <div className="flex flex-wrap items-center justify-between gap-4 mb-6">
        <div className="flex items-center gap-3">
          <div className="p-2.5 bg-primary-600 rounded-xl">
            <Activity className="h-6 w-6 text-white" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-gray-900">任务进程监控</h1>
            <p className="text-sm text-gray-500 mt-0.5">
              服务状态 · 任务进度 · 卡死异常检测
              {lastRefresh && <span className="ml-2 text-gray-400">更新于 {lastRefresh.toLocaleTimeString('zh-CN', { hour12: false })}</span>}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-gray-600 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
              className="w-4 h-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
            />
            自动刷新（5秒）
          </label>
          <button
            onClick={fetchMonitor}
            className="inline-flex items-center gap-2 px-3 py-1.5 text-sm border border-gray-300 rounded-lg bg-white hover:bg-gray-50 text-gray-700"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
            刷新
          </button>
        </div>
      </div>

      {/* 告警区 */}
      {data && data.alerts.length > 0 && (
        <div className="mb-6 space-y-2">
          {data.alerts.map((alert, i) => (
            <div
              key={`${alert.type}-${i}`}
              className={`flex items-start gap-3 rounded-xl border px-4 py-3 ${
                alert.level === 'critical'
                  ? 'border-red-200 bg-red-50 text-red-800'
                  : 'border-amber-200 bg-amber-50 text-amber-800'
              }`}
            >
              {alert.level === 'critical'
                ? <AlertOctagon className="h-5 w-5 flex-shrink-0 mt-0.5" />
                : <AlertTriangle className="h-5 w-5 flex-shrink-0 mt-0.5" />}
              <span className="text-sm">{alert.message}</span>
            </div>
          ))}
        </div>
      )}

      {!data && error && (
        <div className="mb-6 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          监控数据获取失败：{error}
        </div>
      )}

      {loading && !data ? (
        <div className="flex items-center justify-center py-24">
          <Loader2 className="h-6 w-6 text-gray-400 animate-spin" />
          <span className="ml-2 text-gray-500">正在获取监控数据...</span>
        </div>
      ) : data ? (
        <div className="space-y-6">
          {/* 服务状态三卡 */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {/* ComfyUI */}
            <div className="bg-white rounded-2xl p-5 shadow-sm border border-gray-100">
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-3">
                  <div className={`p-2 rounded-lg ${data.services.comfyui.status === 'ok' ? 'bg-green-50' : 'bg-red-50'}`}>
                    <Server className={`h-5 w-5 ${data.services.comfyui.status === 'ok' ? 'text-green-600' : 'text-red-600'}`} />
                  </div>
                  <span className="font-semibold text-gray-900">ComfyUI</span>
                </div>
                <span className={`inline-flex items-center gap-1.5 text-sm font-medium ${
                  data.services.comfyui.status === 'ok' ? 'text-green-600' : 'text-red-600'
                }`}>
                  <span className={`w-2 h-2 rounded-full ${data.services.comfyui.status === 'ok' ? 'bg-green-500' : 'bg-red-500'}`} />
                  {data.services.comfyui.status === 'ok' ? '在线' : '离线'}
                </span>
              </div>
              <div className="space-y-2 text-sm">
                <div className="flex justify-between text-gray-600">
                  <span>运行中队列</span>
                  <span className="font-semibold text-gray-900">{data.services.comfyui.queue_running}</span>
                </div>
                <div className="flex justify-between text-gray-600">
                  <span>等待队列</span>
                  <span className="font-semibold text-gray-900">{data.services.comfyui.queue_pending}</span>
                </div>
              </div>
            </div>

            {/* Ollama */}
            <div className="bg-white rounded-2xl p-5 shadow-sm border border-gray-100">
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-3">
                  <div className={`p-2 rounded-lg ${data.services.ollama.status === 'ok' ? 'bg-green-50' : 'bg-red-50'}`}>
                    <Cpu className={`h-5 w-5 ${data.services.ollama.status === 'ok' ? 'text-green-600' : 'text-red-600'}`} />
                  </div>
                  <span className="font-semibold text-gray-900">Ollama</span>
                </div>
                <span className={`inline-flex items-center gap-1.5 text-sm font-medium ${
                  data.services.ollama.status === 'ok' ? 'text-green-600' : 'text-red-600'
                }`}>
                  <span className={`w-2 h-2 rounded-full ${data.services.ollama.status === 'ok' ? 'bg-green-500' : 'bg-red-500'}`} />
                  {data.services.ollama.status === 'ok' ? '在线' : '离线'}
                </span>
              </div>
              <div className="space-y-2 text-sm">
                <div className="flex justify-between text-gray-600">
                  <span>驻留模型</span>
                  <span className="font-semibold text-gray-900">
                    {data.services.ollama.models.length > 0
                      ? data.services.ollama.models.map((m) => `${m.name} (${m.vram_gb}GB)`).join('、')
                      : '无'}
                  </span>
                </div>
              </div>
            </div>

            {/* GPU */}
            <div className="bg-white rounded-2xl p-5 shadow-sm border border-gray-100">
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-3">
                  <div className="p-2 rounded-lg bg-blue-50">
                    <MemoryStick className="h-5 w-5 text-blue-600" />
                  </div>
                  <span className="font-semibold text-gray-900">GPU</span>
                  {data.gpu.source === 'real' && (
                    <span className="text-xs px-1.5 py-0.5 bg-green-100 text-green-700 rounded">实时</span>
                  )}
                </div>
                <span className="text-sm text-gray-500">{data.gpu.device_name}</span>
              </div>
              <div className="space-y-3">
                <div>
                  <div className="flex justify-between text-sm mb-1">
                    <span className="text-gray-600">GPU 使用率</span>
                    <span className={`font-semibold ${data.gpu.gpu_usage > 80 ? 'text-red-600' : 'text-gray-900'}`}>{data.gpu.gpu_usage}%</span>
                  </div>
                  <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
                    <div className={`h-full rounded-full transition-all duration-500 ${
                      data.gpu.gpu_usage > 80 ? 'bg-red-500' : data.gpu.gpu_usage > 50 ? 'bg-amber-500' : 'bg-green-500'
                    }`} style={{ width: `${Math.min(data.gpu.gpu_usage, 100)}%` }} />
                  </div>
                </div>
                <div>
                  <div className="flex justify-between text-sm mb-1">
                    <span className="text-gray-600">显存占用</span>
                    <span className={`font-semibold ${data.gpu.vram_percent > 90 ? 'text-red-600' : 'text-gray-900'}`}>
                      {data.gpu.vram_used} / {data.gpu.vram_total} GB ({data.gpu.vram_percent}%)
                    </span>
                  </div>
                  <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
                    <div className={`h-full rounded-full transition-all duration-500 ${
                      data.gpu.vram_percent > 90 ? 'bg-red-500' : data.gpu.vram_percent > 70 ? 'bg-amber-500' : 'bg-blue-500'
                    }`} style={{ width: `${Math.min(data.gpu.vram_percent, 100)}%` }} />
                  </div>
                </div>

                {/* 释放显存按钮 */}
                <button
                  onClick={handleFreeVram}
                  disabled={freeing}
                  className={`w-full inline-flex items-center justify-center gap-2 rounded-lg px-3 py-2 text-sm font-medium border transition-colors ${
                    freeing
                      ? 'bg-gray-50 text-gray-400 border-gray-200 cursor-wait'
                      : 'bg-blue-50 text-blue-700 border-blue-200 hover:bg-blue-100'
                  }`}
                >
                  <MemoryStick className="h-4 w-4" />
                  {freeing ? '释放中...' : '释放显存'}
                </button>
                {freeResult && (
                  <div className="text-xs text-gray-600 bg-gray-50 rounded-lg px-3 py-2 leading-relaxed break-all">
                    {freeResult}
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* 任务统计 */}
          <div className="bg-white rounded-2xl p-5 shadow-sm border border-gray-100">
            <div className="flex items-center gap-2 mb-4">
              <ListTodo className="h-5 w-5 text-gray-600" />
              <h2 className="font-semibold text-gray-900">任务统计</h2>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-6 gap-3">
              {[
                ['total', '总数', data.tasks.total, 'bg-gray-100 text-gray-700'],
                ['running', '运行中', data.tasks.running, 'bg-blue-100 text-blue-700'],
                ['pending', '等待中', data.tasks.pending, 'bg-amber-100 text-amber-700'],
                ['failed', '失败', data.tasks.failed, 'bg-red-100 text-red-700'],
                ['completed', '已完成', data.tasks.completed, 'bg-green-100 text-green-700'],
                ['cancelled', '已取消', data.tasks.cancelled, 'bg-gray-100 text-gray-600'],
              ].map(([key, label, value, cls]) => (
                <div key={String(key)} className={`rounded-xl p-4 ${cls}`}>
                  <div className="text-2xl font-bold">{value}</div>
                  <div className="text-sm opacity-80">{label}</div>
                </div>
              ))}
            </div>

            {/* 停滞任务 */}
            {data.tasks.stalled.length > 0 && (
              <div className="mt-5">
                <div className="flex items-center gap-2 mb-3">
                  <Clock className="h-4 w-4 text-red-500" />
                  <h3 className="text-sm font-semibold text-red-700">停滞任务（超过 5 分钟无进展）</h3>
                </div>
                <div className="space-y-2">
                  {data.tasks.stalled.map((task) => (
                    <div key={task.id} className="flex items-center justify-between gap-3 rounded-lg border border-red-100 bg-red-50 px-4 py-2.5 text-sm">
                      <div className="min-w-0">
                        <span className="font-medium text-gray-800">{task.name}</span>
                        <span className="ml-2 text-xs text-gray-500">{task.type}</span>
                      </div>
                      <span className="flex-shrink-0 font-semibold text-red-600">{task.minutes_since_update} 分钟未更新</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* 最近任务 */}
          <div className="bg-white rounded-2xl p-5 shadow-sm border border-gray-100">
            <div className="flex items-center gap-2 mb-4">
              <FileWarning className="h-5 w-5 text-gray-600" />
              <h2 className="font-semibold text-gray-900">最近任务</h2>
              <span className="text-xs text-gray-400">（最多 12 条）</span>
            </div>
            <div className="overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead>
                  <tr className="text-left text-gray-500 border-b border-gray-200">
                    <th className="pb-2 pr-4 font-medium">任务</th>
                    <th className="pb-2 pr-4 font-medium">类型</th>
                    <th className="pb-2 pr-4 font-medium">状态</th>
                    <th className="pb-2 pr-4 font-medium">进度</th>
                    <th className="pb-2 font-medium">当前步骤 / 错误</th>
                  </tr>
                </thead>
                <tbody>
                  {data.tasks.recent.map((task) => (
                    <tr key={task.id} className="border-b border-gray-50">
                      <td className="py-2 pr-4 font-medium text-gray-800">{task.name}</td>
                      <td className="py-2 pr-4 text-gray-500">{task.type}</td>
                      <td className="py-2 pr-4">
                        <span className={`inline-flex rounded-full border px-2 py-0.5 text-xs font-medium ${statusColor[task.status] || 'bg-gray-100 text-gray-600'}`}>
                          {statusLabel[task.status] || task.status}
                        </span>
                      </td>
                      <td className="py-2 pr-4 text-gray-600">{task.progress ?? 0}%</td>
                      <td className="py-2 max-w-xs truncate text-gray-500" title={task.error_message || task.current_step || ''}>
                        {task.error_message || task.current_step || '-'}
                      </td>
                    </tr>
                  ))}
                  {data.tasks.recent.length === 0 && (
                    <tr><td colSpan={5} className="py-6 text-center text-gray-400">暂无任务</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {/* LLM 调用状态 */}
          <div className="bg-white rounded-2xl p-5 shadow-sm border border-gray-100">
            <div className="flex items-center gap-2 mb-4">
              <CheckCircle2 className="h-5 w-5 text-gray-600" />
              <h2 className="font-semibold text-gray-900">LLM 调用状态</h2>
              <span className={`text-xs font-medium rounded-full px-2 py-0.5 ${
                data.llm.pending_count > 0 ? 'bg-amber-100 text-amber-700' : 'bg-green-100 text-green-700'
              }`}>
                {data.llm.pending_count} 个 pending
              </span>
            </div>
            {data.llm.pending.length > 0 ? (
              <div className="space-y-2">
                {data.llm.pending.map((p) => (
                  <div key={p.id} className={`flex items-center justify-between gap-3 rounded-lg border px-4 py-2.5 text-sm ${
                    p.timeout ? 'border-red-100 bg-red-50' : 'border-gray-100 bg-gray-50'
                  }`}>
                    <div className="min-w-0">
                      <span className="font-medium text-gray-800">{p.task_type || '未知任务'}</span>
                      <span className="ml-2 text-xs text-gray-500">{p.model}</span>
                    </div>
                    <span className={`flex-shrink-0 font-semibold ${p.timeout ? 'text-red-600' : 'text-gray-600'}`}>
                      {p.minutes_pending} 分钟
                      {p.timeout && '（超时）'}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="flex items-center gap-2 py-3 text-sm text-green-600">
                <CheckCircle2 className="h-4 w-4" />
                无 pending 调用，LLM 状态正常
              </div>
            )}
          </div>

          {/* 武打任务（独立工作流监控） */}
          <div className="bg-white rounded-2xl p-5 shadow-sm border border-gray-100">
            <div className="flex items-center gap-2 mb-4">
              <Swords className="h-5 w-5 text-gray-600" />
              <h2 className="font-semibold text-gray-900">武打任务</h2>
              <span className={`text-xs font-medium rounded-full px-2 py-0.5 ${
                (martial?.running.length ?? 0) > 0 ? 'bg-amber-100 text-amber-700' : 'bg-green-100 text-green-700'
              }`}>
                {(martial?.running.length ?? 0)} 个运行中
              </span>
            </div>
            {!martial ? (
              <div className="py-3 text-sm text-gray-400">武打监控数据暂不可用</div>
            ) : (
              <>
                {/* 运行中 */}
                {martial.running.length > 0 && (
                  <div className="space-y-2">
                    {martial.running.map((t) => (
                      <div key={t.id} className={`flex items-center justify-between gap-3 rounded-lg border px-4 py-2.5 text-sm ${
                        t.minutes > 10 ? 'border-red-100 bg-red-50' : 'border-blue-100 bg-blue-50'
                      }`}>
                        <div className="min-w-0">
                          <span className="font-medium text-gray-800">{t.name}</span>
                          <span className="ml-2 text-xs text-blue-600">{t.stage}</span>
                          {t.detail && <span className="ml-2 text-xs text-gray-400">{t.detail}</span>}
                        </div>
                        <span className={`flex-shrink-0 font-semibold ${t.minutes > 10 ? 'text-red-600' : 'text-blue-600'}`}>
                          {t.minutes} 分钟
                          {t.minutes > 10 && '（疑似卡死）'}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
                {/* 最近完成/失败 */}
                {martial.recent.length > 0 && (
                  <div className="mt-3">
                    <h3 className="mb-2 text-sm font-semibold text-gray-500">最近完成 / 失败（最多 20 条）</h3>
                    <div className="space-y-2">
                      {martial.recent.map((t) => (
                        <div key={t.id} className={`flex items-center justify-between gap-3 rounded-lg border px-4 py-2 text-sm ${
                          t.status === 'failed' ? 'border-red-100 bg-red-50' : 'border-gray-100 bg-gray-50'
                        }`}>
                          <div className="min-w-0">
                            <span className="font-medium text-gray-800">{t.name}</span>
                            <span className="ml-2 text-xs text-gray-500">{t.stage}</span>
                            {t.error && <span className="ml-2 text-xs text-red-600">{t.error}</span>}
                          </div>
                          <span className={`flex-shrink-0 text-xs font-medium ${
                            t.status === 'failed' ? 'text-red-600' : 'text-gray-500'
                          }`}>
                            {t.status === 'failed' ? '失败' : '完成'} · {t.elapsed_sec != null ? `${t.elapsed_sec}s` : '-'}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                {martial.running.length === 0 && martial.recent.length === 0 && (
                  <div className="py-3 text-sm text-gray-400">暂无武打任务记录</div>
                )}
              </>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}
