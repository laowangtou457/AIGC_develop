/**
 * 视频资源替换 API
 * 接入 ManjuToSplitFrameAndProperty（漫剧抽帧与属性管线）：
 *   上传参考视频 → 镜头切分/ASR/资产抽取/H3提示词导出 → 资产替换再导出 → 打包下载
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

export interface VideoAssetJob {
  id: string;
  video_name: string;
  status: 'running' | 'success' | 'failed';
  stage: string;
  error: string | null;
  summary: VideoAssetSummary;
  created_at: string;
  updated_at: string;
}

export interface VideoAssetJobDetail extends VideoAssetJob {
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
};
