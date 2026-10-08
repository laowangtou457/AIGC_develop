import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Upload, Clapperboard, Film, Layers, FileText, RefreshCw,
  Download, Loader2, CheckCircle2, XCircle, Video, User, MapPin, Sparkles, PlayCircle,
} from 'lucide-react';
import clsx from 'clsx';
import { videoAssetApi, assetFileUrl, downloadUrl } from '../../api/videoAsset';
import type { VideoAssetJob, VideoAssetJobDetail, VideoGenStatus, VideoShotProgress } from '../../api/videoAsset';

type TabKey = 'shots' | 'storyboard' | 'assets' | 'prompts' | 'video';

const VIDEO_STATUS_META: Record<string, { label: string; cls: string }> = {
  idle: { label: '未生成', cls: 'bg-gray-100 text-gray-600' },
  running: { label: '生成中', cls: 'bg-blue-100 text-blue-700' },
  success: { label: '已生成', cls: 'bg-green-100 text-green-700' },
  failed: { label: '失败', cls: 'bg-red-100 text-red-700' },
};

const SHOT_STATUS_META: Record<string, { label: string; cls: string; icon: React.ReactNode }> = {
  pending: { label: '等待', cls: 'bg-gray-100 text-gray-500', icon: <span className="h-3 w-3 rounded-full bg-gray-300" /> },
  running: { label: '生成中', cls: 'bg-blue-100 text-blue-700', icon: <Loader2 className="h-3.5 w-3.5 animate-spin" /> },
  succeeded: { label: '完成', cls: 'bg-green-100 text-green-700', icon: <CheckCircle2 className="h-3.5 w-3.5" /> },
  failed: { label: '失败', cls: 'bg-red-100 text-red-700', icon: <XCircle className="h-3.5 w-3.5" /> },
};

const STATUS_META: Record<string, { label: string; cls: string; icon: React.ReactNode }> = {
  running: { label: '运行中', cls: 'bg-blue-100 text-blue-700', icon: <Loader2 className="h-3.5 w-3.5 animate-spin" /> },
  success: { label: '成功', cls: 'bg-green-100 text-green-700', icon: <CheckCircle2 className="h-3.5 w-3.5" /> },
  failed: { label: '失败', cls: 'bg-red-100 text-red-700', icon: <XCircle className="h-3.5 w-3.5" /> },
};

function fmtTime(t: number): string {
  const m = Math.floor(t / 60);
  const s = (t % 60).toFixed(1).padStart(4, '0');
  return `${m}:${s}`;
}

