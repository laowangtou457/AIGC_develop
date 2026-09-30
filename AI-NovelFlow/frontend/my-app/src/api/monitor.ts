/**
 * 任务进程监控 API
 */
import { api } from './index';

export interface MonitorServiceComfyUI {
  status: 'ok' | 'offline';
  queue_running: number;
  queue_pending: number;
}

export interface MonitorOllamaModel {
  name: string;
  vram_gb: number;
}

export interface MonitorServiceOllama {
  status: 'ok' | 'offline';
  models: MonitorOllamaModel[];
}

export interface MonitorGPU {
  gpu_usage: number;
  vram_used: number;
  vram_total: number;
  vram_percent: number;
  device_name: string;
  source: 'real' | 'estimated';
}

export interface MonitorTask {
  id: string;
  name: string;
  type: string;
  status: string;
  progress: number;
  current_step: string | null;
  error_message?: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface MonitorStalledTask extends MonitorTask {
  minutes_since_update: number;
}

export interface MonitorLLMPending {
  id: string;
  task_type: string | null;
  model: string;
  created_at: string | null;
  minutes_pending: number;
  timeout: boolean;
}

export interface MonitorAlert {
  level: 'critical' | 'warning';
  type: string;
  message: string;
}

export interface SystemMonitor {
  status: string;
  generated_at: string;
  services: {
    comfyui: MonitorServiceComfyUI;
    ollama: MonitorServiceOllama;
  };
  gpu: MonitorGPU;
  tasks: {
    total: number;
    running: number;
    pending: number;
    failed: number;
    completed: number;
    cancelled: number;
    stalled: MonitorStalledTask[];
    recent: MonitorTask[];
  };
  llm: {
    pending_count: number;
    pending: MonitorLLMPending[];
  };
  alerts: MonitorAlert[];
}

export const monitorApi = {
  /** 获取系统监控聚合数据 */
  fetchMonitor: () => api.get<SystemMonitor>('/health/system/monitor'),

  /** 释放显存（ComfyUI 模型缓存 + Ollama 驻留） */
  freeVram: () => api.post<{ comfyui: string; ollama: string; details: string[]; vram_free_gb?: number }>('/health/system/free-vram'),
};
