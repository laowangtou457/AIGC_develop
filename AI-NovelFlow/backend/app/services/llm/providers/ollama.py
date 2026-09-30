"""
Ollama 提供商

支持 Ollama 本地模型服务。
"""
import httpx
import os
import re
import time
from typing import Dict, Any, Optional, List
from ..base import BaseLLMProvider, LLMConfig, LLMResponse, create_llm_log, update_llm_log, build_llm_request_info


class OllamaProvider(BaseLLMProvider):
    """
    Ollama 提供商

    支持 Ollama 本地模型服务。
    """

    PROVIDER_NAME = "ollama"

    def _get_endpoint(self) -> str:
        """获取 API 端点 URL

        使用 Ollama 原生 /api/chat 端点：
        - qwen3 等思考模型在 OpenAI 兼容端点（/v1/chat/completions）无法关闭思考
          （think 字段被忽略），思考会吞掉 num_predict 预算导致正文为空/跑题；
        - 原生端点支持 think:false 与 format:json，JSON 结构化任务可靠。
        """
        base = self.config.api_url.rstrip("/")
        base = re.sub(r'/v1/?$', '', base)  # api_url 可能带 /v1 后缀
        return f"{base}/api/chat"

    def _get_headers(self) -> Dict[str, str]:
        """获取请求头"""
        headers = {
            "Content-Type": "application/json"
        }
        # Ollama 可以不需要 API Key，但如果配置了也可以使用
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    def _get_proxy_config(self) -> Optional[str]:
        """Ollama 通常是本地服务，不需要代理"""
        return None

    def _build_request_body(
        self,
        system_prompt: str,
        user_content: str,
        temperature: float,
        max_tokens: int,
        response_format: Optional[str]
    ) -> Dict[str, Any]:
        """构建请求体（Ollama 原生 /api/chat 格式）

        - think: false：关闭 qwen3 思考模型的 reasoning 输出。
          思考模式会先输出大段思考再输出正文，token 预算被思考耗尽后
          正文为空或被截断（OpenAI 兼容端点无法关闭，必须用原生端点）。
        - format: json：仅 JSON 任务开启，强制模型输出合法 JSON。
        - options.num_predict：限制输出长度。
        """
        body = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
            "think": False,
            # 模型常驻 30 分钟：避免 5 分钟不活跃卸载后每次重载 18GB 模型
            "keep_alive": "30m",
        }
        if response_format == "json_object":
            body["format"] = "json"
        return body

    def _parse_response(self, response_data: Dict[str, Any]) -> str:
        """解析响应（Ollama 原生 /api/chat 格式）

        返回 message.content 正文。思考已通过 think:false 关闭，
        正文即最终输出；不把 reasoning 当作正文返回。
        """
        if isinstance(response_data, dict):
            message = response_data.get("message") or {}
            if isinstance(message, dict):
                return message.get("content", "")
        return ""

    async def chat_completion(
        self,
        system_prompt: str,
        user_content: str,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        response_format: Optional[str] = None,
        task_type: str = None,
        prompt_template_name: str = None,
        novel_id: str = None,
        chapter_id: str = None,
        character_id: str = None
    ) -> LLMResponse:
        """
        发送对话请求

        Args:
            system_prompt: 系统提示词
            user_content: 用户内容
            temperature: 温度参数
            max_tokens: 最大 token 数
            response_format: 响应格式
            task_type: 任务类型
            novel_id: 小说 ID
            chapter_id: 章节 ID
            character_id: 角色 ID

        Returns:
            LLMResponse 对象
        """
        start_time = time.time()
        endpoint = self._get_endpoint()
        headers = self._get_headers()
        body = self._build_request_body(
            system_prompt, user_content, temperature, max_tokens, response_format
        )

        # Ollama 不需要代理
        old_http_proxy = os.environ.pop('HTTP_PROXY', None)
        old_https_proxy = os.environ.pop('HTTPS_PROXY', None)
        old_http_proxy_lower = os.environ.pop('http_proxy', None)
        old_https_proxy_lower = os.environ.pop('https_proxy', None)

        transport = httpx.AsyncHTTPTransport(proxy=None)
        used_proxy = False
        timeout = self.config.timeout or 300.0
        client = httpx.AsyncClient(transport=transport, timeout=timeout)
        request_info = build_llm_request_info(
            provider=self.config.provider,
            base_url=self.config.api_url,
            endpoint=endpoint,
            model=self.config.model,
            headers=headers,
            payload=body,
            proxy_url=None,
            timeout_seconds=timeout,
        )

        log_id = None
        try:
            async with client:
                log_id = create_llm_log(
                    provider=self.config.provider,
                    model=self.config.model,
                    system_prompt=system_prompt,
                    user_prompt=user_content,
                    prompt_template_name=prompt_template_name,
                    task_type=task_type,
                    novel_id=novel_id,
                    chapter_id=chapter_id,
                    character_id=character_id,
                    used_proxy=used_proxy,
                    request_info=request_info,
                )
                response = await client.post(
                    endpoint,
                    headers=headers,
                    json=body,
                    timeout=timeout
                )

            # 恢复环境变量
            if old_http_proxy:
                os.environ['HTTP_PROXY'] = old_http_proxy
            if old_https_proxy:
                os.environ['HTTPS_PROXY'] = old_https_proxy
            if old_http_proxy_lower:
                os.environ['http_proxy'] = old_http_proxy_lower
            if old_https_proxy_lower:
                os.environ['https_proxy'] = old_https_proxy_lower

            duration = time.time() - start_time

            if response.status_code == 200:
                data = response.json()
                content = self._parse_response(data)

                update_llm_log(
                    log_id=log_id,
                    response=content,
                    status="success",
                    duration=duration,
                )

                return LLMResponse(
                    success=True,
                    content=content,
                    raw_response=data,
                    duration=duration
                )
            else:
                error_msg = f"API 错误 ({response.status_code}): {response.text}"
                update_llm_log(
                    log_id=log_id,
                    status="error",
                    error_message=error_msg,
                    duration=duration,
                )

                return LLMResponse(
                    success=False,
                    error=error_msg,
                    duration=duration
                )
        except Exception as e:
            import traceback
            error_type = type(e).__name__
            error_detail = str(e) if str(e) else "(无详细错误信息)"
            error_msg = f"请求异常：[{error_type}] {error_detail}"
            print(f"[OllamaProvider] {error_msg}")
            traceback.print_exc()

            # 恢复环境变量
            if old_http_proxy:
                os.environ['HTTP_PROXY'] = old_http_proxy
            if old_https_proxy:
                os.environ['HTTPS_PROXY'] = old_https_proxy
            if old_http_proxy_lower:
                os.environ['http_proxy'] = old_http_proxy_lower
            if old_https_proxy_lower:
                os.environ['https_proxy'] = old_https_proxy_lower

            duration = time.time() - start_time
            update_llm_log(
                log_id=log_id,
                status="error",
                error_message=error_msg,
                duration=duration,
            )

            return LLMResponse(
                success=False,
                error=error_msg,
                duration=duration
            )

    async def get_models(self) -> List[str]:
        """
        获取 Ollama 可用的模型列表

        Returns:
            模型名称列表
        """
        if not self.config.api_url:
            return []

        try:
            # 尝试 Ollama API
            ollama_url = re.sub(r'/v1/?$', '', self.config.api_url) + "/api/tags"
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    ollama_url,
                    headers=self._get_headers()
                )

                if response.status_code == 200:
                    data = response.json()
                    return [m.get("name") or m.get("model") for m in data.get("models", [])]
        except Exception:
            pass
        return []
