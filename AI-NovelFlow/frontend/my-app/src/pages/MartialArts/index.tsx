/**
 * 武术指导（独立工作流）
 *
 * 一句话需求 → 武术指导 LLM 生成三段产物：
 *   1. 人物锚定卡（角色/武术体系/调性/场景/宫格/兵器/影视参考）
 *   2. 图片提示词（N×N 多宫格文字分镜，直接喂绘图工具）
 *   3. 视频提示词（含参考图阅读协议 + 分镜执行清单，喂视频模型）
 *
 * 本页面为独立工作流，不进入小说→分镜→视频主流程，
 * 后端走独立接口 /api/martial-arts/*（两步 8b 提示词 → 本地 ComfyUI 生成）。
 *
 * 本地生成三步闭环（全部串行防显存溢出）：
 *   ① 文生图：人物锚定卡 → 角色形象图（Flux2-Klein-4B）
 *   ② 图生图：角色形象图(参考) + 文字分镜提示词 → 分镜图（single_image_edit，4B）
 *   ③ 图生视频：分镜图 → H3 视频（ref2va 单参考 / 首尾帧）
 */
import { useState, useCallback, useEffect } from 'react';
import {
  Swords, Wand2, Copy, Check, Loader2, ChevronDown, ChevronUp,
  Info, Image, Video, UserSquare2, AlertTriangle, Clapperboard, Layers, Film,
  History, Trash2, Plus, RefreshCw, Clock,
} from 'lucide-react';
import { useTranslation } from '../../stores/i18nStore';
import { martialArtsApi, toMediaUrl, type MartialArtsResult, type MartialArtsHistoryItem, type MartialCharacterDef } from '../../api/martialArts';

const gridOptions = ['16 = 4×4', '9 = 3×3', '12 = 3×4', '4 = 2×2'];
const weaponOptions = ['徒手', '单刀', '双刀', '剑', '长棍', '双截棍', '软鞭', '长枪', '双枪', '扇'];
const toneOptions = ['暴力美学 + 慢镜', '写实硬派', '飘逸写意', '长镜头压迫', '诙谐杂耍（成龙系）', '肃杀凝重'];
const characterRatioOptions = ['1:1', '4:3', '9:16', '16:9'];
const videoModeOptions = [
  { value: 'ref2va', label: '单参考图（分镜图 → H3 视频）' },
  { value: 'first_last', label: '首尾帧（分镜图 + 尾帧 → 转场视频）' },
  { value: 'three_frame', label: '三关键帧（起点/中段/终点 → 更稳）' },
  { value: 'four_frame', label: '四关键帧（起点/1/3/2/3/终点 → 最稳）' },
];
const videoDurationOptions = [4, 6, 8, 10, 12, 15, 20, 30, 45, 60];

/** 从人物锚定卡提取指定项目内容（如 场景天气氛围/场景布置与构图/武打姿态参考） */
function cardField(card: string, label: string): string {
  if (!card) return '';
  for (const l of card.split('\n')) {
    const t = l.trim();
    if (!t.startsWith('|')) continue;
    const parts = t.replace(/^\|/, '').replace(/\|$/, '').split('|').map((s) => s.trim()).filter(Boolean);
    if (parts.length >= 2 && parts[0] === label) return parts.slice(1).join('；');
  }
  return '';
}

/** 人物锚定卡 markdown 表格 → 纯文本角色描述（"项目：内容；项目：内容"） */
function cardToText(card: string): string {
  if (!card) return '';
  return card
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => l.startsWith('|'))
    .filter((l) => !l.includes('---') && !l.startsWith('| 项目 |'))
    .map((l) => l.replace(/^\|/, '').replace(/\|$/, '').split('|').map((s) => s.trim()).filter(Boolean))
    .filter((parts) => parts.length >= 2)
    .map((parts) => `${parts[0]}：${parts[1]}`)
    .join('；');
}

