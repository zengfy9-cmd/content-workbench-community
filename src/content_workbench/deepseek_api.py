"""Official DeepSeek API. Credentials stay in the workspace's private store."""
from __future__ import annotations

import base64
import asyncio
import contextlib
from pathlib import Path

import httpx

from .config import SecretStore, data_dir

BASE_URL = "https://api.deepseek.com"
SECRET_KEY = "deepseek_api_key"
ROUTES = {
    "research-basic": {"model": "deepseek-flash", "label": "基础研究"},
    "research-deep": {"model": "deepseek-v4-pro", "label": "深度研究"},
    "classify": {"model": "deepseek-flash", "label": "分类与标签"},
    "analyze": {"model": "deepseek-v4-pro", "label": "拆解与写法研究"},
    "write": {"model": "deepseek-v4-pro", "label": "起草与改稿"},
    "vision": {"model": "deepseek-flash", "label": "带截图的视频脚本"},
}


def routes() -> dict:
    return {task: dict(route) for task, route in ROUTES.items()}


def model_for(task: str) -> str:
    if task not in ROUTES:
        raise RuntimeError("未指定受支持的 DeepSeek 任务用途")
    return ROUTES[task]["model"]


def _key() -> str:
    key = SecretStore().get(SECRET_KEY)
    if not isinstance(key, str) or not key.strip():
        raise RuntimeError("DeepSeek 密钥尚未填写，请在设置中保存官方 API Key")
    key = key.strip()
    if not key.isascii() or any(c.isspace() for c in key) or len(key) > 512:
        raise RuntimeError("DeepSeek 密钥格式不正确，请检查后重新填写")
    return key


def status() -> dict:
    try:
        _key()
        return {"ok": True, "backend": "deepseek-api", "detail": "DeepSeek 官方 API · 已配置，实际连接请点检查连接", "routes": routes()}
    except RuntimeError as exc:
        return {"ok": False, "backend": "deepseek-api", "detail": str(exc), "routes": routes()}


def _request(method: str, path: str, timeout: int, payload: dict | None = None, *, key: str | None = None, cancelled=None) -> dict:
    key = key or _key()
    try:
        # The official endpoint is fixed. No environment proxy or redirects.
        if cancelled is not None:
            async def request():
                async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(timeout, 15)), follow_redirects=False, trust_env=False) as client:
                    pending = asyncio.create_task(client.request(method, BASE_URL + path, headers={"Authorization": "Bearer " + key}, json=payload))
                    try:
                        while not pending.done():
                            if cancelled():
                                raise RuntimeError("拆解已暂停")
                            await asyncio.wait({pending}, timeout=0.15)
                        if cancelled():
                            raise RuntimeError("拆解已暂停")
                        return await pending
                    finally:
                        if not pending.done():
                            pending.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await pending
            response = asyncio.run(request())
        else:
            with httpx.Client(timeout=httpx.Timeout(timeout, connect=min(timeout, 15)), follow_redirects=False, trust_env=False) as client:
                response = client.request(method, BASE_URL + path, headers={"Authorization": "Bearer " + key}, json=payload)
    except httpx.TimeoutException:
        raise RuntimeError("DeepSeek 请求超时，请稍后重试；原有内容保持不变") from None
    except httpx.HTTPError:
        raise RuntimeError("无法连接 DeepSeek 官方 API，请检查网络；未自动换服务") from None
    if response.status_code != 200:
        messages = {
            400: "请求参数或内容不受支持",
            401: "密钥无效，请重新填写官方 API Key",
            402: "账户余额不足，请到 DeepSeek 官方平台充值",
            403: "密钥没有此项权限，请检查官方账户",
            422: "请求格式不受支持",
            429: "请求过于频繁或额度受限，请稍后重试",
            500: "服务暂时异常，请稍后重试",
            503: "服务繁忙，请稍后重试",
        }
        reason = messages.get(response.status_code, "官方接口返回异常")
        # Never include provider bodies/headers, which can echo prompts or keys.
        raise RuntimeError(f"DeepSeek：{reason}（HTTP {response.status_code}）；未自动换服务")
    try:
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError
    except ValueError:
        raise RuntimeError("DeepSeek 返回格式异常，请重试；原有内容保持不变") from None
    return result


def check_connection() -> dict:
    """A non-generating check of credentials and both configured model IDs."""
    try:
        data = _request("GET", "/models", 20)
        available = {item["id"] for item in data.get("data", []) if isinstance(item, dict) and isinstance(item.get("id"), str)}
        missing = sorted({route["model"] for route in ROUTES.values()} - available)
        if missing:
            raise RuntimeError("DeepSeek 当前账户未提供所选型号：" + "、".join(missing))
        return {"ok": True, "detail": "连接检查通过，Flash / Pro 均可用；实际生成以任务结果为准", "routes": routes()}
    except (RuntimeError, TypeError) as exc:
        # RuntimeErrors above contain only our own messages.
        detail = str(exc) if isinstance(exc, RuntimeError) else "DeepSeek 模型列表格式异常"
        return {"ok": False, "detail": detail, "routes": routes()}


def _content(prompt: str, allow_read: bool, image_paths: list[Path] | None) -> str | list[dict]:
    if not image_paths:
        return prompt
    if not allow_read:
        raise RuntimeError("截图任务未授权读取图片")
    root = (data_dir() / "llm-cwd" / "video").resolve()
    content = [{"type": "text", "text": prompt}]
    seen = set()
    for supplied in image_paths:
        path = Path(supplied).resolve()
        if not path.is_relative_to(root) or not path.is_file() or path in seen:
            continue
        if path.stat().st_size > 5_000_000:
            raise RuntimeError("截图过大，请先缩小后重试")
        seen.add(path)
        content.extend([{"type": "text", "text": f"截图文件：{path.name}"},
                        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode("ascii")}}])
    return content


def run(prompt: str, timeout: int, task: str, *, json_output: bool = False,
        allow_read: bool = False, image_paths: list[Path] | None = None, cancelled=None) -> str:
    model = model_for(task)
    if image_paths and task != "vision":
        raise RuntimeError("图片输入必须明确使用 Flash 截图任务")
    payload = {"model": model, "stream": False,
               "max_tokens": 2048 if task == "classify" else 32768,
               "thinking": {"type": "disabled" if task == "classify" else "enabled"},
               "messages": [{"role": "user", "content": _content(prompt, allow_read, image_paths)}]}
    if task != "classify":
        payload["reasoning_effort"] = "high"
    if json_output:
        payload["response_format"] = {"type": "json_object"}
        payload["messages"].insert(0, {"role": "system", "content": "只输出符合用户要求的合法 JSON 对象，不要 Markdown 代码围栏或解释。"})
    key = _key()
    data = _request("POST", "/chat/completions", timeout, payload, key=key, cancelled=cancelled)
    try:
        choice = data["choices"][0]
        if choice.get("finish_reason") == "length":
            raise RuntimeError("DeepSeek 回复超过长度限制，未保存不完整内容；请缩小本次范围")
        if choice.get("finish_reason") != "stop":
            raise RuntimeError("DeepSeek 未完整生成内容，原有内容保持不变，请重试")
        message = choice["message"]
        result = message.get("content")
        if message.get("tool_calls") or not isinstance(result, str) or not result.strip():
            raise RuntimeError("DeepSeek 未返回可用正文，原有内容保持不变，请重试")
    except (KeyError, IndexError, TypeError, AttributeError):
        raise RuntimeError("DeepSeek 返回格式异常，原有内容保持不变，请重试") from None
    # Only final content is used. Provider reasoning is never stored as prose.
    return result.replace(key, "[密钥已隐藏]")
