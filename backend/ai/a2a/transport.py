"""ai.a2a.transport — A2A 的真实 HTTP 传输。

本模块存在的唯一理由
------------------
让 `AuditAgent → LawAgent` 成为**真实的 HTTP request/response**，
而不是 `audit_agent()` 里直接 `law_agent()` 的函数调用。

    禁止：AuditAgent ──(函数调用)──▶ LawAgent      ← 不算 A2A
    必须：AuditAgent ──HTTP POST──▶ /api/a2a/tasks ──▶ LawAgent

依赖说明
-------
用 `httpx`。它**不是新引入的依赖**：本项目 venv 中 httpx 0.28.1 已存在，
由 `chromadb` / `huggingface_hub` / `openai` 传递带入（FastAPI TestClient 也依赖它）。
本轮**不动** requirements 之外的东西，也**不改**现有 LLM 的 HTTP 调用方式
（LLM 仍走 openai SDK，见 `ai/llm_client.py`）。

安全：目标地址白名单（防 SSRF）
-----------------------------
本传输层只允许把任务发往**本机回环**上的 A2A 端点：
* scheme 必须是 http/https；
* host 必须是 localhost / 127.0.0.1 / ::1（或 `A2A_ALLOWED_HOSTS` 显式放行的主机）；
* path 必须正是 `A2A_TASK_PATH`（`/api/a2a/tasks`）。

因此即使调用方影响了目标地址，也无法把本服务变成"任意 URL 请求器"。
"""
from __future__ import annotations

import ipaddress
import logging
import os
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# A2A Task 的接收端点（与 api/a2a.py 的路由一致）
A2A_TASK_PATH = "/api/a2a/tasks"

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


class A2ATransportError(RuntimeError):
    """HTTP 传输失败（连接失败/超时/非 2xx）。"""


def _extra_allowed_hosts() -> set[str]:
    """额外放行主机（逗号分隔）。默认空 —— 即默认只允许本机回环。"""
    raw = os.getenv("A2A_ALLOWED_HOSTS", "")
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def _is_allowed_host(host: str) -> bool:
    host = (host or "").lower()
    if host in _LOOPBACK_HOSTS or host in _extra_allowed_hosts():
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_task_url(url: str) -> str:
    """校验目标 URL 只指向本机回环上的 A2A 端点，返回规范化后的 URL。

    Raises:
        A2ATransportError: scheme/host/path 任一不合规。
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise A2ATransportError(f"A2A 目标地址 scheme 必须是 http/https，收到 {parsed.scheme!r}")
    if not parsed.hostname:
        raise A2ATransportError("A2A 目标地址缺少主机名")
    if not _is_allowed_host(parsed.hostname):
        raise A2ATransportError(
            f"A2A 目标主机 {parsed.hostname!r} 不在白名单内（本阶段仅允许本机回环；"
            f"如需放行请设置环境变量 A2A_ALLOWED_HOSTS）"
        )
    if parsed.path.rstrip("/") != A2A_TASK_PATH:
        raise A2ATransportError(
            f"A2A 目标路径必须是 {A2A_TASK_PATH}，收到 {parsed.path!r}"
        )
    return url


class HttpA2ATransport:
    """把 A2ATask 通过真实 HTTP POST 送到对端 Agent 端点。

    客户端是**短连接、一次性**的：不做连接池复用、不做后台重试队列
    （本轮不引入 worker / 消息队列语义）。
    """

    def __init__(self, base_url: str, *, timeout: float = 30.0):
        if not base_url:
            raise A2ATransportError("A2A base_url 不能为空")
        self.base_url = base_url.rstrip("/")
        self.url = validate_task_url(self.base_url + A2A_TASK_PATH)
        self.timeout = timeout

    def _headers(self, bearer_token: str | None) -> dict:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"
        return headers

    def _unwrap(self, response: "httpx.Response") -> dict:
        """把对端响应拆成 A2AResult payload。

        对端 API 统一信封 `{code, message, data:{a2a_result}}`；
        4xx/5xx 时也应返回 `data.a2a_result`（含稳定错误码），能拆就拆。
        """
        try:
            body = response.json()
        except ValueError:
            raise A2ATransportError(
                f"对端 Agent 返回 {response.status_code} 且响应非 JSON"
            ) from None
        if not isinstance(body, dict):
            raise A2ATransportError("对端 Agent 响应不是 JSON 对象")
        data = body.get("data")
        if isinstance(data, dict) and isinstance(data.get("a2a_result"), dict):
            return data["a2a_result"]
        if response.status_code >= 400:
            raise A2ATransportError(f"对端 Agent 返回 {response.status_code}，响应体不含 a2a_result")
        return body

    def post_task(self, task, *, bearer_token: str | None = None) -> dict:
        """**同步** POST 一个 A2ATask（供客户端侧 Agent 在普通代码里调用）。

        Raises:
            A2ATransportError: 连接失败 / 超时 / 响应非 JSON / 非 2xx 且无法解析。
        """
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(self.url, json=task.to_payload(), headers=self._headers(bearer_token))
        except httpx.HTTPError as exc:
            raise A2ATransportError(
                f"调用对端 Agent 失败（{type(exc).__name__}）：{self.url}"
            ) from exc
        return self._unwrap(response)

    async def post_task_async(self, task, *, bearer_token: str | None = None) -> dict:
        """**异步** POST 一个 A2ATask。

        为什么需要它（不是重复实现）：本阶段演示闭环里，`AuditAgent` 会在
        **本服务的一个 async 端点内部**反向请求本服务的 `/api/a2a/tasks`。
        若在事件循环里做**阻塞** HTTP，事件循环会被这个请求占住，
        内层请求永远得不到处理 —— 自请求直接死锁（实测 ReadTimeout）。
        因此服务端自请求这一跳必须用异步客户端 `await`，让事件循环能并发处理内层请求。
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    self.url, json=task.to_payload(), headers=self._headers(bearer_token)
                )
        except httpx.HTTPError as exc:
            raise A2ATransportError(
                f"调用对端 Agent 失败（{type(exc).__name__}）：{self.url}"
            ) from exc
        return self._unwrap(response)


__all__ = [
    "A2A_TASK_PATH",
    "A2ATransportError",
    "HttpA2ATransport",
    "validate_task_url",
]