export default function VideoAssetSwap() {
  const [jobs, setJobs] = useState<VideoAssetJob[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<VideoAssetJobDetail | null>(null);
  const [tab, setTab] = useState<TabKey>('shots');
  const [uploading, setUploading] = useState(false);
  const [swapping, setSwapping] = useState<string | null>(null);
  const [promptsText, setPromptsText] = useState<string>('');
  const [promptsLoading, setPromptsLoading] = useState(false);
  const [videoGen, setVideoGen] = useState<VideoGenStatus | null>(null);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);

  const refreshJobs = useCallback(async () => {
    const r = await videoAssetApi.listJobs();
    if (r.success && r.data) setJobs(r.data);
  }, []);

  const loadVideoStatus = useCallback(async (id: string) => {
    const r = await videoAssetApi.getVideoStatus(id);
    if (r.success && r.data) setVideoGen(r.data);
  }, []);

  const loadDetail = useCallback(async (id: string) => {
    const r = await videoAssetApi.getJob(id);
    if (r.success && r.data) {
      setDetail(r.data);
      // 视频生成状态同步
      if (r.data.video) {
        setVideoGen({
          video_status: r.data.video.status || 'idle',
          video_stage: r.data.video.stage,
          video_error: r.data.video.error,
          summary: r.data.video.summary,
          files: [],
        });
      }
      // 尝试加载 prompts.md 预览
      const p = r.data.file_tree.find(f => f.path === 'minimax_h3/prompts.md');
      if (p) {
        setPromptsLoading(true);
        try {
          const res = await fetch(assetFileUrl(id, p.path));
          setPromptsText(await res.text());
        } catch { setPromptsText(''); }
        setPromptsLoading(false);
      } else {
        setPromptsText('');
      }
    }
  }, []);

  // 初始加载 + 轮询（有 running 任务时 5s 刷一次；视频生成中 3s 刷一次进度）
  useEffect(() => {
    refreshJobs();
    const timer = setInterval(() => {
      refreshJobs();
      if (selectedId) {
        const j = jobs.find(x => x.id === selectedId);
        if (j && (j.status === 'running' || (j.video_status === 'running'))) loadDetail(selectedId);
        if (videoGen?.video_status === 'running') loadVideoStatus(selectedId);
      }
    }, 3000);
    return () => clearInterval(timer);
  }, [refreshJobs, loadDetail, loadVideoStatus, selectedId, jobs, videoGen?.video_status]);

  // 选中任务：首次或任务完成时拉详情
  useEffect(() => {
    if (!selectedId) { setDetail(null); setVideoGen(null); return; }
    loadDetail(selectedId);
  }, [selectedId, loadDetail]);

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    setError('');
    try {
      const r = await videoAssetApi.analyze(file);
      if (r.success && r.data?.job_id) {
        setSelectedId(r.data.job_id);
        await refreshJobs();
      } else {
        setError(r.message || '提交失败');
      }
    } catch (err) {
      setError(`提交失败: ${String(err)}`);
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  const handleSwap = async (assetType: 'character' | 'scene', assetId: string) => {
    if (!selectedId || !detail) return;
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/png,image/jpeg,image/webp';
    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file) return;
      setSwapping(assetId);
      setError('');
      try {
        const r = await videoAssetApi.swapAsset(selectedId, assetType, assetId, file);
        if (r.success) {
          await loadDetail(selectedId);
        } else {
          setError(r.message || '替换失败');
        }
      } catch (err) {
        setError(`替换失败: ${String(err)}`);
      } finally {
        setSwapping(null);
      }
    };
    input.click();
  };

  const handleGenerateVideo = async () => {
    if (!selectedId || !detail) return;
    setGenerating(true);
    setError('');
    try {
      const r = await videoAssetApi.generateVideo(selectedId);
      if (r.success) {
        await loadVideoStatus(selectedId);
        setTab('video');
      } else {
        setError(r.message || '提交失败');
      }
    } catch (err) {
      setError(`提交失败: ${String(err)}`);
    } finally {
      setGenerating(false);
    }
  };

  const selectedJob = jobs.find(j => j.id === selectedId);
  const selectedSummary = selectedJob?.summary || detail?.summary;

  return (
    <div className="space-y-6">
      {/* 页头 */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <Clapperboard className="h-7 w-7 text-primary-600" /> 视频资源替换
          </h1>
          <p className="mt-1 text-sm text-gray-500">
            上传参考成片 → 逆向出分镜/关键帧/台词/资产 → 替换为原创形象 → 导出 Seedance/Kling/Veo/即梦/MiniMax H3 提示词
          </p>
        </div>
        <button
          onClick={() => fileRef.current?.click()}
          disabled={uploading}
          className="inline-flex items-center gap-2 rounded-lg bg-primary-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-primary-700 disabled:opacity-50"
        >
          {uploading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
          {uploading ? '上传分析中…' : '上传视频并分析'}
        </button>
        <input ref={fileRef} type="file" accept="video/mp4,video/quicktime,video/x-matroska,video/webm" className="hidden" onChange={handleUpload} />
      </div>

      {error && (
        <div className="rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">{error}</div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-[360px_1fr] gap-6">
        {/* 左：任务列表 */}
        <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden self-start">
          <div className="px-4 py-3 border-b border-gray-200 flex items-center justify-between">
            <span className="text-sm font-semibold text-gray-700">历史任务</span>
            <button onClick={refreshJobs} className="text-gray-400 hover:text-primary-600" title="刷新">
              <RefreshCw className="h-4 w-4" />
            </button>
          </div>
          <div className="max-h-[70vh] overflow-y-auto divide-y divide-gray-100">
            {jobs.length === 0 && (
              <div className="px-4 py-10 text-center text-sm text-gray-400">暂无任务，上传视频开始分析</div>
            )}
            {jobs.map(j => {
              const st = STATUS_META[j.status] || STATUS_META.running;
              return (
                <button
                  key={j.id}
                  onClick={() => setSelectedId(j.id)}
                  className={clsx(
                    'w-full text-left px-4 py-3 hover:bg-gray-50 transition-colors',
                    selectedId === j.id && 'bg-primary-50',
                  )}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-medium text-gray-800 truncate">{j.video_name}</span>
                    <span className={clsx('inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium shrink-0', st.cls)}>
                      {st.icon}{st.label}
                    </span>
                  </div>
                  <div className="mt-1 text-xs text-gray-500 truncate">{j.stage || '—'}</div>
                  <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-gray-400">
                    {j.summary?.shot_count != null && <span>{j.summary.shot_count} 镜头</span>}
                    {j.summary?.character_count != null && <span>{j.summary.character_count} 角色</span>}
                    {j.summary?.scene_count != null && <span>{j.summary.scene_count} 场景</span>}
                    <span>{j.created_at}</span>
                    {j.video_status && j.video_status !== 'idle' && (
                      <span className={clsx('font-medium', j.video_status === 'running' ? 'text-blue-500' : j.video_status === 'success' ? 'text-green-600' : j.video_status === 'failed' ? 'text-red-500' : 'text-gray-400')}>
                        {VIDEO_STATUS_META[j.video_status]?.label || j.video_status}
                      </span>
                    )}
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        {/* 右：详情 */}
        <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden min-h-[60vh]">
          {!detail ? (
            <div className="flex flex-col items-center justify-center py-24 text-gray-400">
              <Video className="h-12 w-12 mb-3" />
              <div className="text-sm">选择左侧任务查看分镜、资产与提示词</div>
            </div>
          ) : (
            <>
              {/* 头部摘要 */}
              <div className="px-5 py-4 border-b border-gray-200 flex flex-wrap items-center justify-between gap-3">
                <div>
                  <div className="text-lg font-bold text-gray-900 flex items-center gap-2">
                    {detail.video_name}
                    <span className={clsx('inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium', STATUS_META[detail.status]?.cls)}>
                      {STATUS_META[detail.status]?.icon}{STATUS_META[detail.status]?.label}
                    </span>
                  </div>
                  <div className="mt-1 text-xs text-gray-500">
                    {detail.status === 'running' ? detail.stage : (
                      <>
                        时长 {fmtTime(selectedSummary?.duration || 0)} ｜
                        {selectedSummary?.shot_count ?? 0} 镜头 ｜
                        {selectedSummary?.character_count ?? 0} 角色 ｜
                        {selectedSummary?.scene_count ?? 0} 场景 ｜
                        {selectedSummary?.dialogue_count ?? 0} 条台词 ｜
                        {detail.updated_at}
                      </>
                    )}
                  </div>
                  {detail.status === 'failed' && detail.error && (
                    <div className="mt-1 text-xs text-red-600 break-all">{detail.error}</div>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  {detail.status === 'running' && (
                    <span className="text-xs text-blue-600"><Loader2 className="h-3.5 w-3.5 inline animate-spin" /> 管线运行中，约数分钟，自动刷新</span>
                  )}
                  {/* 生成新视频（替换资产后）：主动点击触发 */}
                  <button
                    onClick={handleGenerateVideo}
                    disabled={generating || detail.status !== 'success' || videoGen?.video_status === 'running' || !selectedSummary?.has_prompts}
                    title={detail.status !== 'success' ? '需先完成视频分析' : !selectedSummary?.has_prompts ? '需先完成 H3 提示词导出' : '基于替换后的资产与逐镜提示词生成新视频'}
                    className="inline-flex items-center gap-1.5 rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-primary-700 disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    {generating || videoGen?.video_status === 'running' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <PlayCircle className="h-3.5 w-3.5" />}
                    {videoGen?.video_status === 'running' ? '视频生成中…' : videoGen?.video_status === 'success' ? '重新生成新视频' : '生成新视频'}
                  </button>
                  <a
                    href={downloadUrl(detail.id)}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-semibold text-gray-700 hover:bg-gray-50"
                  >
                    <Download className="h-3.5 w-3.5" /> 下载产物 ZIP
                  </a>
                </div>
              </div>

              {/* Tabs */}
              <div className="flex items-center gap-1 border-b border-gray-200 px-3">
                {([
                  { key: 'shots', label: '分镜镜头', icon: Film },
                  { key: 'storyboard', label: '分镜表', icon: FileText },
                  { key: 'assets', label: '资产（可替换）', icon: Layers },
                  { key: 'prompts', label: 'H3 提示词', icon: Sparkles },
                  { key: 'video', label: '新视频', icon: PlayCircle },
                ] as { key: TabKey; label: string; icon: React.ComponentType<{ className?: string }> }[]).map(t => (
                  <button
                    key={t.key}
                    onClick={() => setTab(t.key)}
                    className={clsx(
                      'inline-flex items-center gap-1.5 px-4 py-3 text-sm font-medium border-b-2 -mb-px transition-colors',
                      tab === t.key ? 'border-primary-600 text-primary-700' : 'border-transparent text-gray-500 hover:text-gray-700',
                    )}
                  >
                    <t.icon className="h-4 w-4" /> {t.label}
                  </button>
                ))}
              </div>

              <div className="p-5">
                {/* ── 分镜镜头 ── */}
                {tab === 'shots' && detail.shots && (() => {
                  const shotsData = detail.shots;
                  return (
                  <div className="space-y-5">
                    {/* 时间线条 */}
                    <div className="rounded-lg bg-gray-50 border border-gray-200 p-4">
                      <div className="flex h-10 items-stretch gap-0.5 overflow-hidden rounded-md">
                        {shotsData.shots.map(s => {
                          const w = (s.end - s.start) / shotsData.duration * 100;
                          return (
                            <div
                              key={s.id}
                              title={`镜头${s.id + 1}: ${fmtTime(s.start)} - ${fmtTime(s.end)}`}
                              className="bg-primary-500 hover:bg-primary-600 cursor-pointer"
                              style={{ width: `${w}%` }}
                            />
                          );
                        })}
                      </div>
                      <div className="mt-2 flex justify-between text-[11px] text-gray-400">
                        <span>00:00</span><span>{fmtTime(detail.shots.duration)}</span>
                      </div>
                      <div className="mt-1 text-xs text-gray-500">
                        {shotsData.shots.length} 个镜头（scenedetect 切分{shotsData.scene_method ? ` · ${shotsData.scene_method}` : ''}）
                      </div>
                    </div>
                    {/* 关键帧网格 */}
                    <div>
                      <div className="mb-2 text-sm font-semibold text-gray-700">关键帧（点击查看对应镜头时间）</div>
                      <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-5 gap-3">
                        {shotsData.shots.map(s => {
                          const kf = s.keyframes?.[0];
                          return (
                            <div key={s.id} className="group relative rounded-lg overflow-hidden border border-gray-200 bg-gray-50">
                              {kf ? (
                                <img
                                  src={assetFileUrl(detail.id, kf)}
                                  alt={`镜头${s.id + 1}`}
                                  className="aspect-video w-full object-cover group-hover:scale-105 transition-transform"
                                  loading="lazy"
                                />
                              ) : (
                                <div className="aspect-video w-full flex items-center justify-center text-xs text-gray-400">无</div>
                              )}
                              <div className="absolute bottom-0 inset-x-0 bg-gradient-to-t from-black/70 to-transparent px-2 py-1 text-[10px] text-white">
                                #{s.id + 1} {fmtTime(s.start)}-{fmtTime(s.end)}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  </div>
                  );
                })()}

                {/* ── 分镜表 ── */}
                {tab === 'storyboard' && (
                  <StoryboardTable detail={detail} />
                )}

                {/* ── 资产 ── */}
                {tab === 'assets' && (
                  <div className="space-y-6">
                    <AssetSection
                      title="角色资产"
                      icon={<User className="h-4 w-4" />}
                      desc="点「替换」上传原创形象图（无版权问题），保存后自动重新导出 H3 提示词"
                      assets={detail.assets?.characters.map(c => ({ id: c.id, name: c.name, ref: c.ref_images[0], sub: `来源镜头 #${(c.source_shots || []).map(x => x + 1).join(', #') || '—'}` })) || []}
                      jobId={detail.id}
                      swapping={swapping}
                      onSwap={(id) => handleSwap('character', id)}
                      emptyText="未检出角色（Haar 人脸检测，正脸更易命中）"
                    />
                    <AssetSection
                      title="场景资产"
                      icon={<MapPin className="h-4 w-4" />}
                      desc="点「替换」上传原创场景图，保存后自动重新导出 H3 提示词"
                      assets={detail.assets?.scenes.map(s => ({ id: s.id, name: s.id, ref: s.ref_images[0], sub: `覆盖镜头 #${(s.shot_ids || []).map(x => x + 1).join(', #') || '—'}` })) || []}
                      jobId={detail.id}
                      swapping={swapping}
                      onSwap={(id) => handleSwap('scene', id)}
                      emptyText="未抽取场景"
                    />
                  </div>
                )}

                {/* ── H3 提示词 ── */}
                {tab === 'prompts' && (
                  <div className="space-y-4">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="text-sm text-gray-600">
                        <span className="font-semibold text-gray-800">minimax_h3/prompts.md</span> — 六段式逐镜提示词，
                        可直接粘贴到 <span className="font-medium">Seedance / Kling / Veo / 即梦 / MiniMax H3</span> 逐镜头提示输入
                      </div>
                      <div className="flex gap-2">
                        {detail.file_tree.filter(f => f.path.startsWith('minimax_h3/payloads/')).length > 0 && (
                          <span className="text-xs text-gray-400">
                            {detail.file_tree.filter(f => f.path.startsWith('minimax_h3/payloads/')).length} 个逐镜 payload
                          </span>
                        )}
                        <a
                          href={assetFileUrl(detail.id, 'minimax_h3/prompts.md')}
                          className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-semibold text-gray-700 hover:bg-gray-50"
                        >
                          <Download className="h-3.5 w-3.5" /> prompts.md
                        </a>
                      </div>
                    </div>
                    {promptsLoading ? (
                      <div className="py-10 text-center text-sm text-gray-400"><Loader2 className="h-5 w-5 inline animate-spin" /> 加载中…</div>
                    ) : promptsText ? (
                      <pre className="max-h-[60vh] overflow-auto rounded-lg bg-gray-900 text-gray-100 p-4 text-xs leading-relaxed whitespace-pre-wrap">
                        {promptsText}
                      </pre>
                    ) : (
                      <div className="py-10 text-center text-sm text-gray-400">
                        未找到 prompts.md{detail.status === 'running' ? '（任务运行中）' : '，或该任务未开启 minimax_h3 导出'}
                      </div>
                    )}
                    {/* payload 清单 */}
                    <div>
                      <div className="mb-2 text-sm font-semibold text-gray-700">逐镜 API payload</div>
                      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-2">
                        {detail.file_tree
                          .filter(f => f.path.startsWith('minimax_h3/payloads/'))
                          .map(f => (
                            <a
                              key={f.path}
                              href={assetFileUrl(detail.id, f.path)}
                              className="rounded-lg border border-gray-200 px-3 py-2 text-xs text-gray-700 hover:border-primary-400 hover:text-primary-700 truncate"
                              title={f.path}
                            >
                              {f.path.split('/').pop()}
                            </a>
                          ))}
                        {detail.file_tree.filter(f => f.path.startsWith('minimax_h3/payloads/')).length === 0 && (
                          <div className="col-span-full text-xs text-gray-400">无 payload 产物</div>
                        )}
                      </div>
                    </div>
                  </div>
                )}

                {/* ── 新视频（替换资产后生成） ── */}
                {tab === 'video' && (
                  <VideoTab
                    jobId={detail.id}
                    videoGen={videoGen}
                    onGenerate={handleGenerateVideo}
                    generating={generating}
                    disabled={detail.status !== 'success' || !selectedSummary?.has_prompts}
                  />
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/* ── 分镜表 ── */
function StoryboardTable({ detail }: { detail: VideoAssetJobDetail }) {
  const sb = detail.storyboard as { shots?: { id?: number; start?: number; end?: number; dialogue?: { start?: number; end?: number; text?: string }[]; visual?: Record<string, unknown> }[] } | null;
  const shots = sb?.shots || [];
  if (!shots.length) {
    return (
      <div className="py-10 text-center text-sm text-gray-400">
        分镜表为空{detail.status === 'running' ? '（任务运行中）' : '（未开启 VLM 画面描述时 visual 字段为待补充）'}
      </div>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full divide-y divide-gray-200 text-sm">
        <thead className="bg-gray-50">
          <tr>
            <th className="px-3 py-2 text-left text-xs font-semibold text-gray-500">镜号</th>
            <th className="px-3 py-2 text-left text-xs font-semibold text-gray-500">时间</th>
            <th className="px-3 py-2 text-left text-xs font-semibold text-gray-500">台词</th>
            <th className="px-3 py-2 text-left text-xs font-semibold text-gray-500">画面描述</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {shots.map((s, i) => {
            const dial = (s.dialogue || []).map(d => d.text).filter(Boolean).join(' / ');
            const visual = s.visual as { scene?: string; action?: string; characters?: unknown[]; camera?: string; lighting?: string } | null;
            const vText = [
              visual?.scene ? `场景: ${visual.scene}` : '',
              visual?.action ? `动作: ${visual.action}` : '',
              visual?.camera ? `机位: ${visual.camera}` : '',
            ].filter(Boolean).join('；');
            return (
              <tr key={i} className="hover:bg-gray-50">
                <td className="px-3 py-2 text-gray-700 font-medium whitespace-nowrap">#{i + 1}</td>
                <td className="px-3 py-2 text-gray-500 whitespace-nowrap">{fmtTime(s.start || 0)}-{fmtTime(s.end || 0)}</td>
                <td className="px-3 py-2 text-gray-700 max-w-[260px]">{dial || <span className="text-gray-300">—</span>}</td>
                <td className="px-3 py-2 text-gray-500 max-w-[420px]">{vText || <span className="text-gray-300">待补充</span>}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/* ── 资产区块（角色/场景通用） ── */
function AssetSection({
  title, icon, desc, assets, jobId, swapping, onSwap, emptyText,
}: {
  title: string;
  icon: React.ReactNode;
  desc: string;
  assets: { id: string; name: string; ref: string; sub: string }[];
  jobId: string;
  swapping: string | null;
  onSwap: (id: string) => void;
  emptyText: string;
}) {
  return (
    <div>
      <div className="flex items-center gap-2 mb-1">
        <span className="inline-flex items-center gap-1 text-sm font-semibold text-gray-800">{icon}{title}</span>
        <span className="text-xs text-gray-400">{assets.length} 个</span>
      </div>
      <p className="mb-3 text-xs text-gray-500">{desc}</p>
      {assets.length === 0 ? (
        <div className="py-8 text-center text-sm text-gray-400">{emptyText}</div>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-3">
          {assets.map(a => (
            <div key={a.id} className="rounded-lg border border-gray-200 overflow-hidden bg-gray-50 group">
              <div className="relative aspect-square bg-gray-100">
                {a.ref ? (
                  <img src={assetFileUrl(jobId, a.ref)} alt={a.name} className="h-full w-full object-cover" loading="lazy" />
                ) : (
                  <div className="h-full w-full flex items-center justify-center text-xs text-gray-400">无图</div>
                )}
                <div className="absolute top-1.5 left-1.5 rounded bg-black/60 px-1.5 py-0.5 text-[10px] text-white">{a.id}</div>
              </div>
              <div className="px-2 py-1.5">
                <div className="text-xs font-semibold text-gray-800 truncate" title={a.name}>{a.name}</div>
                <div className="text-[10px] text-gray-400 truncate">{a.sub}</div>
                <button
                  onClick={() => onSwap(a.id)}
                  disabled={swapping === `${a.id}`}
                  className="mt-1.5 w-full inline-flex items-center justify-center gap-1 rounded-md border border-primary-300 bg-primary-50 px-2 py-1 text-[11px] font-semibold text-primary-700 hover:bg-primary-100 disabled:opacity-50"
                >
                  {swapping === `${a.id}` ? <Loader2 className="h-3 w-3 animate-spin" /> : <Upload className="h-3 w-3" />}
                  替换资产
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ── 新视频页签：替换资产后生成 ── */
function VideoTab({
  jobId, videoGen, onGenerate, generating, disabled,
}: {
  jobId: string;
  videoGen: VideoGenStatus | null;
  onGenerate: () => void;
  generating: boolean;
  disabled: boolean;
}) {
  const status = videoGen?.video_status || 'idle';
  const summary = videoGen?.summary;
  const shots: VideoShotProgress[] = summary?.shots || [];
  const merged = summary?.merged;
  const finalVideo = merged?.video_path || videoGen?.files?.find(f => f.path.endsWith('/final_video.mp4') || f.path === 'new_video/final_video.mp4')?.path || null;
  const shotVideos = (videoGen?.files || []).filter(f => /^new_video\/shot_\d+\.mp4$/.test(f.path));
  const doneCount = summary?.done ?? shots.filter(s => s.status === 'succeeded').length;
  const failedCount = shots.filter(s => s.status === 'failed').length;

  return (
    <div className="space-y-5">
      {/* 流程说明 + 状态条 */}
      <div className="rounded-lg bg-primary-50/60 border border-primary-200 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="text-sm text-gray-700">
            <span className="font-semibold text-gray-900">生成新视频</span>
            <span className="mx-2 text-gray-300">→</span>
            逐镜 H3 提示词 + 替换后的资产参考图
            <span className="mx-2 text-gray-300">→</span>
            ComfyUI H3 视频工作流
            <span className="mx-2 text-gray-300">→</span>
            ffmpeg 合并成片
          </div>
          <span className={clsx('inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium', VIDEO_STATUS_META[status]?.cls)}>
            {status === 'running' && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {VIDEO_STATUS_META[status]?.label || status}
          </span>
        </div>
        {status === 'running' && (
          <div className="mt-3">
            <div className="flex items-center justify-between text-xs text-gray-500 mb-1">
              <span>{videoGen?.video_stage || '准备中…'}</span>
              <span>{doneCount}/{summary?.total || 0} 镜头完成{failedCount > 0 ? ` · ${failedCount} 失败` : ''}</span>
            </div>
            <div className="h-2 rounded-full bg-gray-200 overflow-hidden">
              <div
                className="h-full bg-primary-500 transition-all duration-500"
                style={{ width: `${summary?.total ? Math.round((doneCount / summary.total) * 100) : 0}%` }}
              />
            </div>
          </div>
        )}
        {status === 'failed' && videoGen?.video_error && (
          <div className="mt-2 text-xs text-red-600 break-all">{videoGen.video_error}</div>
        )}
      </div>

      {status === 'idle' && (
        <div className="py-14 text-center">
          <PlayCircle className="h-14 w-14 mx-auto mb-3 text-gray-300" />
          <div className="text-sm text-gray-600 font-medium">尚未生成新视频</div>
          <p className="mt-1 text-xs text-gray-400 max-w-md mx-auto">
            先完成分析并替换资产（角色/场景图），然后点击「生成新视频」：系统将用替换后的资产图作为参考，
            结合逐镜 H3 提示词逐镜生成视频，最后自动合并为成片（new_video/final_video.mp4）。
          </p>
          <button
            onClick={onGenerate}
            disabled={disabled || generating}
            className="mt-4 inline-flex items-center gap-1.5 rounded-lg bg-primary-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-primary-700 disabled:opacity-40"
          >
            {generating ? <Loader2 className="h-4 w-4 animate-spin" /> : <PlayCircle className="h-4 w-4" />}
            生成新视频
          </button>
          {disabled && (
            <div className="mt-2 text-xs text-amber-600">需先完成视频分析并导出 H3 提示词</div>
          )}
        </div>
      )}

      {/* 最终成片 */}
      {finalVideo && (
        <div className="rounded-lg border border-gray-200 overflow-hidden">
          <div className="px-4 py-3 border-b border-gray-200 flex items-center justify-between bg-gray-50">
            <span className="text-sm font-semibold text-gray-800 flex items-center gap-2">
              <Clapperboard className="h-4 w-4" /> 最终成片（合并 {doneCount || shots.length} 个镜头）
            </span>
            <a
              href={assetFileUrl(jobId, finalVideo)}
              download
              className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-semibold text-gray-700 hover:bg-gray-50"
            >
              <Download className="h-3.5 w-3.5" /> 下载成片
            </a>
          </div>
          <div className="p-4">
            <video
              src={assetFileUrl(jobId, finalVideo)}
              controls
              className="w-full rounded-lg bg-black aspect-video"
            />
          </div>
        </div>
      )}

      {/* 逐镜视频网格 */}
      {shots.length > 0 && (
        <div>
          <div className="mb-2 flex items-center justify-between">
            <span className="text-sm font-semibold text-gray-700">逐镜生成结果（{shots.length} 镜）</span>
            {shotVideos.length > 0 && (
              <span className="text-xs text-gray-400">{shotVideos.length} 个镜头视频</span>
            )}
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {shots.map(s => {
              const meta = SHOT_STATUS_META[s.status] || SHOT_STATUS_META.pending;
              const vPath = s.video_path || (s.status === 'succeeded'
                ? shotVideos.find(f => f.path === `new_video/shot_${String(s.shot_idx).padStart(3, '0')}.mp4`)?.path
                : null);
              return (
                <div key={s.index} className="rounded-lg border border-gray-200 overflow-hidden bg-gray-50">
                  <div className="relative bg-black">
                    {vPath ? (
                      <video src={assetFileUrl(jobId, vPath)} controls className="w-full aspect-video" preload="metadata" />
                    ) : (
                      <div className="aspect-video w-full flex items-center justify-center bg-gray-100">
                        <span className="text-xs text-gray-400">未生成</span>
                      </div>
                    )}
                    <span className={clsx('absolute top-1.5 left-1.5 rounded px-1.5 py-0.5 text-[10px] text-white bg-black/60')}>
                      #{s.shot_idx}
                    </span>
                  </div>
                  <div className="px-2 py-1.5">
                    <div className="flex items-center justify-between gap-1">
                      <span className={clsx('inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium', meta.cls)}>
                        {meta.icon}{meta.label}
                      </span>
                      <span className="text-[10px] text-gray-400">{s.duration}s{s.workflow ? ` · ${s.workflow.replace(/Minimax H3 /, '')}` : ''}</span>
                    </div>
                    {s.error && <div className="mt-1 text-[10px] text-red-500 break-all">{s.error}</div>}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {status === 'success' && !finalVideo && shots.length === 0 && (
        <div className="py-10 text-center text-sm text-gray-400">
          未找到新视频产物（可点击「生成新视频」重新生成）
        </div>
      )}
    </div>
  );
}