export default function MartialArts() {
  const { t } = useTranslation();
  const [requirement, setRequirement] = useState('');
  const [showAdvanced, setShowAdvanced] = useState(false);

  const [character, setCharacter] = useState('');
  const [systemStyle, setSystemStyle] = useState('');
  const [tone, setTone] = useState('暴力美学 + 慢镜');
  const [equipment, setEquipment] = useState('');
  const [scene, setScene] = useState('');
  const [grid, setGrid] = useState(gridOptions[0]);
  const [weapon, setWeapon] = useState('徒手');
  const [reference, setReference] = useState('');

  // ---- 多角色定义（用户显式指定每个角色，保证多角色形象稳定进锚定卡）----
  const [characters, setCharacters] = useState<MartialCharacterDef[]>([
    { name: '', role: '主角', gender: '', age: '', physique: '', clothing: '', weapon: '', martial: '' },
  ]);
  const addCharacter = () =>
    setCharacters((prev) => [...prev, { name: '', role: '对手', gender: '', age: '', physique: '', clothing: '', weapon: '', martial: '' }]);
  const updateCharacter = (idx: number, key: keyof MartialCharacterDef, value: string) =>
    setCharacters((prev) => prev.map((c, i) => (i === idx ? { ...c, [key]: value } : c)));
  const removeCharacter = (idx: number) =>
    setCharacters((prev) => (prev.length <= 1 ? prev : prev.filter((_, i) => i !== idx)));

  // ---- 参考图上传与解析（qwen2.5vl 视觉资产）----
  const [referenceImageUrl, setReferenceImageUrl] = useState('');   // 上传后平台 URL（/api/files/...）
  const [referencePreview, setReferencePreview] = useState('');     // 本地预览（objectURL）
  const [referenceAssets, setReferenceAssets] = useState('');       // 解析出的资产文本（可编辑）
  const [referenceAnalyzing, setReferenceAnalyzing] = useState(false);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<MartialArtsResult | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  // ---- 生成扩展状态（文生图 / 图生图 / 图生视频，全部串行防显存溢出）----
  const [genLoading, setGenLoading] = useState<'image' | 'edit' | 'video' | null>(null);
  const [genError, setGenError] = useState<string | null>(null);
  const [characterUrl, setCharacterUrl] = useState('');
  const [characterRatio, setCharacterRatio] = useState('1:1');
  const [characterExtraPrompt, setCharacterExtraPrompt] = useState(''); // 角色图手动附加约束
  const [storyboardPrompt, setStoryboardPrompt] = useState(''); // 空 = 默认使用图片提示词（文字分镜）
  const [storyboardUrl, setStoryboardUrl] = useState('');
  const [videoMode, setVideoMode] = useState<'ref2va' | 'first_last' | 'three_frame' | 'four_frame'>('ref2va');
  const [videoDuration, setVideoDuration] = useState(4);
  const [splitSegments, setSplitSegments] = useState(false); // 分段拼接：8+8 拍 × 15 秒 → 30 秒
  const [videoUrl, setVideoUrl] = useState('');

  // ---- 历史任务持久化（刷新不丢，可回看/恢复）----
  const [historyList, setHistoryList] = useState<MartialArtsHistoryItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [currentHistoryId, setCurrentHistoryId] = useState<string | null>(null);

  const formatTime = (iso: string) => {
    if (!iso) return '';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    return d.toLocaleString('zh-CN', { hour12: false });
  };

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true);
    try {
      const res = await martialArtsApi.listHistory();
      if (res.success && res.data) setHistoryList(res.data);
    } catch {
      // 静默失败，不影响主流程
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  useEffect(() => {
    loadHistory();
  }, [loadHistory]);

  /** 点击历史任务 → 恢复提示词三件套 + 三产物状态 */
  const loadHistoryItem = async (item: MartialArtsHistoryItem) => {
    setError(null);
    setGenError(null);
    setResult({
      characterCard: item.characterCard || '',
      imagePrompt: item.imagePrompt || '',
      videoPrompt: item.videoPrompt || '',
      h3Prompt: item.h3Prompt || '',
      raw: item.raw || '',
      historyId: item.id,
    });
    setCurrentHistoryId(item.id);
    setCharacterUrl(item.characterImageUrl || '');
    setStoryboardUrl(item.storyboardImageUrl || '');
    setVideoUrl(item.videoUrl || '');
    setStoryboardPrompt('');
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  /** 新建任务：清空当前工作区（历史记录仍保留） */
  const handleNewTask = () => {
    setRequirement('');
    setResult(null);
    setCurrentHistoryId(null);
    setCharacterUrl('');
    setStoryboardUrl('');
    setVideoUrl('');
    setStoryboardPrompt('');
    setGenError(null);
    setError(null);
  };

  const handleDeleteHistory = async (id: string) => {
    try {
      await martialArtsApi.deleteHistory(id);
      setHistoryList((prev) => prev.filter((h) => h.id !== id));
      if (currentHistoryId === id) setCurrentHistoryId(null);
    } catch {
      // 静默
    }
  };

  const copyText = useCallback(async (key: string, text: string) => {
    try {
      await navigator.clipboard.writeText(text || '');
      setCopied(key);
      setTimeout(() => setCopied(null), 1500);
    } catch {
      setCopied(null);
    }
  }, []);

  const handleGenerate = async () => {
    if (!requirement.trim()) {
      setError('请先输入一句话需求（如：太极宗师对外家拳师，晨曦庭院，要有太极哲学）');
      return;
    }
    setLoading(true);
    setError(null);
    setResult(null);
    setGenError(null);
    setCharacterUrl('');
    setStoryboardUrl('');
    setVideoUrl('');
    try {
      const res = await martialArtsApi.generate({
        requirement: requirement.trim(),
        character,
        system_style: systemStyle,
        tone,
        equipment,
        scene,
        grid,
        weapon,
        reference,
        reference_assets: referenceAssets.trim() || undefined,
        characters: characters.filter((c) => (c.name || '').trim() !== '' || (c.clothing || '').trim() !== ''),
      });
      if (res.success && res.data) {
        setResult(res.data);
        if (res.data.historyId) {
          setCurrentHistoryId(res.data.historyId);
          loadHistory();
        }
      } else {
        setError(res.message || '生成失败，请重试');
      }
    } catch (e) {
      setError('请求失败：' + String(e));
    } finally {
      setLoading(false);
    }
  };

  // ---- 参考图：上传 + 解析（qwen2.5vl:3b 视觉资产） ----
  const handleUploadReference = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setReferenceImageUrl('');
    setReferenceAssets('');
    setGenError(null);
    const preview = URL.createObjectURL(file);
    setReferencePreview(preview);
    try {
      const res = await martialArtsApi.uploadReference(file);
      if (res.success && res.data?.image_url) {
        setReferenceImageUrl(res.data.image_url);
      } else {
        setGenError(res.message || '参考图上传失败');
        setReferencePreview('');
      }
    } catch (err) {
      setGenError('参考图上传请求失败：' + String(err));
      setReferencePreview('');
    }
  };

  const handleAnalyzeReference = async () => {
    if (!referenceImageUrl) {
      setGenError('请先上传参考图');
      return;
    }
    setReferenceAnalyzing(true);
    setGenError(null);
    try {
      const res = await martialArtsApi.analyzeReference({
        image_url: referenceImageUrl,
        requirement: requirement.trim() || undefined,
      });
      if (res.success && res.data?.assets) {
        setReferenceAssets(res.data.assets);
      } else {
        setGenError(res.message || '参考图解析失败');
      }
    } catch (err) {
      setGenError('参考图解析请求失败：' + String(err));
    } finally {
      setReferenceAnalyzing(false);
    }
  };

  // ---- ① 文生图：人物锚定卡 → 角色形象图（Flux2-Klein-4B）----
  const handleGenerateCharacter = async () => {
    if (!result?.characterCard) {
      setGenError('请先生成武打分镜提示词（取人物锚定卡）');
      return;
    }
    const card = result.characterCard || '';
    const sceneWeather = cardField(card, '场景天气氛围');
    const scene = cardField(card, '场景');
    const comp = cardField(card, '场景布置与构图');
    const poseRef = cardField(card, '武打姿态参考');
    const chars = cardToText(card); // 全量角色信息兜底
    const prompt =
      '武指角色形象图，电影级写实武指质感，全身完整入画，画面构图稳定、人物与场景深度融合一体：' +
      `【场景】${[sceneWeather, scene, comp].filter(Boolean).join('；') || '（按输入需求演绎场景）'}。人物站位遵循构图：双脚踩实地面/桥板/台阶，站立于场景纵深中，与场景元素（地面/栏杆/立柱/树木/山石）产生明确接触，禁止人物脱离场景、禁止悬浮虚空、禁止人物与背景分离成两层。` +
      `【人物】${chars || '（按输入需求演绎）'}。人物数量与需求严格一致，主次分明，多人同框对垒站位。` +
      `【动作】${poseRef || '起手蓄势'}——具体化为可见武术姿态：前弓步或沉马半蹲、重心下沉、一拳护于胸前/一掌前探、身体微侧、肢体线条舒展、发力肌群可见，动作有张力不僵硬、非站桩、衣袂被风带起。` +
      '【整体】人物与场景同一光感同一透视，景深合理，禁止拼贴感、禁止人物飘在虚空。';
    setGenLoading('image');
    setGenError(null);
    setStoryboardUrl('');
    setVideoUrl('');
    try {
      const res = await martialArtsApi.generateImage({
        prompt,
        aspect_ratio: characterRatio,
        history_id: currentHistoryId || undefined,
        extra_prompt: characterExtraPrompt.trim() || undefined,
      });
      if (res.success && res.data?.image_url) {
        setCharacterUrl(res.data.image_url);
      } else {
        setGenError(res.message || '角色形象图生成失败');
      }
    } catch (e) {
      setGenError('文生图请求失败：' + String(e));
    } finally {
      setGenLoading(null);
    }
  };

  // ---- ② 图生图：角色形象图(参考) + 文字分镜提示词 → 分镜图（single_image_edit，4B）----
  const handleEditStoryboard = async () => {
    if (!characterUrl) {
      setGenError('请先生成角色形象图（①）');
      return;
    }
    const prompt = storyboardPrompt.trim() || result?.imagePrompt || '';
    if (!prompt) {
      setGenError('缺少文字分镜提示词，请先生成武打分镜提示词');
      return;
    }
    setGenLoading('edit');
    setGenError(null);
    setVideoUrl('');
    try {
      const res = await martialArtsApi.editImage({
        image_url: characterUrl,
        prompt,
        history_id: currentHistoryId || undefined,
      });
      if (res.success && res.data?.image_url) {
        setStoryboardUrl(res.data.image_url);
      } else {
        setGenError(res.message || '分镜图生成失败');
      }
    } catch (e) {
      setGenError('图生图请求失败：' + String(e));
    } finally {
      setGenLoading(null);
    }
  };

  // ---- ③ 图生视频：分镜图 → H3 视频 ----
  const handleGenerateVideo = async () => {
    if (!result?.videoPrompt) {
      setGenError('请先生成武打分镜提示词');
      return;
    }
    const refUrl = storyboardUrl || characterUrl;
    if (!refUrl) {
      setGenError('请先生成分镜图（②）或角色形象图（①）作为参考图');
      return;
    }
    if (videoMode === 'first_last' && !storyboardUrl) {
      setGenError('首尾帧模式请先生成分镜图（②）作为尾帧基础');
      return;
    }
    if ((videoMode === 'three_frame' || videoMode === 'four_frame') && !storyboardUrl) {
      setGenError('多关键帧模式请先生成分镜图（②）作为关键帧来源');
      return;
    }
    setGenLoading('video');
    setGenError(null);
    try {
      const res = await martialArtsApi.generateVideo({
        prompt: result.videoPrompt,
        image_url: refUrl,
        second_image_url: videoMode === 'first_last' ? storyboardUrl : '',
        mode: videoMode,
        duration_seconds: videoDuration,
        history_id: currentHistoryId || undefined,
        image_is_storyboard: !!storyboardUrl, // 分镜图=16格海报需裁第1格；角色图原样
        split_segments: splitSegments && videoDuration >= 24, // 分段拼接：8+8 拍 × 15 秒 → 30 秒（两段各 15 秒，段间首帧衔接）
      });
      if (res.success && res.data?.video_url) {
        setVideoUrl(res.data.video_url);
        const h3 = (res.data as { h3_prompt?: string }).h3_prompt;
        if (h3) setResult((prev) => (prev ? { ...prev, h3Prompt: h3 } : prev));
      } else {
        setGenError(res.message || '视频生成失败');
      }
    } catch (e) {
      setGenError('图生视频请求失败：' + String(e));
    } finally {
      setGenLoading(null);
    }
  };

  const inputCls =
    'w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 placeholder-gray-400 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500';
  const labelCls = 'block text-sm font-medium text-gray-700 mb-1';

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="mx-auto max-w-5xl px-4 py-8">
        {/* 页头 */}
        <div className="mb-6 flex items-start justify-between">
          <div>
            <h1 className="flex items-center gap-2 text-2xl font-bold text-gray-900">
              <Swords className="h-7 w-7 text-primary-600" />
              武术指导
              <span className="rounded-full bg-primary-50 px-3 py-1 text-xs font-medium text-primary-600">
                独立工作流
              </span>
            </h1>
            <p className="mt-1 text-sm text-gray-500">
              一句话需求 → 武打分镜提示词 + 角色图 + 分镜图 + 视频 · 参考 martial-arts-director-cy (Apache-2.0)
            </p>
          </div>
        </div>

        {/* 历史任务（刷新不丢，可回看/恢复） */}
        <div className="mb-6 rounded-xl border border-gray-200 bg-white shadow-sm">
          <div className="flex items-center justify-between border-b border-gray-100 px-5 py-3">
            <h2 className="flex items-center gap-2 text-base font-semibold text-gray-900">
              <History className="h-5 w-5 text-primary-600" />
              历史任务
              <span className="text-xs font-normal text-gray-400">共 {historyList.length} 条 · 刷新不丢失</span>
            </h2>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={loadHistory}
                disabled={historyLoading}
                className="inline-flex items-center gap-1 rounded-md border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-60"
              >
                <RefreshCw className={`h-3.5 w-3.5 ${historyLoading ? 'animate-spin' : ''}`} />
                刷新
              </button>
              <button
                type="button"
                onClick={handleNewTask}
                className="inline-flex items-center gap-1 rounded-md bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-700"
              >
                <Plus className="h-3.5 w-3.5" />
                新建任务
              </button>
            </div>
          </div>
          {historyList.length ? (
            <ul className="max-h-80 divide-y divide-gray-100 overflow-y-auto">
              {historyList.map((h) => (
                <li
                  key={h.id}
                  onClick={() => loadHistoryItem(h)}
                  className={`flex cursor-pointer items-center gap-3 px-5 py-3 transition-colors hover:bg-gray-50 ${
                    currentHistoryId === h.id ? 'bg-primary-50/60' : ''
                  }`}
                >
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium text-gray-800">{h.requirement || '（无需求文本）'}</div>
                    <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-gray-400">
                      <span>
                        <Clock className="mr-1 inline h-3 w-3" />
                        开始 {formatTime(h.createdAt)}
                      </span>
                      <span>
                        <Check className="mr-1 inline h-3 w-3 text-green-500" />
                        完成 {formatTime(h.updatedAt || h.createdAt)}
                      </span>
                      {h.characterCard && <span className="text-primary-500">锚定卡</span>}
                    </div>
                    {/* 历史任务产物缩略：角色图 / 分镜图 / 视频 */}
                    <div className="mt-2 flex items-center gap-2">
                      {h.characterImageUrl ? (
                        <img
                          src={toMediaUrl(h.characterImageUrl)}
                          alt="角色形象图"
                          title="角色形象图"
                          className="h-14 w-14 shrink-0 rounded-lg border border-gray-200 bg-white object-cover"
                        />
                      ) : (
                        <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-lg border border-dashed border-gray-300 bg-gray-50 text-gray-400">
                          <UserSquare2 className="h-5 w-5" />
                        </div>
                      )}
                      {h.storyboardImageUrl ? (
                        <img
                          src={toMediaUrl(h.storyboardImageUrl)}
                          alt="分镜图"
                          title="分镜图"
                          className="h-14 w-14 shrink-0 rounded-lg border border-gray-200 bg-white object-cover"
                        />
                      ) : (
                        <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-lg border border-dashed border-gray-300 bg-gray-50 text-gray-400">
                          <Image className="h-5 w-5" />
                        </div>
                      )}
                      {h.videoUrl ? (
                        <video
                          src={toMediaUrl(h.videoUrl)}
                          muted
                          playsInline
                          preload="metadata"
                          title="武打视频"
                          className="h-14 w-24 shrink-0 rounded-lg border border-gray-200 bg-black object-cover"
                        />
                      ) : (
                        <div className="flex h-14 w-24 shrink-0 items-center justify-center rounded-lg border border-dashed border-gray-300 bg-gray-50 text-gray-400">
                          <Video className="h-5 w-5" />
                        </div>
                      )}
                    </div>
                  </div>
                  <button
                    type="button"
                    title="删除该历史任务"
                    onClick={(e) => {
                      e.stopPropagation();
                      handleDeleteHistory(h.id);
                    }}
                    className="shrink-0 rounded-md p-1.5 text-gray-400 hover:bg-red-50 hover:text-red-600"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <div className="px-5 py-4 text-sm text-gray-500">
              暂无历史任务。每次「生成武打分镜提示词」成功后会自动保存在这里，刷新页面不丢失；生成的角色图/分镜图/视频也会自动回写到对应记录。
            </div>
          )}
        </div>

        {/* 独立工作流说明 */}
        <div className="mb-6 flex items-start gap-2 rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-700">
          <Info className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            本功能为<strong>独立工作流</strong>，不进入小说→分镜→视频主流程，不影响现有任务队列。
            提示词生成：招式编排（8b）→ 提示词组装（8b），约 1–3 分钟；本地生成三步：角色图 → 分镜图 → 视频，全部 GPU 串行。
          </span>
        </div>

        {/* 输入表单 */}
        <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
          <label className={labelCls}>
            一句话需求 <span className="text-red-500">*</span>
          </label>
          <textarea
            value={requirement}
            onChange={(e) => setRequirement(e.target.value)}
            rows={3}
            placeholder="例：太极宗师对外家拳师，晨曦庭院，要有太极哲学；或：地铁清洁阿姨 vs 三个劫匪，小空间写实打斗"
            className={inputCls}
          />

          {/* 参考图上传（可选）：模型解析生成视觉资产，并入招式编排 */}
          <div className="mt-4 rounded-lg border border-dashed border-gray-300 bg-gray-50 p-4">
            <div className="flex flex-wrap items-center gap-3">
              <span className="text-sm font-medium text-gray-700">参考图（可选，qwen2.5vl 解析资产）</span>
              <label className="cursor-pointer rounded-md bg-white px-3 py-1.5 text-xs font-medium text-gray-700 ring-1 ring-gray-300 hover:bg-gray-100">
                选择图片
                <input type="file" accept="image/png,image/jpeg,image/jpg,image/webp" className="hidden"
                  onChange={handleUploadReference} />
              </label>
              <button
                type="button"
                onClick={handleAnalyzeReference}
                disabled={referenceAnalyzing || !referenceImageUrl}
                className="inline-flex items-center gap-1 rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {referenceAnalyzing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                {referenceAnalyzing ? '解析中（qwen2.5vl）…' : '解析参考图生成资产'}
              </button>
              {referenceImageUrl && (
                <button
                  type="button"
                  onClick={() => { setReferenceImageUrl(''); setReferencePreview(''); setReferenceAssets(''); }}
                  className="text-xs text-gray-400 hover:text-red-500"
                >
                  移除
                </button>
              )}
            </div>
            {referencePreview && (
              <div className="mt-3 flex flex-wrap items-start gap-4">
                <img src={referencePreview} alt="参考图" className="h-40 w-auto rounded-md object-cover ring-1 ring-gray-300" />
                <div className="min-w-[260px] flex-1">
                  <div className="mb-1 text-xs font-medium text-gray-500">解析资产（可编辑，随需求一起进入招式编排）</div>
                  <textarea
                    value={referenceAssets}
                    onChange={(e) => setReferenceAssets(e.target.value)}
                    rows={6}
                    placeholder="点击「解析参考图生成资产」后，此处展示 qwen2.5vl 提取的人物（性别/年龄/服装/武器长短）/场景布置与构图/动作风格/镜头参考；可直接修改"
                    className={inputCls}
                  />
                </div>
              </div>
            )}
          </div>

          <button
            type="button"
            onClick={() => setShowAdvanced(!showAdvanced)}
            className="mt-4 flex items-center gap-1 text-sm font-medium text-primary-600 hover:text-primary-700"
          >
            {showAdvanced ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
            高级选项（不填则按默认值：16宫格 / 徒手 / 暗色舞台灰 / 暴力美学+电影武指）
          </button>

          {showAdvanced && (
            <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <label className={labelCls}>人物 / 体型气质</label>
                <input value={character} onChange={(e) => setCharacter(e.target.value)}
                  placeholder="如：修长精瘦男性，肩宽腿长" className={inputCls} />
              </div>

              {/* 多角色定义：每个角色独立形象（保证多角色时另一角色形象稳定进锚定卡） */}
              <div className="sm:col-span-2 mt-1 rounded-lg border border-primary-100 bg-primary-50/40 p-3">
                <div className="mb-2 flex items-center justify-between">
                  <label className="text-sm font-semibold text-primary-700">角色定义（多角色）</label>
                  <button type="button" onClick={addCharacter}
                    className="inline-flex items-center gap-1 rounded-md bg-primary-600 px-2.5 py-1 text-xs font-semibold text-white hover:bg-primary-700">
                    <Plus className="h-3.5 w-3.5" /> 添加新角色
                  </button>
                </div>
                <div className="space-y-3">
                  {characters.map((c, idx) => (
                    <div key={idx} className="rounded-md border border-primary-200 bg-white p-3">
                      <div className="mb-2 flex items-center justify-between">
                        <span className="text-xs font-semibold text-gray-500">角色 {idx + 1} · {c.role || (idx === 0 ? '主角' : '对手')}</span>
                        {characters.length > 1 && (
                          <button type="button" onClick={() => removeCharacter(idx)}
                            className="text-xs text-gray-400 hover:text-red-500">移除</button>
                        )}
                      </div>
                      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                        <input value={c.name} onChange={(e) => updateCharacter(idx, 'name', e.target.value)}
                          placeholder="角色名（如：太极宗师）" className={inputCls} />
                        <select value={c.role} onChange={(e) => updateCharacter(idx, 'role', e.target.value)} className={inputCls}>
                          <option value="主角">主角</option><option value="对手">对手</option><option value="配角">配角</option>
                        </select>
                        <select value={c.gender} onChange={(e) => updateCharacter(idx, 'gender', e.target.value)} className={inputCls}>
                          <option value="">性别</option><option value="男">男</option><option value="女">女</option><option value="中性">中性</option>
                        </select>
                        <input value={c.age} onChange={(e) => updateCharacter(idx, 'age', e.target.value)}
                          placeholder="年龄（少年/青年/中年/老年）" className={inputCls} />
                        <input value={c.physique} onChange={(e) => updateCharacter(idx, 'physique', e.target.value)}
                          placeholder="体型与气质+形象细节（发型/五官/身高/面形）" className={`${inputCls} col-span-2`} />
                        <input value={c.clothing} onChange={(e) => updateCharacter(idx, 'clothing', e.target.value)}
                          placeholder="服装（材质+颜色+款式+配饰：腰带/护腕/披风/纹样）" className={`${inputCls} col-span-2`} />
                        <input value={c.weapon} onChange={(e) => updateCharacter(idx, 'weapon', e.target.value)}
                          placeholder="兵器（类型+材质：如 玄铁长枪）" className={inputCls} />
                        <input value={c.martial} onChange={(e) => updateCharacter(idx, 'martial', e.target.value)}
                          placeholder="武术体系（如：太极）" className={inputCls} />
                      </div>
                    </div>
                  ))}
                </div>
                <p className="mt-2 text-xs text-gray-400">填写的角色会进入人物锚定卡「角色定义」行，招式编排以这些角色为准（人物数量一致）。</p>
              </div>
              <div>
                <label className={labelCls}>武术体系</label>
                <input value={systemStyle} onChange={(e) => setSystemStyle(e.target.value)}
                  placeholder="如：咏春 / 八极 / MMA / 双刀 / gun-fu" className={inputCls} />
              </div>
              <div>
                <label className={labelCls}>风格调性</label>
                <select value={tone} onChange={(e) => setTone(e.target.value)} className={inputCls}>
                  {toneOptions.map((o) => <option key={o} value={o}>{o}</option>)}
                </select>
              </div>
              <div>
                <label className={labelCls}>装备 / 服装</label>
                <input value={equipment} onChange={(e) => setEquipment(e.target.value)}
                  placeholder="如：黑色短款机能马甲 + 紧身长裤" className={inputCls} />
              </div>
              <div>
                <label className={labelCls}>场景</label>
                <input value={scene} onChange={(e) => setScene(e.target.value)}
                  placeholder="如：竹林晨雾 / 雨夜霓虹街 / 暗色舞台灰" className={inputCls} />
              </div>
              <div>
                <label className={labelCls}>宫格规格</label>
                <select value={grid} onChange={(e) => setGrid(e.target.value)} className={inputCls}>
                  {gridOptions.map((o) => <option key={o} value={o}>{o}</option>)}
                </select>
              </div>
              <div>
                <label className={labelCls}>兵器</label>
                <select value={weapon} onChange={(e) => setWeapon(e.target.value)} className={inputCls}>
                  {weaponOptions.map((o) => <option key={o} value={o}>{o}</option>)}
                </select>
              </div>
              <div>
                <label className={labelCls}>影视参考（可选）</label>
                <input value={reference} onChange={(e) => setReference(e.target.value)}
                  placeholder="如：袁和平《卧虎藏龙》/ John Wick" className={inputCls} />
              </div>
            </div>
          )}

          <div className="mt-6 flex items-center gap-3">
            <button
              onClick={handleGenerate}
              disabled={loading}
              className="inline-flex items-center gap-2 rounded-md bg-primary-600 px-5 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wand2 className="h-4 w-4" />}
              {loading ? '正在编排招式…（两步工作流，约 1–3 分钟）' : '生成武打分镜提示词'}
            </button>
            {loading && (
              <span className="text-sm text-amber-600">
                已加入 GPU 串行锁，与其他任务互斥，请勿重复点击
              </span>
            )}
          </div>

          {error && (
            <div className="mt-4 flex items-start gap-2 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              {error}
            </div>
          )}
        </div>

        {/* 结果区 */}
        {result && (
          <div className="mt-8 space-y-6">
            {/* 人物锚定卡 */}
            {result.characterCard && (
              <div className="rounded-xl border border-gray-200 bg-white shadow-sm">
                <div className="flex items-center justify-between border-b border-gray-100 px-5 py-3">
                  <h2 className="flex items-center gap-2 text-base font-semibold text-gray-900">
                    <UserSquare2 className="h-5 w-5 text-primary-600" />
                    人物锚定卡
                  </h2>
                  <button
                    onClick={() => copyText('card', result.characterCard)}
                    className="inline-flex items-center gap-1 rounded-md border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50"
                  >
                    {copied === 'card' ? <Check className="h-3.5 w-3.5 text-green-600" /> : <Copy className="h-3.5 w-3.5" />}
                    复制
                  </button>
                </div>
                <pre className="whitespace-pre-wrap px-5 py-4 text-sm leading-relaxed text-gray-800">{result.characterCard}</pre>
              </div>
            )}

            {/* 图片提示词 */}
            {result.imagePrompt && (
              <div className="rounded-xl border border-gray-200 bg-white shadow-sm">
                <div className="flex items-center justify-between border-b border-gray-100 px-5 py-3">
                  <h2 className="flex items-center gap-2 text-base font-semibold text-gray-900">
                    <Image className="h-5 w-5 text-primary-600" />
                    文字分镜提示词（多宫格海报）
                  </h2>
                  <button
                    onClick={() => copyText('img', result.imagePrompt)}
                    className="inline-flex items-center gap-1 rounded-md border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50"
                  >
                    {copied === 'img' ? <Check className="h-3.5 w-3.5 text-green-600" /> : <Copy className="h-3.5 w-3.5" />}
                    复制
                  </button>
                </div>
                <pre className="whitespace-pre-wrap px-5 py-4 text-sm leading-relaxed text-gray-800">{result.imagePrompt}</pre>
                <div className="border-t border-gray-100 px-5 py-3 text-xs text-gray-500">
                  作为②图生图的文字分镜输入：以角色形象图为参考，把 N 招按序排成多宫格分镜海报。
                </div>
              </div>
            )}

            {/* 视频提示词 */}
            {result.videoPrompt && (
              <div className="rounded-xl border border-gray-200 bg-white shadow-sm">
                <div className="flex items-center justify-between border-b border-gray-100 px-5 py-3">
                  <h2 className="flex items-center gap-2 text-base font-semibold text-gray-900">
                    <Video className="h-5 w-5 text-primary-600" />
                    视频提示词（图生视频，与分镜图配套）
                  </h2>
                  <button
                    onClick={() => copyText('vid', result.videoPrompt)}
                    className="inline-flex items-center gap-1 rounded-md border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50"
                  >
                    {copied === 'vid' ? <Check className="h-3.5 w-3.5 text-green-600" /> : <Copy className="h-3.5 w-3.5" />}
                    复制
                  </button>
                </div>
                <pre className="whitespace-pre-wrap px-5 py-4 text-sm leading-relaxed text-gray-800">{result.videoPrompt}</pre>
                <div className="border-t border-gray-100 px-5 py-3 text-xs text-gray-500">
                  分镜图作参考图（first frame）+ 本段提示词一起喂视频模型（即梦 / Kling / Seedance / Veo / 本地 H3）。
                </div>
              </div>
            )}

            {/* H3 视频提示词（实际执行用英文模板，生成视频成功后回填） */}
            {result.h3Prompt && (
              <div className="rounded-xl border border-gray-200 bg-white shadow-sm">
                <div className="flex items-center justify-between border-b border-gray-100 px-5 py-3">
                  <h2 className="flex items-center gap-2 text-base font-semibold text-gray-900">
                    <Video className="h-5 w-5 text-primary-600" />
                    H3 视频提示词（执行用，逐分镜独立镜头块）
                  </h2>
                  <button
                    onClick={() => copyText('h3', result.h3Prompt || '')}
                    className="inline-flex items-center gap-1 rounded-md border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50"
                  >
                    {copied === 'h3' ? <Check className="h-3.5 w-3.5 text-green-600" /> : <Copy className="h-3.5 w-3.5" />}
                    复制
                  </button>
                </div>
                <pre className="whitespace-pre-wrap px-5 py-4 text-xs leading-relaxed text-gray-700">{result.h3Prompt}</pre>
                <div className="border-t border-gray-100 px-5 py-3 text-xs text-gray-500">
                  生成视频时实际提交给本地 H3（qwen3vl-32B）的英文模板：每个 [Shot N] 为独立镜头（机位 / 动作 / 对手反应 / 相机运动），
                  机位逐镜变化，性别与外貌按锚定卡锁定。此段为执行产物，仅供核对。
                </div>
              </div>
            )}

            {/* 本地生成区：文生图 → 图生图 → 图生视频（全部串行防显存溢出） */}
            <div className="rounded-xl border border-gray-200 bg-white shadow-sm">
              <div className="flex items-center justify-between border-b border-gray-100 px-5 py-3">
                <h2 className="flex items-center gap-2 text-base font-semibold text-gray-900">
                  <Clapperboard className="h-5 w-5 text-primary-600" />
                  本地生成（Flux2-Klein-4B + H3，串行调度）
                </h2>
              </div>
              <div className="space-y-5 px-5 py-4">
                {/* ① 文生图：角色形象图 */}
                <div className="rounded-lg border border-gray-100 bg-gray-50 p-4">
                  <div className="flex flex-wrap items-center gap-3">
                    <span className="flex items-center gap-1 text-sm font-semibold text-gray-700">
                      <Layers className="h-4 w-4 text-primary-600" /> ① 角色形象图（文生图）
                    </span>
                    <select
                      value={characterRatio}
                      onChange={(e) => setCharacterRatio(e.target.value)}
                      className="rounded-md border border-gray-300 bg-white px-2 py-1.5 text-xs text-gray-700"
                    >
                      {characterRatioOptions.map((o) => <option key={o} value={o}>{o}</option>)}
                    </select>
                    <button
                      onClick={handleGenerateCharacter}
                      disabled={genLoading !== null}
                      className="inline-flex items-center gap-1.5 rounded-md bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      {genLoading === 'image' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Image className="h-4 w-4" />}
                      {genLoading === 'image' ? '角色图生成中（约 30-90s）…' : '生成角色形象图'}
                    </button>
                  </div>
                  <textarea
                    value={characterExtraPrompt}
                    onChange={(e) => setCharacterExtraPrompt(e.target.value)}
                    rows={2}
                    placeholder="手动附加提示词约束（可选）：每次生成角色形象图时追加到提示词末尾。例如：五官端正、中式古风服装、正面全身站姿、画面无文字无水印"
                    className="mt-2 w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900"
                  />
                  <p className="mt-1 text-xs text-gray-500">
                    输入为人物锚定卡角色信息（Flux2-Klein-4B 文生图）；此图是②图生图的角色一致性参考锚点。上方附加约束可稳定生成效果（降低随机性）。
                  </p>
                  {characterUrl && (
                    <div className="mt-3">
                      <img src={toMediaUrl(characterUrl)} alt="武指角色形象图" className="max-h-96 rounded-lg border border-gray-200 bg-white object-contain shadow-sm" />
                      <p className="mt-1 flex items-center gap-3 text-xs text-gray-500">
                        <span>角色形象图（锁定人物外貌/服装/气质）</span>
                        <a href={toMediaUrl(characterUrl)} download="武指角色形象图.png" className="font-medium text-primary-600 hover:text-primary-700">下载</a>
                      </p>
                    </div>
                  )}
                </div>

                {/* ② 图生图：角色图 + 文字分镜 → 分镜图（角色图生成后出现） */}
                {characterUrl && (
                  <div className="rounded-lg border border-gray-100 bg-gray-50 p-4">
                    <span className="flex items-center gap-1 text-sm font-semibold text-gray-700">
                      <Wand2 className="h-4 w-4 text-primary-600" /> ② 分镜图（图生图：角色图 + 文字分镜）
                    </span>
                    <textarea
                      value={storyboardPrompt}
                      onChange={(e) => setStoryboardPrompt(e.target.value)}
                      rows={3}
                      placeholder="留空默认使用上方「文字分镜提示词（多宫格海报）」；也可自行改写为单格/多格分镜描述"
                      className="mt-2 w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900"
                    />
                    <div className="mt-2 flex items-center gap-3">
                      <button
                        onClick={handleEditStoryboard}
                        disabled={genLoading !== null}
                        className="inline-flex items-center gap-1.5 rounded-md bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        {genLoading === 'edit' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wand2 className="h-4 w-4" />}
                        {genLoading === 'edit' ? '分镜图生成中（约 30-90s）…' : '生成分镜图'}
                      </button>
                      <span className="text-xs text-gray-500">角色图作参考（保持同一人物），文字分镜描述招式与镜头</span>
                    </div>
                    {storyboardUrl && (
                      <div className="mt-3">
                        <img src={toMediaUrl(storyboardUrl)} alt="武指分镜图" className="max-h-96 rounded-lg border border-gray-200 bg-white object-contain shadow-sm" />
                        <p className="mt-1 flex items-center gap-3 text-xs text-gray-500">
                          <span>分镜图（多宫格分镜海报，可直接作视频参考图/首帧）</span>
                          <a href={toMediaUrl(storyboardUrl)} download="武指分镜图.png" className="font-medium text-primary-600 hover:text-primary-700">下载</a>
                        </p>
                      </div>
                    )}
                  </div>
                )}

                {/* ③ 图生视频：分镜图 → H3 视频（角色图生成后出现） */}
                {characterUrl && (
                  <div className="rounded-lg border border-gray-100 bg-gray-50 p-4">
                    <span className="flex items-center gap-1 text-sm font-semibold text-gray-700">
                      <Film className="h-4 w-4 text-primary-600" /> ③ 武打视频（图生视频 · H3）
                    </span>
                    <div className="mt-2 flex flex-wrap items-center gap-3">
                      <select
                        value={videoMode}
                        onChange={(e) => setVideoMode(e.target.value as 'ref2va' | 'first_last')}
                        className="rounded-md border border-gray-300 bg-white px-2 py-1.5 text-xs text-gray-700"
                      >
                        {videoModeOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                      </select>
                      <select
                        value={videoDuration}
                        onChange={(e) => setVideoDuration(Number(e.target.value))}
                        className="rounded-md border border-gray-300 bg-white px-2 py-1.5 text-xs text-gray-700"
                      >
                        {videoDurationOptions.map((o) => <option key={o} value={o}>{o} 秒</option>)}
                      </select>
                      {videoDuration >= 24 && (
                        <label className="flex cursor-pointer items-center gap-1.5 text-xs text-gray-600" title="16 拍拆 8+8，两段各 15 秒（H3 舒适区），第二段首帧=第一段尾帧，ffmpeg 拼 30 秒，段间不跳变">
                          <input
                            type="checkbox"
                            checked={splitSegments}
                            onChange={(e) => setSplitSegments(e.target.checked)}
                            className="h-3.5 w-3.5 accent-primary-600"
                          />
                          分段拼接 8+8（15s×2，更稳）
                        </label>
                      )}
                      <button
                        onClick={handleGenerateVideo}
                        disabled={genLoading !== null}
                        className="inline-flex items-center gap-1.5 rounded-md bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        {genLoading === 'video' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Video className="h-4 w-4" />}
                        {genLoading === 'video' ? '视频生成中（约 2-6 分钟）…' : '生成武打视频'}
                      </button>
                      <span className="text-xs text-gray-500">参考图默认取分镜图（②），未生成时回退角色图（①）</span>
                    </div>
                    <p className="mt-2 text-xs text-gray-500">
                      显存治理：生成前自动卸载 Ollama + 释放 flux 缓存，H3 独占显存，与其他任务互斥（GPU 串行锁）。
                    </p>
                    {videoUrl && (
                      <div className="mt-3">
                        <video src={toMediaUrl(videoUrl)} controls className="max-h-96 w-full rounded-lg border border-gray-200 bg-black shadow-sm" />
                        <p className="mt-1 flex items-center gap-3 text-xs text-gray-500">
                          <span>生成的武打视频（{videoMode === 'first_last' ? '首尾帧转场' : '单参考图'} · {videoDuration}s）</span>
                          <a href={toMediaUrl(videoUrl)} download="武指武打视频.mp4" className="font-medium text-primary-600 hover:text-primary-700">下载</a>
                        </p>
                      </div>
                    )}
                  </div>
                )}

                {genError && (
                  <div className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                    {genError}
                  </div>
                )}
                {genLoading !== null && (
                  <p className="text-xs text-amber-600">
                    已持 GPU 串行锁，生成期间不会与其他 LLM / ComfyUI 任务并发，请勿重复点击。
                  </p>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
