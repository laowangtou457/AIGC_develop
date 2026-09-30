/**
 * API 配置
 * 统一管理 API 基础 URL 和请求配置
 */

export const API_BASE = import.meta.env.VITE_API_URL 
  ? `${import.meta.env.VITE_API_URL}/api` 
  : '/api';

/**
 * 通用请求封装
 */

/** 统一解析响应：非 JSON（如 500 文本 "Internal Server Error"）转为友好错误，不抛 SyntaxError */
type ApiResult<T = unknown> = { success: boolean; data?: T; message?: string };
async function parseResponse<T>(res: Response): Promise<ApiResult<T>> {
  const text = await res.text();
  if (!text) return { success: false, message: `HTTP ${res.status}：服务返回空响应` };
  try {
    return JSON.parse(text) as ApiResult<T>;
  } catch {
    return {
      success: false,
      message: `HTTP ${res.status}${res.ok ? '' : ' 服务异常'}：${text.slice(0, 200)}`,
    };
  }
}

export const api = {
  get: async <T>(url: string): Promise<{ success: boolean; data?: T; message?: string }> => {
    const res = await fetch(`${API_BASE}${url}`);
    return parseResponse<T>(res);
  },

  post: async <T>(url: string, body?: unknown): Promise<{ success: boolean; data?: T; message?: string }> => {
    const res = await fetch(`${API_BASE}${url}`, {
      method: 'POST',
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    return parseResponse<T>(res);
  },

  put: async <T>(url: string, body: unknown): Promise<{ success: boolean; data?: T; message?: string }> => {
    const res = await fetch(`${API_BASE}${url}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    return parseResponse<T>(res);
  },

  delete: async <T>(url: string): Promise<{ success: boolean; data?: T; message?: string }> => {
    const res = await fetch(`${API_BASE}${url}`, { method: 'DELETE' });
    return parseResponse<T>(res);
  },

  upload: async <T>(url: string, formData: FormData): Promise<{ success: boolean; data?: T; message?: string }> => {
    const res = await fetch(`${API_BASE}${url}`, {
      method: 'POST',
      body: formData,
    });
    return parseResponse<T>(res);
  },
};
