/**
 * 章节相关 API
 */
import { api } from './index';
import type { Chapter, Novel } from '../types';

export interface ParseResult {
  created: number;
  updated: number;
  total: number;
}

export interface DialogueData {
  character_name: string;
  text: string;
  emotion_prompt?: string;
  audio_url?: string;
  audio_task_id?: string;
  audio_source?: 'ai_generated' | 'uploaded';
}

export interface AudioTaskResult {
  character_name: string;
  task_id: string;
  status: string;
  message?: string;
}

export interface AudioWarning {
  shot_index?: number;
  character_name: string;
  reason: string;
}

// API 返回的 data 字段结构
export interface GenerateShotAudioData {
  tasks: AudioTaskResult[];
  warnings: AudioWarning[];
}

export interface GenerateAllAudioData {
  tasks: AudioTaskResult[];
  warnings: AudioWarning[];
  total_tasks: number;
  total_warnings: number;
}


export interface ParseAssetItem {
  id: string;
  name: string;
  description?: string;
  appearance?: string;
  imageUrl?: string;
  generatingStatus?: string;
  startChapter?: number;
  endChapter?: number;
  sourceRange?: string;
  updatedAt?: string;
}

export interface ParseAndGenerateResult {
  type?: 'characters' | 'scenes' | 'props';
  statistics?: ParseResult;
  queuedImageTasks?: number;
  message?: string;
  items?: ParseAssetItem[];
}

/** 章节最终视频（合并后）信息 */
export interface FinalVideoInfo {
  finalVideo: string | null;
  chapterVideoUrl?: string | null;
  chapterVideoDuration?: number | null;
  chapterVideoSize?: number | null;
  chapterVideoShotCount?: number | null;
  chapterVideoTaskId?: string | null;
  chapterVideoCompletedAt?: string | null;
}

export const chapterApi = {
  /** 获取章节信息 */
  fetch: (novelId: string, chapterId: string) =>
    api.get<Chapter>(`/novels/${novelId}/chapters/${chapterId}/`),

  /** 获取章节最终视频（合并后）信息；尚未合并时 data 为 null */
  finalVideo: (novelId: string, chapterId: string) =>
    api.get<FinalVideoInfo | null>(`/novels/${novelId}/chapters/${chapterId}/final-video`),

  /** 更新章节 */
  update: (novelId: string, chapterId: string, data: { title: string; content: string }) =>
    api.put<Chapter>(`/novels/${novelId}/chapters/${chapterId}/`, data),

  /** 删除章节 */
  delete: (novelId: string, chapterId: string) =>
    api.delete(`/novels/${novelId}/chapters/${chapterId}/`),

  /** 获取小说所有章节 */
  fetchByNovel: (novelId: string) =>
    api.get<Chapter[]>(`/novels/${novelId}/chapters/`),

  /** 创建章节 */
  create: (novelId: string, data: Partial<Chapter>) =>
    api.post<Chapter>(`/novels/${novelId}/chapters/`, data),

  /** 解析章节角色 */
  parseCharacters: (novelId: string, chapterId: string, isIncremental = true) =>
    api.post<{ statistics: ParseResult }>(`/novels/${novelId}/chapters/${chapterId}/parse-characters/`, { is_incremental: isIncremental }),

  /** 解析章节场景 */
  parseScenes: (novelId: string, chapterId: string, isIncremental = true) =>
    api.post<{ statistics: ParseResult }>(`/novels/${novelId}/chapters/${chapterId}/parse-scenes/`, { is_incremental: isIncremental }),


  /** 解析章节角色/场景/道具并排队生成缺失图，返回实体列表（章节编辑页三按钮） */
  parseAndGenerate: (novelId: string, chapterId: string, type: 'characters' | 'scenes' | 'props', isIncremental = true) =>
    api.post<ParseAndGenerateResult>(`/novels/${novelId}/chapters/${chapterId}/parse-and-generate/?type=${type}`, { is_incremental: isIncremental }),
  /** 拆分章节 */
  split: (novelId: string, chapterId: string) =>
    api.post<Chapter[]>(`/novels/${novelId}/chapters/${chapterId}/split/`),

  /** 清理章节资源 */
  clearResources: (novelId: string, chapterId: string) =>
    api.post(`/novels/${novelId}/chapters/${chapterId}/clear-resources`),

  /** 下载章节素材 */
  downloadMaterials: (novelId: string, chapterId: string) =>
    `/api/novels/${novelId}/chapters/${chapterId}/download-materials/`,

  /** 合并章节视频 */
  mergeVideos: (novelId: string, chapterId: string) =>
    api.post(`/novels/${novelId}/chapters/${chapterId}/merge-videos`),

  /** 生成分镜台词音频 */
  generateShotAudio: (novelId: string, chapterId: string, shotIndex: number, dialogues: DialogueData[]) =>
    api.post<GenerateShotAudioData>(`/novels/${novelId}/chapters/${chapterId}/shots/${shotIndex}/audio`, { dialogues }),

  /** 批量生成章节所有分镜台词音频 */
  generateAllAudio: (novelId: string, chapterId: string) =>
    api.post<GenerateAllAudioData>(`/novels/${novelId}/chapters/${chapterId}/audio/generate-all`),

  /** 上传分镜台词音频 */
  uploadDialogueAudio: (novelId: string, chapterId: string, shotIndex: number, characterName: string, file: File) => {
    const formData = new FormData();
    formData.append('file', file);
    return api.upload<{
      shot_index: number;
      character_name: string;
      audio_url: string;
      audio_source: string;
      parsed_data: any;
    }>(`/novels/${novelId}/chapters/${chapterId}/shots/${shotIndex}/dialogues/${encodeURIComponent(characterName)}/audio/upload`, formData);
  },

  /** 删除分镜台词音频 */
  deleteDialogueAudio: (novelId: string, chapterId: string, shotIndex: number, characterName: string) =>
    api.delete<{
      success: boolean;
      data: {
        shot_index: number;
        character_name: string;
      };
      message: string;
    }>(`/novels/${novelId}/chapters/${chapterId}/shots/${shotIndex}/dialogues/${encodeURIComponent(characterName)}/audio`),

  /** 批量导入预览 */
  batchImportPreview: (novelId: string, file: File) => {
    const formData = new FormData();
    formData.append('file', file);
    return api.upload<{
      chapters: { number: number; title: string; content_length: number; action: 'new' | 'replace' }[];
      summary: { total: number; new: number; replace: number };
      errors: { segment: number; title: string; reason: string }[];
    }>(`/novels/${novelId}/chapters/batch-import/preview`, formData);
  },

  /** 批量导入执行 */
  batchImport: (novelId: string, file: File) => {
    const formData = new FormData();
    formData.append('file', file);
    return api.upload<{
      total: number;
      created: number;
      updated: number;
      failed: number;
      errors: { number?: number; title: string; reason: string }[];
      chapters: any[];
    }>(`/novels/${novelId}/chapters/batch-import`, formData);
  },
};
