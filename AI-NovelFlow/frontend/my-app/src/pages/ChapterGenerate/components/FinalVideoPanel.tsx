/**
 * FinalVideoPanel - 章节最终视频（合并后）展示面板
 * 显示章节合并后的最终视频：播放器 + 元信息 + 下载 + 刷新
 * 挂在视频生成 Tab 的右侧栏
 */
import { useCallback, useEffect, useState } from 'react';
import { RefreshCw, Download, Clock, Hash, Film, Info } from 'lucide-react';
import { chapterApi, type FinalVideoInfo } from '../../../api/chapters';

interface Props {
  novelId: string;
  chapterId?: string; // 路由参数可能为 undefined，组件内部已判空
}

function formatSize(bytes?: number | null): string {
  if (!bytes || !Number.isFinite(bytes) || bytes <= 0) return '-';
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatDuration(seconds?: number | null): string {
  if (!seconds || !Number.isFinite(seconds) || seconds <= 0) return '-';
  const total = Math.round(seconds);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

function formatTime(iso?: string | null): string {
  if (!iso) return '-';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '-';
  return d.toLocaleString('zh-CN', { hour12: false });
}

export function FinalVideoPanel({ novelId, chapterId }: Props) {
  const [info, setInfo] = useState<FinalVideoInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (silent = false) => {
    if (!novelId || !chapterId) return;
    if (!silent) setLoading(true);
    try {
      const res = await chapterApi.finalVideo(novelId, chapterId);
      if (res.success) {
        setInfo(res.data ?? null);
        setError(null);
      } else {
        setError(res.message || '获取失败');
      }
    } catch (e) {
      setError('获取最终视频信息失败');
      console.error('FinalVideoPanel load error:', e);
    } finally {
      setLoading(false);
    }
  }, [novelId, chapterId]);

  // 挂载立即加载 + 每 10 秒静默轮询（合并任务进行中时自动刷新）
  useEffect(() => {
    load(true);
    const timer = setInterval(() => load(true), 10000);
    return () => clearInterval(timer);
  }, [load]);

  const videoUrl = info?.finalVideo || info?.chapterVideoUrl || null;

  return (
    <div className="h-full flex flex-col gap-3">
      {/* 标题 + 刷新 */}
      <div className="flex items-center justify-between flex-shrink-0">
        <h3 className="text-sm font-semibold text-gray-800 flex items-center gap-1.5">
          <Film className="h-4 w-4 text-blue-600" />
          {`最终视频`}
        </h3>
        <button
          type="button"
          onClick={() => load(false)}
          disabled={loading}
          className="flex items-center gap-1 px-2 py-1 text-xs rounded-md border border-gray-200 bg-white text-gray-600 hover:bg-gray-50 disabled:opacity-50"
          title="刷新最终视频信息"
        >
          <RefreshCw className={`h-3 w-3 ${loading ? 'animate-spin' : ''}`} />
          刷新
        </button>
      </div>

      {error && (
        <div className="flex items-center gap-2 text-xs text-red-600 bg-red-50 rounded-md px-3 py-2">
          <Info className="h-3.5 w-3.5 flex-shrink-0" />
          {error}
        </div>
      )}

      {!loading && !info && !error ? (
        <div className="flex-1 min-h-0 flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-gray-300 bg-gray-50 p-6 text-center">
          <Film className="h-10 w-10 text-gray-300" />
          <div className="text-sm text-gray-500">
            暂无最终视频
            <div className="mt-1 text-xs text-gray-400">
              请先在「视频生成」页完成分镜视频生成，再选择分镜合并章节视频
            </div>
          </div>
        </div>
      ) : null}

      {videoUrl ? (
        <>
          {/* 视频播放器 */}
          <div className="flex-shrink-0 rounded-lg overflow-hidden bg-black">
            <video
              src={videoUrl}
              controls
              playsInline
              className="w-full aspect-video object-contain"
            />
          </div>

          {/* 元信息 */}
          <div className="flex-shrink-0 grid grid-cols-2 gap-2 text-xs">
            <div className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 flex items-center gap-1.5">
              <Clock className="h-3.5 w-3.5 text-gray-400" />
              <span className="text-gray-500">时长</span>
              <span className="ml-auto font-medium text-gray-800">
                {formatDuration(info?.chapterVideoDuration)}
              </span>
            </div>
            <div className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 flex items-center gap-1.5">
              <Hash className="h-3.5 w-3.5 text-gray-400" />
              <span className="text-gray-500">分镜</span>
              <span className="ml-auto font-medium text-gray-800">
                {info?.chapterVideoShotCount ?? '-'} 镜
              </span>
            </div>
            <div className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 flex items-center gap-1.5">
              <Download className="h-3.5 w-3.5 text-gray-400" />
              <span className="text-gray-500">大小</span>
              <span className="ml-auto font-medium text-gray-800">
                {formatSize(info?.chapterVideoSize)}
              </span>
            </div>
            <div className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 flex items-center gap-1.5">
              <Clock className="h-3.5 w-3.5 text-gray-400" />
              <span className="text-gray-500">生成时间</span>
              <span className="ml-auto font-medium text-gray-800">
                {formatTime(info?.chapterVideoCompletedAt)}
              </span>
            </div>
          </div>

          {/* 下载 */}
          <div className="flex-shrink-0">
            <a
              href={videoUrl}
              download
              className="w-full flex items-center justify-center gap-2 px-3 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
            >
              <Download className="h-4 w-4" />
              下载最终视频
            </a>
          </div>
        </>
      ) : null}

      {loading && info ? (
        <div className="flex-1 min-h-0 flex items-center justify-center text-xs text-gray-400">
          刷新中...
        </div>
      ) : null}
    </div>
  );
}

export default FinalVideoPanel;
