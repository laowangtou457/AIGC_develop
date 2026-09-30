import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { toast } from '../../../stores/toastStore';
import { useTranslation } from '../../../stores/i18nStore';
import { novelApi } from '../../../api/novels';
import { chapterApi, type ParseResult, type ParseAssetItem } from '../../../api/chapters';
import { characterApi } from '../../../api/characters';
import { sceneApi } from '../../../api/scenes';
import { propApi } from '../../../api/props';
import type { Chapter, Novel } from '../../../types';
import type { ParseResultData, PreviewImageState } from '../types';

export function useChapterDetailState() {
  const { t } = useTranslation();
  const { id, cid } = useParams<{ id: string; cid: string }>();
  const navigate = useNavigate();

  const [chapter, setChapter] = useState<Chapter | null>(null);
  const [novel, setNovel] = useState<Novel | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [content, setContent] = useState('');
  const [title, setTitle] = useState('');
  const [previewImage, setPreviewImage] = useState<PreviewImageState>({ isOpen: false, url: null, index: 0, images: [] });
  const [parsingChapter, setParsingChapter] = useState(false);
  const [parsingScenes, setParsingScenes] = useState(false);
  const [parsingProps, setParsingProps] = useState(false);
  const [parseResult, setParseResult] = useState<ParseResultData | null>(null);
  const [parseScenesResult, setParseScenesResult] = useState<ParseResultData | null>(null);
  const [parsePropsResult, setParsePropsResult] = useState<ParseResultData | null>(null);
  const [parseCharactersItems, setParseCharactersItems] = useState<ParseAssetItem[]>([]);
  const [parseScenesItems, setParseScenesItems] = useState<ParseAssetItem[]>([]);
  const [parsePropsItems, setParsePropsItems] = useState<ParseAssetItem[]>([]);

  useEffect(() => { if (id && cid) fetchData(); }, [id, cid]);

  const fetchData = async () => {
    setIsLoading(true);
    try {
      const novelData = await novelApi.fetch(id!);
      if (novelData.success && novelData.data) setNovel(novelData.data);
      const chapterData = await chapterApi.fetch(id!, cid!);
      if (chapterData.success && chapterData.data) {
        setChapter(chapterData.data);
        setTitle(chapterData.data.title);
        setContent(chapterData.data.content || '');
      }
    } catch (error) {
      console.error('获取数据失败:', error);
    } finally {
      setIsLoading(false);
    }
  };

  const handleSave = async () => {
    setIsSaving(true);
    try {
      const data = await chapterApi.update(id!, cid!, { title, content });
      if (data.success && data.data) { setChapter(data.data); toast.success(t('common.saveSuccess')); }
    } catch (error) {
      console.error('保存失败:', error);
      toast.error(t('common.saveFailed'));
    } finally {
      setIsSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!confirm(t('chapterDetail.confirmDelete'))) return;
    try {
      await chapterApi.delete(id!, cid!);
      navigate(`/novels/${id}`);
    } catch (error) {
      console.error('删除失败:', error);
      toast.error(t('chapterDetail.deleteFailed'));
    }
  };

  const handleGenerate = () => {
    if (!content.trim()) { toast.warning(t('chapterDetail.pleaseEditContent')); return; }
    navigate(`/novels/${id}/chapters/${cid}/generate`);
  };


  /** 解析后轮询刷新实体图片生成状态（ComfyUI 异步任务） */
  const pollAssets = useCallback(async (type: 'characters' | 'scenes' | 'props', setItems: (items: ParseAssetItem[]) => void) => {
    const fetchList = type === 'characters' ? characterApi.fetchList : type === 'scenes' ? sceneApi.fetchList : propApi.fetchList;
    const deadline = Date.now() + 180000; // 最多轮询 3 分钟
    while (Date.now() < deadline) {
      await new Promise(r => setTimeout(r, 5000));
      try {
        const res = await fetchList(id!);
        if (res.success && res.data) {
          const list: ParseAssetItem[] = (res.data as any[]).map(x => ({
            id: x.id,
            name: x.name,
            description: x.description,
            appearance: (x as any).appearance,
            imageUrl: x.imageUrl || (x as any).image_url,
            generatingStatus: x.generatingStatus || (x as any).generating_status,
          }));
          setItems(list);
          const hasRunning = list.some(x => x.generatingStatus === 'running' || x.generatingStatus === 'pending');
          if (!hasRunning) break;
        }
      } catch (e) {
        console.error('轮询解析结果失败:', e);
        break;
      }
    }
  }, [id]);
  /** 一次性加载某类资产列表（用于全本解析完成后刷新展示框） */
  const loadAssetsOnce = useCallback(async (type: 'characters' | 'scenes' | 'props', setItems: (items: ParseAssetItem[]) => void) => {
    try {
      const fetchList = type === 'characters' ? characterApi.fetchList : type === 'scenes' ? sceneApi.fetchList : propApi.fetchList;
      const res = await fetchList(id!);
      if (res.success && res.data) {
        const list: ParseAssetItem[] = (res.data as any[]).map(x => ({
          id: x.id,
          name: x.name,
          description: x.description,
          appearance: (x as any).appearance,
          imageUrl: x.imageUrl || (x as any).image_url,
          generatingStatus: x.generatingStatus || (x as any).generating_status,
        }));
        setItems(list);
      }
    } catch (e) {
      console.error('加载资产列表失败:', e);
    }
  }, [id]);

  /** 轮询全本解析任务直到完成/失败（最长 10 分钟），完成后刷新展示框 */
  const pollParseTask = useCallback(async (
    taskId: string,
    type: 'characters' | 'scenes' | 'props',
    setResult: (r: ParseResultData) => void,
    setItems: (items: ParseAssetItem[]) => void,
  ) => {
    const deadline = Date.now() + 600000;
    while (Date.now() < deadline) {
      await new Promise(r => setTimeout(r, 5000));
      try {
        const res = await novelApi.getParseTask(taskId);
        const task = (res as any)?.data || res;
        if (task?.status === 'completed') {
          const stats = task.statistics?.parse || {};
          setResult({ created: stats.created || 0, updated: stats.updated || 0, total: stats.total || 0 });
          await loadAssetsOnce(type, setItems);
          const queued = task.statistics?.queued || 0;
          toast.success(t('chapterDetail.parseComplete') + `：新增 ${stats.created || 0}，更新 ${stats.updated || 0}，排队生图 ${queued}`);
          return;
        }
        if (task?.status === 'failed') {
          toast.error(t('chapterDetail.parseFailed') + ': ' + (task.message || '未知错误'));
          await loadAssetsOnce(type, setItems);
          return;
        }
      } catch (e) {
        console.error('轮询全本解析失败:', e);
        toast.error(t('chapterDetail.parseFailed'));
        return;
      }
    }
    toast.warning(t('chapterDetail.parseComplete') + '（超时，请到资产页刷新查看）');
  }, [id, loadAssetsOnce, t]);

  const handleParseCharacters = async () => {
    if (!content.trim()) { toast.warning(t('chapterDetail.chapterEmptyError')); return; }
    setParsingChapter(true);
    setParseResult(null);
    try {
      const data = await novelApi.parseAndGenerateAll(id!, 'characters');
      if (data.success) {
        toast.info(t('chapterDetail.parseCharacters') + '：全本解析已启动');
        await pollParseTask((data as any).task_id, 'characters', setParseResult, setParseCharactersItems);
      } else {
        toast.error(t('chapterDetail.parseFailed') + ': ' + (data as any).message);
      }
    } catch (error) {
      console.error(t('chapterDetail.parseFailed') + ':', error);
      toast.error(t('chapterDetail.parseFailed'));
    } finally {
      setParsingChapter(false);
    }
  };

  const handleParseScenes = async () => {
    if (!content.trim()) { toast.warning(t('chapterDetail.chapterEmptyError')); return; }
    setParsingScenes(true);
    setParseScenesResult(null);
    try {
      const data = await novelApi.parseAndGenerateAll(id!, 'scenes');
      if (data.success) {
        toast.info(t('chapterDetail.parseScenes') + '：全本解析已启动');
        await pollParseTask((data as any).task_id, 'scenes', setParseScenesResult, setParseScenesItems);
      } else {
        toast.error(t('chapterDetail.parseScenesFailed') + ': ' + (data as any).message);
      }
    } catch (error) {
      console.error(t('chapterDetail.parseScenesFailed') + ':', error);
      toast.error(t('chapterDetail.parseScenesFailed'));
    } finally {
      setParsingScenes(false);
    }
  };

  const handleParseProps = async () => {
    if (!content.trim()) { toast.warning(t('chapterDetail.chapterEmptyError')); return; }
    setParsingProps(true);
    setParsePropsResult(null);
    try {
      const data = await novelApi.parseAndGenerateAll(id!, 'props');
      if (data.success) {
        toast.info(t('chapterDetail.parseProps') + '：全本解析已启动');
        await pollParseTask((data as any).task_id, 'props', setParsePropsResult, setParsePropsItems);
      } else {
        toast.error(t('chapterDetail.parsePropsFailed') + ': ' + (data as any).message);
      }
    } catch (error) {
      console.error(t('chapterDetail.parsePropsFailed') + ':', error);
      toast.error(t('chapterDetail.parsePropsFailed'));
    } finally {
      setParsingProps(false);
    }
  };


  const openImagePreview = useCallback((url: string, index: number, images: string[]) => {
    setPreviewImage({ isOpen: true, url, index, images });
  }, []);

  const closeImagePreview = useCallback(() => {
    setPreviewImage({ isOpen: false, url: null, index: 0, images: [] });
  }, []);

  const navigatePreview = useCallback((direction: 'prev' | 'next') => {
    if (!previewImage.images.length) return;
    const newIndex = direction === 'prev'
      ? (previewImage.index === 0 ? previewImage.images.length - 1 : previewImage.index - 1)
      : (previewImage.index === previewImage.images.length - 1 ? 0 : previewImage.index + 1);
    setPreviewImage({ ...previewImage, url: previewImage.images[newIndex], index: newIndex });
  }, [previewImage]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (!previewImage.isOpen) return;
      if (e.key === 'ArrowLeft') { e.preventDefault(); navigatePreview('prev'); }
      else if (e.key === 'ArrowRight') { e.preventDefault(); navigatePreview('next'); }
      else if (e.key === 'Escape') { e.preventDefault(); closeImagePreview(); }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [previewImage.isOpen, previewImage.index, previewImage.images, navigatePreview, closeImagePreview]);

  return {
    // State
    id, cid, chapter, novel, isLoading, isSaving, content, setContent, title, setTitle,
    previewImage, parsingChapter, parsingScenes, parsingProps, parseResult, parseScenesResult, parsePropsResult, parseCharactersItems, parseScenesItems, parsePropsItems,
    // Actions
    handleSave, handleDelete, handleGenerate, handleParseCharacters, handleParseScenes, handleParseProps,
    openImagePreview, closeImagePreview, navigatePreview,
  };
}
