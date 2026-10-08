import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Wand2, Upload, FileText, RefreshCw, Loader2, CheckCircle2, XCircle,
  Copy, Download, ChevronRight, PlayCircle, Layers, Users, MapPin, Clock,
} from 'lucide-react';
import clsx from 'clsx';
import {
  listPromptReforgeTasks, getPromptReforgeTask, createPromptReforgeTask,
  listDirectorModels, reforgeFileUrl,
} from '../../api/promptReforge';
import type { PromptReforgeTask, PromptReforgeDetail, DirectorModel } from '../../api/promptReforge';

const STATUS_META: Record<string, { label: string; cls: string; icon: React.ReactNode }> = {
  running: { label: '重构中', cls: 'bg-blue-100 text-blue-700', icon: <Loader2 className="h-3.5 w-3.5 animate-spin" /> },
  success: { label: '已完成', cls: 'bg-green-100 text-green-700', icon: <CheckCircle2 className="h-3.5 w-3.5" /> },
  failed: { label: '失败', cls: 'bg-red-100 text-red-700', icon: <XCircle className="h-3.5 w-3.5" /> },
};

const PLATFORM_OPTIONS = [
  { key: 'minimax_h3', label: 'MiniMax H3' },
  { key: 'seedance', label: 'Seedance' },
  { key: 'kling', label: 'Kling' },
  { key: 'veo', label: 'Veo' },
  { key: 'jimeng', label: '即梦' },
];

const INPUT_TYPES = [
  { key: 'text', label: '提示词 / 剧本 / 小说（直接输入）' },
  { key: 'file', label: '上传文件（txt/md/docx/srt…）' },
];

