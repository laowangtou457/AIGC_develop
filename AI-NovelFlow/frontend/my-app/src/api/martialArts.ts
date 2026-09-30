/**
 * 武术指导 API（独立工作流）
 * 参考: https://github.com/CY-CHENYUE/martial-arts-director-cy (Apache-2.0)
 * 独立于小说→分镜→视频主流程，单独调用 /api/martial-arts/*
 * 生成扩展：文生图（海报）/ 图生图（变体/尾帧）/ 图生视频（ref2va / 首尾帧）
 */
import { api, API_BASE } from './index';

/**
 * 把 ComfyUI /view 产物 URL 转成平台同源代理地址。
 * 背景：浏览器加载 <img>/<video> 自动带 Referer，ComfyUI /view 对跨源 Referer 返回 403；
 * 统一走后端 /api/martial-arts/media-proxy 转发，保证前端可展示、可下载。
 */
export function toMediaUrl(url: string): string {
  if (!url) return '';
  if (url.startsWith('/api/')) return url; // 已是代理地址
  try {
    const u = new URL(url);
    const params = u.searchParams;
    const q = new URLSearchParams();
    const f = params.get('filename') || '';
    if (!f) return url;
    q.set('filename', f);
    const sub = params.get('subfolder') || '';
    if (sub) q.set('subfolder', sub);
    q.set('type', params.get('type') || 'output');
    return `${API_BASE}/martial-arts/media-proxy?${q.toString()}`;
  } catch {
    return url;
  }
}

/** 用户显式定义的一个角色（多角色时保证每个角色形象稳定进锚定卡） */
export interface MartialCharacterDef {
  name: string;       // 角色名（如：太极宗师）
  role?: string;      // 身份：主角 / 对手 / 配角
  gender?: string;    // 性别：男 / 女 / 中性
  age?: string;       // 年龄：少年 / 青年 / 中年 / 老年 或具体年龄
  physique?: string;  // 体型与气质
  clothing?: string;  // 服装（材质+颜色+款式）
  weapon?: string;    // 兵器（类型，可"徒手"）
  martial?: string;   // 武术体系
}

export interface MartialArtsRequest {
  requirement: string;        // 一句话需求（必填）
  character?: string;         // 人物/体型气质
  system_style?: string;      // 武术体系
  tone?: string;              // 风格调性
  equipment?: string;         // 装备/服装
  scene?: string;             // 场景
  grid?: string;              // 宫格规格（16=4×4 默认）
  weapon?: string;            // 是否带兵器
  reference?: string;         // 影视参考
  reference_assets?: string;  // 参考图解析资产（qwen2.5vl 解析结果，并入编排输入）
  characters?: MartialCharacterDef[]; // 用户指定多角色定义（人物清点以此为准）
}

export interface MartialArtsResult {
  characterCard: string;      // 人物锚定卡
  imagePrompt: string;        // 图片提示词（多宫格海报）
  videoPrompt: string;        // 视频提示词（图生视频）
  h3Prompt?: string;          // 实际执行用 H3 英文模板（逐分镜独立镜头块，生成视频后回填）
  raw: string;                // 完整原始输出
  historyId?: string;         // 历史任务 id（generate 成功后自动创建并返回）
}

/** 历史任务条目（列表/详情通用） */
export interface MartialArtsHistoryItem {
  id: string;
  requirement: string;
  optionsJson: string;
  characterCard: string;
  imagePrompt: string;
  videoPrompt: string;
  h3Prompt?: string;
  raw: string;
  characterImageUrl: string;
  storyboardImageUrl: string;
  videoUrl: string;
  createdAt: string;
  updatedAt: string;
}

export interface GenerateImageRequest {
  prompt: string;             // 角色描述（文生图）
  aspect_ratio?: string;      // 1:1 / 4:3 / 16:9 / 9:16
  history_id?: string;        // 历史任务 id（生成成功后回写角色图 URL）
  extra_prompt?: string;      // 手动附加提示词约束（用户输入框，追加到提示词末尾）
}

export interface EditImageRequest {
  image_url: string;          // ComfyUI /view 链接（角色形象图）
  prompt: string;             // 文字分镜提示词（编辑指令）
  aspect_ratio?: string;
  history_id?: string;        // 历史任务 id（生成成功后回写分镜图 URL）
}

export interface GenerateVideoRequest {
  prompt: string;             // 视频提示词
  image_url: string;          // 首帧/参考图（分镜图）
  second_image_url?: string;  // 尾帧（mode=first_last 时）
  mode?: 'ref2va' | 'first_last' | 'three_frame' | 'four_frame';
  duration_seconds?: number;  // 4 / 6 / 8
  history_id?: string;        // 历史任务 id（生成成功后回写视频 URL）
  image_is_storyboard?: boolean; // 参考图是否为16格分镜海报（后端裁第1格作单帧起点）
  split_segments?: boolean; // 分段拼接：16 拍拆 8+8，两段各 15 秒，段间首帧衔接，ffmpeg 拼 30 秒
}

export interface MartialTaskRecord {
  id: string;
  name: string;
  task_type: string;
  status: 'running' | 'completed' | 'failed';
  stage: string;
  detail: string;
  error: string | null;
  started_at: number;
  finished_at: number | null;
  elapsed_sec: number | null;
  minutes: number;
}

export interface MartialMonitorData {
  running: MartialTaskRecord[];
  recent: MartialTaskRecord[];
}

export const martialArtsApi = {
  generate: (body: MartialArtsRequest) =>
    api.post<MartialArtsResult>('/martial-arts/generate', body),
  generateImage: (body: GenerateImageRequest) =>
    api.post<{ image_url: string; aspect_ratio: string }>('/martial-arts/generate-image', body),
  editImage: (body: EditImageRequest) =>
    api.post<{ image_url: string }>('/martial-arts/edit-image', body),
  generateVideo: (body: GenerateVideoRequest) =>
    api.post<{ video_url: string; mode: string; duration_seconds: number }>('/martial-arts/generate-video', body),

  // ---- 历史任务持久化 ----
  listHistory: () => api.get<MartialArtsHistoryItem[]>('/martial-arts/history'),
  getHistory: (id: string) => api.get<MartialArtsHistoryItem>(`/martial-arts/history/${id}`),
  createHistory: (body: Partial<MartialArtsHistoryItem>) =>
    api.post<MartialArtsHistoryItem>('/martial-arts/history', body),
  updateHistory: (id: string, body: Partial<MartialArtsHistoryItem>) =>
    api.put<MartialArtsHistoryItem>(`/martial-arts/history/${id}`, body),
  deleteHistory: (id: string) =>
    api.delete<{ message: string }>(`/martial-arts/history/${id}`),

  // ---- 武打任务进程监控 ----
  fetchMonitor: () => api.get<MartialMonitorData>('/martial-arts/monitor'),

  // ---- 参考图上传与解析（qwen2.5vl 视觉资产） ----
  uploadReference: (file: File) => {
    const formData = new FormData();
    formData.append('file', file);
    return api.upload<{ image_url: string; file_path: string }>('/martial-arts/upload-reference', formData);
  },
  analyzeReference: (body: { image_url: string; requirement?: string }) =>
    api.post<{ assets: string; raw: string }>('/martial-arts/analyze-reference', body),
};
