from __future__ import annotations

from dataclasses import dataclass
import json
import os
import socket
import ssl
import time
import urllib.error
import urllib.request

from ._ssl import build_ssl_context


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMResponse:
    text: str
    provider: str
    model: str


class LLMClient:
    def __init__(self, provider: str, config: dict):
        self.provider = provider
        self.config = config
        self.model = str(config.get("model") or "")
        self.base_url = str(config.get("base_url") or "")
        self.timeout = int(config.get("timeout_seconds") or 90)
        self.api_type = str(config.get("api_type") or provider)
        key_env = str(config.get("api_key_env") or "")
        self.api_key_env = key_env
        self.api_key = str(config.get("api_key") or "").strip()
        if not self.api_key and key_env:
            self.api_key = os.getenv(key_env, "").strip()
        self.ssl_context = build_ssl_context()

    def generate(self, system: str, prompt: str, max_tokens: int, temperature: float) -> LLMResponse:
        if not self.api_key:
            raise LLMError(f"缺少 API Key，请先在设置页配置 {self.provider}，或在环境变量 {self.api_key_env} 中配置。")
        if not self.model:
            raise LLMError("缺少模型名称，请先在设置页配置默认模型，或在页面的[本次使用模型]中填写。")
        if self.api_type == "openai_chat" or self.provider in {"qwen", "deepseek"}:
            return self._generate_openai(system, prompt, max_tokens, temperature)
        if self.api_type == "anthropic_messages" or self.provider == "claude":
            return self._generate_claude(system, prompt, max_tokens, temperature)
        raise LLMError(f"暂不支持的模型服务：{self.provider}")

    def generate_stream(self, system: str, prompt: str, max_tokens: int, temperature: float):
        """Yield text chunks via SSE streaming. Falls back to batch for unsupported providers."""
        if not self.api_key:
            raise LLMError(f"缺少 API Key，请先在设置页配置 {self.provider}，或在环境变量 {self.api_key_env} 中配置。")
        if not self.model:
            raise LLMError("缺少模型名称，请先在设置页配置默认模型，或在页面的[本次使用模型]中填写。")
        if self.api_type == "openai_chat" or self.provider in {"qwen", "deepseek"}:
            yield from self._stream_openai(system, prompt, max_tokens, temperature)
        elif self.api_type == "anthropic_messages" or self.provider == "claude":
            yield from self._stream_claude(system, prompt, max_tokens, temperature)
        else:
            resp = self.generate(system, prompt, max_tokens, temperature)
            yield resp.text

    def _post_json(self, payload: dict, headers: dict) -> dict:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers = {"User-Agent": "investment-hub-wechat-assistant/1.0", **headers}
        last_exc: Exception | None = None
        for attempt in range(3):
            req = urllib.request.Request(self.base_url, data=data, headers=request_headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout, context=self.ssl_context) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                raise LLMError(self._format_http_error(exc.code, body)) from exc
            except (urllib.error.URLError, ConnectionResetError, TimeoutError, socket.timeout, ssl.SSLError) as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(0.8 * (attempt + 1))
                    continue
                reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
                raise LLMError(f"模型服务连接失败：{reason}。已自动重试 3 次，请检查网络/代理，或切换到其他模型服务。") from exc
        raise LLMError(f"模型服务连接失败：{last_exc}")

    def _format_http_error(self, status_code: int, body: str) -> str:
        message = body[:600]
        service_code = ""
        try:
            parsed = json.loads(body)
            error = parsed.get("error") if isinstance(parsed, dict) else None
            if isinstance(error, dict):
                message = str(error.get("message") or message)
                service_code = str(error.get("code") or error.get("type") or "")
        except json.JSONDecodeError:
            pass

        combined = f"{message} {service_code}".lower()
        context = f"{self.provider} / {self.model}"
        if any(token in combined for token in ("quota", "credit", "balance", "free tier", "insufficient")):
            return f"模型服务额度不足（{context}）：{message}。请充值/开通付费额度，或切换到其他可用模型服务。"
        if status_code in {401, 403} and any(token in combined for token in ("api key", "apikey", "unauthorized", "forbidden")):
            return f"模型服务 API Key 无效或无权限（{context}）：{message}。请到设置页检查密钥。"
        if status_code == 400 and any(token in combined for token in ("model", "not found", "invalid")):
            return f"模型名称或请求参数无效（{context}）：{message}。请到设置页检查模型名称。"
        return f"模型服务请求失败（{context}，HTTP {status_code}）：{message}"

    def _generate_openai(self, system: str, prompt: str, max_tokens: int, temperature: float) -> LLMResponse:
        payload = {
            "model": self.model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        data = self._post_json(payload, {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        })
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"模型服务返回结构无法解析：{json.dumps(data, ensure_ascii=False)[:600]}") from exc
        return LLMResponse(text=text.strip(), provider=self.provider, model=self.model)

    def _generate_claude(self, system: str, prompt: str, max_tokens: int, temperature: float) -> LLMResponse:
        payload = {
            "model": self.model,
            "system": system,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        data = self._post_json(payload, {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        })
        try:
            parts = data["content"]
            text = "\n".join(part.get("text", "") for part in parts if part.get("type") == "text")
        except (KeyError, TypeError) as exc:
            raise LLMError(f"Claude 返回结构无法解析：{json.dumps(data, ensure_ascii=False)[:600]}") from exc
        return LLMResponse(text=text.strip(), provider=self.provider, model=self.model)

    def _stream_openai(self, system: str, prompt: str, max_tokens: int, temperature: float):
        payload = {
            "model": self.model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "investment-hub-wechat-assistant/1.0",
        }
        req = urllib.request.Request(self.base_url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self.ssl_context) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8").strip()
                    if not line.startswith("data: "):
                        continue
                    chunk_str = line[6:]
                    if chunk_str == "[DONE]":
                        return
                    try:
                        chunk_data = json.loads(chunk_str)
                        text = chunk_data["choices"][0]["delta"].get("content", "")
                        if text:
                            yield text
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise LLMError(self._format_http_error(exc.code, body)) from exc
        except (urllib.error.URLError, ConnectionResetError, TimeoutError, socket.timeout, ssl.SSLError) as exc:
            reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
            raise LLMError(f"模型服务连接失败：{reason}") from exc

    def _stream_claude(self, system: str, prompt: str, max_tokens: int, temperature: float):
        payload = {
            "model": self.model,
            "system": system,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            "messages": [{"role": "user", "content": prompt}],
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
            "User-Agent": "investment-hub-wechat-assistant/1.0",
        }
        req = urllib.request.Request(self.base_url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self.ssl_context) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8").strip()
                    if not line.startswith("data: "):
                        continue
                    try:
                        event_data = json.loads(line[6:])
                        if event_data.get("type") == "content_block_delta":
                            text = event_data.get("delta", {}).get("text", "")
                            if text:
                                yield text
                    except (json.JSONDecodeError, KeyError):
                        continue
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise LLMError(self._format_http_error(exc.code, body)) from exc
        except (urllib.error.URLError, ConnectionResetError, TimeoutError, socket.timeout, ssl.SSLError) as exc:
            reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
            raise LLMError(f"模型服务连接失败：{reason}") from exc
