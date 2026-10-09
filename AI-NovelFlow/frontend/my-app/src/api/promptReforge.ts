/**
 * 提示词提取与重构 API
 * 输入 提示词/剧本/小说 → 按导演模型提取节拍 → 重构为 AI 工具可生成级多平台提示词集
 */
import { api, API_BASE } from './index';

export interface DirectorModel {
  key: string;
  name: string;
  description: string;
}

export interface ReforgeFileNode {
  path: string;
  size: number;
}

export interface PromptReforgeTask {
  id: string;
  title: string;
  input_type: string;
  source_name: string | null;
  director_model: string;
  target_platforms: string[];
  status: 'running' | 'success' | 'failed';
  stage: string | null;
  error: string | null;
  input_summary: string | null;
  shot_count: number | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface ReforgeBeat {
  scene?: string;
  summary?: string;
  action?: string;
  camera?: string;
  atmosphere?: string;
  [k: string]: unknown;
}

export interface ReforgeBeats {
  title?: string;
  style?: string;
  characters?: Array<{ name: string; gender?: string; age?: string; appearance?: string }>;
  scenes?: Array<{ name: string; setting?: string }>;
  beats?: ReforgeBeat[];
}

export interface ReforgeChapter {
  index: number;
  heading: string;
  beat_count?: number;
  shot_count?: number;
  md_file?: string;
  platform_parts?: Record<string, string>;
  beats?: ReforgeBeats;
}

export interface PromptReforgeDetail extends PromptReforgeTask {
  input_text: string;
  output_md: string;
  report: {
    beats?: ReforgeBeats;
    chapters?: ReforgeChapter[];
    chapter_count?: number;
    platforms?: string[];
    shot_count?: number;
    platform_parts?: Record<string, string>;
  };
  file_tree: ReforgeFileNode[];
}

export const listPromptReforgeTasks = async (): Promise<PromptReforgeTask[]> => {
  const res = await api.get<PromptReforgeTask[]>('/prompt-reforge/tasks');
  return res.data ?? [];
};

export const getPromptReforgeTask = async (id: string): Promise<PromptReforgeDetail> => {
  const res = await api.get<PromptReforgeDetail>(`/prompt-reforge/tasks/${id}`);
  return res.data ?? ({} as PromptReforgeDetail);
};

export const createPromptReforgeTask = async (params: {
  title?: string;
  input_type?: string;
  director_model?: string;
  target_platforms?: string;
  input_text?: string;
  file?: File;
}): Promise<{ task_id: string; message: string }> => {
  const form = new FormData();
  if (params.title) form.append('title', params.title);
  if (params.input_type) form.append('input_type', params.input_type);
  if (params.director_model) form.append('director_model', params.director_model);
  if (params.target_platforms) form.append('target_platforms', params.target_platforms);
  if (params.input_text) form.append('input_text', params.input_text);
  if (params.file) form.append('file', params.file);
  const res = await api.upload<{ task_id: string; message: string }>('/prompt-reforge/tasks', form);
  return (res.data ?? { task_id: '', message: res.message || '' }) as { task_id: string; message: string };
};

export const listDirectorModels = async (): Promise<DirectorModel[]> => {
  const res = await api.get<DirectorModel[]>('/prompt-reforge/director-models');
  return res.data ?? [];
};

export const reforgeFileUrl = (taskId: string, path: string): string =>
  `${API_BASE}/prompt-reforge/tasks/${taskId}/files/${path}`;
