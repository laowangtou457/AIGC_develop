/**
 * 视频资源替换 API
 * 接入 ManjuToSplitFrameAndProperty（漫剧抽帧与属性管线）：
 *   上传参考视频 → 镜头切分/ASR/资产抽取/H3提示词导出 → 资产替换再导出 → 生成新视频
 */
import { api, API_BASE } from './index';

export interface VideoAssetSummary {
  duration: number;
  shot_count: number;
  character_count: number;
  scene_count: number;
  prop_count: number;
  dialogue_count: number;
  has_prompts: boolean;
  has_prompt_pack: boolean;
}

export interface VideoShotProgress {
  index: number;
  shot_idx: number;
  status: 'pending' | 'running' | 'succeeded' | 'failed';
  video_path: string | null;
  error: string | null;
  duration: number;
  workflow: string | null;
}

export interface VideoGenSummary {
  total: number;
  done: number;
  ratio: string;
  started_at: string;
  shots: VideoShotProgress[];
  merged: { status: 'pending' | 'running' | 'succeeded' | 'failed'; video_path: string | null; error: string | null };
}

export interface VideoAssetJob {
  id: string;
  video_name: string;
  status: 'running' | 'success' | 'failed';
  stage: string;
  error: string | null;
  video_status: 'idle' | 'running' | 'success' | 'failed';
  summary: VideoAssetSummary;
  created_at: string;
  updated_at: string;
}

export interface VideoAssetJobDetail extends VideoAssetJob {
  video: {
    status: 'idle' | 'running' | 'success' | 'failed';
    stage: string | null;
    error: string | null;
    summary: VideoGenSummary;
  };
  shots: {
    duration: number;
    scene_method: string;
    shots: { id: number; start: number; end: number; keyframes: string[]; dialogue: unknown[] }[];
  } | null;
  storyboard: { shots?: unknown[] } | null;
  assets: {
    characters: { id: string; name: string; ref_images: string[]; source_shots: number[]; ref_time: number }[];
    scenes: { id: string; shot_ids: number[]; ref_images: string[] }[];
    props: unknown[];
  } | null;
  video_info: { duration?: number; width?: number; height?: number; fps?: number } | null;
  file_tree: { path: string; size: number }[];
}

export interface VideoGenStatus {
  video_status: 'idle' | 'running' | 'success' | 'failed';
  video_stage: string | null;
  video_error: string | null;
  summary: VideoGenSummary;
  files: { path: string; size: number }[];
}

/** 产物文件 URL（后端同源静态服务，自带目录穿越防护） */
export function assetFileUrl(jobId: string, relPath: string): string {
  const encoded = relPath.split('/').map(encodeURIComponent).join('/');
  return `${API_BASE}/video-asset/jobs/${jobId}/files/${encoded}`;
}

export function downloadUrl(jobId: string): string {
  return `${API_BASE}/video-asset/jobs/${jobId}/download`;
}

export const videoAssetApi = {
  /** 上传视频并启动分析（后台运行） */
  analyze: (file: File) => {
    const formData = new FormData();
    formData.append('file', file);
    return api.upload<{ job_id: string }>('/video-asset/analyze', formData);
  },

  /** 任务列表 */
  listJobs: () => api.get<VideoAssetJob[]>('/video-asset/jobs'),

  /** 任务详情（产物 JSON 全量） */
  getJob: (id: string) => api.get<VideoAssetJobDetail>(`/video-asset/jobs/${id}`),

  /** 替换资产图（原创形象）→ 自动重跑阶段4 → 重新导出 H3 提示词 */
  swapAsset: (jobId: string, assetType: 'character' | 'scene', assetId: string, file: File) => {
    const formData = new FormData();
    formData.append('asset_type', assetType);
    formData.append('asset_id', assetId);
    formData.append('file', file);
    return api.upload<{ message: string; prompts_url: string }>(
      `/video-asset/jobs/${jobId}/swap-asset`,
      formData,
    );
  },

  /** 生成新视频（替换资产后）：逐镜 H3 提示词 + 资产参考图 → ComfyUI → 合并成片 */
  generateVideo: (jobId: string) =>
    api.post<{ message: string }>(`/video-asset/jobs/${jobId}/generate-video`, {}),

  /** 视频生成状态（逐镜进度 + 合并结果 + new_video 产物） */
  getVideoStatus: (jobId: string) =>
    api.get<VideoGenStatus>(`/video-asset/jobs/${jobId}/video`),
};