export default function PromptReforge() {
  const [tasks, setTasks] = useState<PromptReforgeTask[]>([]);
  const [models, setModels] = useState<DirectorModel[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<PromptReforgeDetail | null>(null);

  // 新建表单
  const [showForm, setShowForm] = useState(false);
  const [title, setTitle] = useState('');
  const [inputType, setInputType] = useState('text');
  const [inputText, setInputText] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [directorModel, setDirectorModel] = useState('h3_ref2va');
  const [platforms, setPlatforms] = useState<string[]>(['minimax_h3', 'seedance', 'kling', 'veo', 'jimeng']);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  // 详情展示
  const [activeTab, setActiveTab] = useState<'input' | 'beats' | 'prompts' | 'files'>('prompts');
  const [activePlatform, setActivePlatform] = useState('minimax_h3');
  const [copied, setCopied] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const refreshTasks = useCallback(async () => {
    try {
      const r = await listPromptReforgeTasks();
      setTasks(r);
    } catch (e) {
      console.error('刷新任务列表失败', e);
    }
  }, []);

  const loadDetail = useCallback(async (id: string) => {
    try {
      const d = await getPromptReforgeTask(id);
      setDetail(d);
      if (d.report?.platforms?.length) setActivePlatform(d.report.platforms[0]);
    } catch (e) {
      console.error('加载任务详情失败', e);
    }
  }, []);

  useEffect(() => {
    refreshTasks();
    listDirectorModels().then((r) => setModels(r)).catch(() => {});
  }, [refreshTasks]);

  // 轮询：运行中任务刷新列表，选中任务刷新详情
  useEffect(() => {
    const timer = setInterval(async () => {
      await refreshTasks();
      if (selectedId) await loadDetail(selectedId);
    }, 3000);
    return () => clearInterval(timer);
  }, [selectedId, refreshTasks, loadDetail]);

  const selectTask = useCallback((id: string) => {
    setSelectedId(id);
    setActiveTab('prompts');
    loadDetail(id);
  }, [loadDetail]);

  const togglePlatform = (key: string) => {
    setPlatforms((prev) => (prev.includes(key) ? prev.filter((p) => p !== key) : [...prev, key]));
  };

  const handleSubmit = async () => {
    setError('');
    if (inputType === 'file' && !file) {
      setError('请选择要上传的文件');
      return;
    }
    if (inputType === 'text' && !inputText.trim()) {
      setError('请输入需要重构的提示词 / 剧本 / 小说');
      return;
    }
    setSubmitting(true);
    try {
      const r = await createPromptReforgeTask({
        title: title || undefined,
        input_type: inputType,
        director_model: directorModel,
        target_platforms: platforms.join(','),
        input_text: inputType === 'text' ? inputText : undefined,
        file: inputType === 'file' ? file ?? undefined : undefined,
      });
      await refreshTasks();
      setShowForm(false);
      setTitle('');
      setInputText('');
      setFile(null);
      selectTask(r.task_id);
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || '提交失败，请检查后端服务');
    } finally {
      setSubmitting(false);
    }
  };

  const copyText = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // 剪贴板不可用时提示手动复制
      window.prompt('请手动复制：', text);
    }
  };

  const platformParts: Record<string, string> = detail?.report?.platform_parts || {};
  const currentPlatformText = platformParts[activePlatform] || detail?.output_md || '';

  const renderBeats = () => {
    const beats = detail?.report?.beats;
    if (!beats) return <p className="text-sm text-gray-500">暂无提取结果。</p>;
    return (
      <div className="space-y-4">
        {beats.title && <p className="text-sm text-gray-700 font-medium">标题：{beats.title}</p>}
        {beats.style && <p className="text-sm text-gray-600">风格：{beats.style}</p>}
        {!!beats.characters?.length && (
          <div>
            <p className="mb-1 text-sm font-medium flex items-center gap-1"><Users className="h-4 w-4" /> 角色（{beats.characters.length}）</p>
            <div className="grid grid-cols-2 gap-2">
              {beats.characters.map((c, i) => (
                <div key={i} className="rounded-md border border-gray-200 bg-gray-50 p-2 text-xs">
                  <b>{c.name}</b>{c.gender ? `（${c.gender}·${c.age || ''}）` : ''}
                  {c.appearance && <p className="mt-1 text-gray-600">{c.appearance}</p>}
                </div>
              ))}
            </div>
          </div>
        )}
        {!!beats.scenes?.length && (
          <div>
            <p className="mb-1 text-sm font-medium flex items-center gap-1"><MapPin className="h-4 w-4" /> 场景（{beats.scenes.length}）</p>
            {beats.scenes.map((s, i) => (
              <div key={i} className="rounded-md border border-gray-200 bg-gray-50 p-2 text-xs">
                <b>{s.name}</b>
                {s.setting && <p className="mt-1 text-gray-600">{s.setting}</p>}
              </div>
            ))}
          </div>
        )}
        {!!beats.beats?.length && (
          <div>
            <p className="mb-1 text-sm font-medium flex items-center gap-1"><Layers className="h-4 w-4" /> 剧本节拍（{beats.beats.length} 镜）</p>
            <div className="space-y-2">
              {beats.beats.map((b, i) => {
                const action = String(b.action || '');
                const camera = String(b.camera || '');
                const atmosphere = String(b.atmosphere || '');
                return (
                  <div key={i} className="rounded-md border border-gray-200 p-2 text-xs">
                    <p className="font-medium">Beat {i + 1} · {String(b.scene || '')}</p>
                    <p className="mt-1 text-gray-700">{String(b.summary || '')}</p>
                    {(action || camera || atmosphere) && (
                      <p className="mt-1 text-gray-500">
                        {action ? `动作：${action}` : ''}{camera ? `｜机位：${camera}` : ''}{atmosphere ? `｜氛围：${atmosphere}` : ''}
                      </p>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="p-6">
      {/* 页头 */}
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Wand2 className="h-6 w-6 text-indigo-500" />
          <h1 className="text-xl font-semibold">提示词提取与重构</h1>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={refreshTasks}
            className="flex items-center gap-1 rounded-md border border-gray-300 px-3 py-1.5 text-sm hover:bg-gray-50"
          >
            <RefreshCw className="h-4 w-4" /> 刷新
          </button>
          <button
            onClick={() => { setShowForm((v) => !v); setError(''); }}
            className="flex items-center gap-1 rounded-md bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-700"
          >
            <Upload className="h-4 w-4" /> {showForm ? '收起表单' : '新建重构任务'}
          </button>
        </div>
      </div>

      {/* 新建表单 */}
      {showForm && (
        <div className="mb-5 rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
          <h2 className="mb-3 text-sm font-semibold">新建提示词重构任务</h2>
          <div className="grid gap-3 md:grid-cols-2">
            <div>
              <label className="mb-1 block text-xs text-gray-600">任务标题（可选）</label>
              <input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="留空则自动取输入首行"
                className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-gray-600">导演模型</label>
              <select
                value={directorModel}
                onChange={(e) => setDirectorModel(e.target.value)}
                className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
              >
                {models.map((m) => (
                  <option key={m.key} value={m.key}>{m.name}</option>
                ))}
              </select>
              {models.find((m) => m.key === directorModel) && (
                <p className="mt-1 text-xs text-gray-500">{models.find((m) => m.key === directorModel)?.description}</p>
              )}
            </div>
          </div>

          <div className="mt-3">
            <label className="mb-1 block text-xs text-gray-600">输入类型</label>
            <div className="flex gap-2">
              {INPUT_TYPES.map((it) => (
                <button
                  key={it.key}
                  onClick={() => { setInputType(it.key); setError(''); }}
                  className={clsx(
                    'rounded-md border px-3 py-1.5 text-sm',
                    inputType === it.key ? 'border-indigo-500 bg-indigo-50 text-indigo-700' : 'border-gray-300 text-gray-600 hover:bg-gray-50',
                  )}
                >
                  {it.label}
                </button>
              ))}
            </div>
          </div>

          {inputType === 'file' ? (
            <div className="mt-3">
              <label className="mb-1 block text-xs text-gray-600">选择文件（txt / md / docx / doc / srt / json / csv，≤2MB）</label>
              <div className="flex items-center gap-2">
                <input
                  ref={fileRef}
                  type="file"
                  accept=".txt,.md,.docx,.doc,.srt,.json,.csv"
                  onChange={(e) => setFile(e.target.files?.[0] || null)}
                  className="hidden"
                />
                <button
                  onClick={() => fileRef.current?.click()}
                  className="flex items-center gap-1 rounded-md border border-gray-300 px-3 py-2 text-sm hover:bg-gray-50"
                >
                  <FileText className="h-4 w-4" /> {file ? file.name : '选择文件'}
                </button>
                {file && <span className="text-xs text-gray-500">{(file.size / 1024).toFixed(1)} KB</span>}
              </div>
            </div>
          ) : (
            <div className="mt-3">
              <label className="mb-1 block text-xs text-gray-600">需要重构的提示词 / 剧本 / 小说</label>
              <textarea
                value={inputText}
                onChange={(e) => setInputText(e.target.value)}
                rows={8}
                placeholder={'粘贴一句话需求、剧本片段或小说章节…\n示例：一位白衣剑客在暴雨中的断桥上与黑衣刀客对峙，刀光如雪，桥下怒江奔涌。'}
                className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
              />
            </div>
          )}

          <div className="mt-3">
            <label className="mb-1 block text-xs text-gray-600">目标平台（输出 AI 工具可生成级提示词集）</label>
            <div className="flex flex-wrap gap-2">
              {PLATFORM_OPTIONS.map((p) => (
                <button
                  key={p.key}
                  onClick={() => togglePlatform(p.key)}
                  className={clsx(
                    'rounded-full border px-3 py-1 text-xs',
                    platforms.includes(p.key) ? 'border-indigo-500 bg-indigo-50 text-indigo-700' : 'border-gray-300 text-gray-500 hover:bg-gray-50',
                  )}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>

          {error && <p className="mt-2 text-sm text-red-600">{error}</p>}

          <button
            onClick={handleSubmit}
            disabled={submitting}
            className="mt-4 flex items-center gap-1 rounded-md bg-indigo-600 px-4 py-2 text-sm text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wand2 className="h-4 w-4" />}
            {submitting ? '提交中…' : '开始提取与重构'}
          </button>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[300px_1fr]">
        {/* 任务列表 */}
        <div className="rounded-lg border border-gray-200 bg-white shadow-sm">
          <div className="border-b border-gray-100 px-3 py-2 text-sm font-semibold">
            历史任务（{tasks.length}）
          </div>
          <div className="max-h-[70vh] overflow-y-auto">
            {tasks.length === 0 && (
              <p className="px-3 py-6 text-center text-sm text-gray-400">暂无任务，点击右上角新建</p>
            )}
            {tasks.map((t) => (
              <button
                key={t.id}
                onClick={() => selectTask(t.id)}
                className={clsx(
                  'w-full border-b border-gray-50 px-3 py-2.5 text-left hover:bg-gray-50',
                  selectedId === t.id && 'bg-indigo-50',
                )}
              >
                <div className="flex items-center justify-between gap-1">
                  <span className="truncate text-sm font-medium">{t.title || '未命名任务'}</span>
                  <span className={clsx('rounded-full px-2 py-0.5 text-[10px]', STATUS_META[t.status]?.cls)}>
                    {STATUS_META[t.status]?.label}
                  </span>
                </div>
                <div className="mt-0.5 flex items-center gap-2 text-[11px] text-gray-400">
                  <span>{t.director_model}</span>
                  <span className="flex items-center gap-0.5"><Clock className="h-3 w-3" />{t.created_at?.slice(5, 16)}</span>
                </div>
                {t.stage && <p className="mt-0.5 truncate text-[11px] text-gray-500">{t.stage}</p>}
              </button>
            ))}
          </div>
        </div>

        {/* 详情 */}
        <div className="rounded-lg border border-gray-200 bg-white shadow-sm">
          {!detail ? (
            <div className="flex flex-col items-center justify-center py-20 text-gray-400">
              <Wand2 className="mb-2 h-10 w-10" />
              <p className="text-sm">选择左侧任务查看重构结果，或新建任务</p>
            </div>
          ) : (
            <div>
              {/* 详情头 */}
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-gray-100 px-4 py-3">
                <div>
                  <h2 className="text-base font-semibold">{detail.title}</h2>
                  <p className="mt-0.5 text-xs text-gray-500">
                    {detail.source_name || detail.input_type === 'file' ? `文件：${detail.source_name}` : '直接输入'} ·
                    导演模型：{models.find((m) => m.key === detail.director_model)?.name || detail.director_model} ·
                    {detail.shot_count != null ? `${detail.shot_count} 镜` : ''}
                  </p>
                  {detail.error && <p className="mt-1 text-xs text-red-600">错误：{detail.error}</p>}
                </div>
                <div className="flex items-center gap-2">
                  <span className={clsx('rounded-full px-2.5 py-1 text-xs', STATUS_META[detail.status]?.cls)}>
                    {STATUS_META[detail.status]?.label}
                  </span>
                  <span className="text-xs text-gray-400">{detail.stage}</span>
                </div>
              </div>

              {/* 页签 */}
              <div className="flex gap-1 border-b border-gray-100 px-4 pt-2">
                {([
                  ['input', '原始输入'],
                  ['beats', '提取节拍'],
                  ['prompts', '提示词集'],
                  ['files', '产物文件'],
                ] as const).map(([key, label]) => (
                  <button
                    key={key}
                    onClick={() => setActiveTab(key)}
                    className={clsx(
                      'rounded-t-md px-3 py-1.5 text-sm',
                      activeTab === key ? 'border-b-2 border-indigo-500 font-medium text-indigo-700' : 'text-gray-500 hover:text-gray-700',
                    )}
                  >
                    {label}
                  </button>
                ))}
              </div>

              <div className="p-4">
                {/* 原始输入 */}
                {activeTab === 'input' && (
                  <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded-md bg-gray-50 p-3 text-xs text-gray-700">
                    {detail.input_text}
                  </pre>
                )}

                {/* 提取节拍 */}
                {activeTab === 'beats' && renderBeats()}

                {/* 提示词集 */}
                {activeTab === 'prompts' && (
                  <div>
                    <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                      <div className="flex flex-wrap gap-1">
                        {detail.report?.platforms?.map((p) => (
                          <button
                            key={p}
                            onClick={() => setActivePlatform(p)}
                            className={clsx(
                              'rounded-md border px-2.5 py-1 text-xs',
                              activePlatform === p ? 'border-indigo-500 bg-indigo-50 text-indigo-700' : 'border-gray-300 text-gray-500 hover:bg-gray-50',
                            )}
                          >
                            {PLATFORM_OPTIONS.find((o) => o.key === p)?.label || p}
                          </button>
                        ))}
                      </div>
                      <button
                        onClick={() => copyText(currentPlatformText)}
                        className="flex items-center gap-1 rounded-md border border-gray-300 px-2.5 py-1 text-xs hover:bg-gray-50"
                      >
                        {copied ? <CheckCircle2 className="h-3.5 w-3.5 text-green-600" /> : <Copy className="h-3.5 w-3.5" />}
                        {copied ? '已复制' : '复制当前平台提示词'}
                      </button>
                    </div>
                    {detail.status === 'running' ? (
                      <div className="flex items-center gap-2 py-16 text-sm text-gray-500">
                        <Loader2 className="h-5 w-5 animate-spin text-indigo-500" />
                        正在重构{detail.stage ? `：${detail.stage}` : '…'}，请稍候（自动刷新）
                      </div>
                    ) : (
                      <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded-md bg-gray-50 p-3 text-xs text-gray-800">
                        {currentPlatformText || '（暂无提示词输出）'}
                      </pre>
                    )}
                    {detail.status === 'success' && (
                      <div className="mt-2 flex items-center justify-end gap-2 text-xs">
                        <a
                          href={reforgeFileUrl(detail.id, 'prompts.md')}
                          target="_blank"
                          rel="noreferrer"
                          className="flex items-center gap-1 rounded-md border border-gray-300 px-2.5 py-1 hover:bg-gray-50"
                        >
                          <Download className="h-3.5 w-3.5" /> 下载 prompts.md
                        </a>
                        <a
                          href={reforgeFileUrl(detail.id, 'report.json')}
                          target="_blank"
                          rel="noreferrer"
                          className="flex items-center gap-1 rounded-md border border-gray-300 px-2.5 py-1 hover:bg-gray-50"
                        >
                          <Download className="h-3.5 w-3.5" /> 下载 report.json
                        </a>
                      </div>
                    )}
                  </div>
                )}

                {/* 产物文件 */}
                {activeTab === 'files' && (
                  <div className="space-y-1">
                    {detail.file_tree.length === 0 && <p className="text-sm text-gray-400">暂无产物文件。</p>}
                    {detail.file_tree.map((f) => (
                      <div key={f.path} className="flex items-center justify-between rounded-md border border-gray-100 px-3 py-2 text-sm hover:bg-gray-50">
                        <span className="flex items-center gap-2 text-gray-700">
                          <FileText className="h-4 w-4 text-gray-400" /> {f.path}
                        </span>
                        <span className="flex items-center gap-2 text-xs text-gray-400">
                          {(f.size / 1024).toFixed(1)} KB
                          <a href={reforgeFileUrl(detail.id, f.path)} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-indigo-600 hover:underline">
                            <Download className="h-3.5 w-3.5" /> 下载
                          </a>
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
