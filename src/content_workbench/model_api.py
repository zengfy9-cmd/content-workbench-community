"""Use the owner's existing approved Claude provider, without copying credentials."""
import base64
import importlib.util
import json
import re
from pathlib import Path

import httpx

from .config import Settings, SecretStore, data_dir

DAITU_SECRET='daitu_api_key'


class BuiltinDaitu:
    """Portable approved provider; no Windows connector path is needed in cloud."""
    MODELS={'sonnet':'claude-sonnet-4-6','opus':'claude-opus-4-6'}
    @staticmethod
    def read_credentials():
        key=SecretStore().get(DAITU_SECRET)
        if not isinstance(key,str) or not key or len(key)>512 or not key.isascii() or any(c.isspace() for c in key):
            raise RuntimeError('代兔密钥尚未配置')
        return {'claude':key}
    @staticmethod
    def claude_provider(fields):return 'https://api.daitu.cc/v1',fields['claude']
    @staticmethod
    def redact(text,fields):return text.replace(fields['claude'],'[密钥已隐藏]')


def connector(settings: Settings):
    if settings.model_connector_path=='builtin:daitu':return BuiltinDaitu
    path = Path(settings.model_connector_path)
    if not path.is_file() or path.suffix != ".py":
        raise RuntimeError("尚未配置本机模型连接器")
    spec = importlib.util.spec_from_file_location("cw_owner_model_connector", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def selected_model(settings, module):
    if settings.llm_model == "claude-opus-5-5":
        endpoint, _ = module.claude_provider(module.read_credentials())
        if endpoint != "https://api.daitu.cc/v1":
            raise RuntimeError("Opus 5.5 写稿已指定代兔渠道，请检查对应连接器")
        return settings.llm_model
    model = module.MODELS.get(settings.llm_model)
    if not model:
        raise RuntimeError("未配置指定型号，不会自动换模型")
    return model


def status(settings: Settings) -> dict:
    try:
        module = connector(settings)
        module.claude_provider(module.read_credentials())
        model = selected_model(settings, module)
        return {"ok": True, "detail": f"{model} · 已配置，实际生成以任务结果为准", "backend": "model-api"}
    except Exception:
        return {"ok": False, "detail": "模型连接器或凭据未配置，请检查本机接入", "backend": "model-api"}


def content(prompt: str, allow_read: bool, image_paths: list[Path] | None = None) -> list[dict]:
    blocks = [{"type": "text", "text": prompt}]
    if not allow_read:
        return blocks
    # Paths come from video._pictures, never from model/user prose.
    root = (data_dir() / "llm-cwd" / "video").resolve()
    seen = set()
    for supplied in image_paths or []:
        path = Path(supplied).resolve()
        if not path.is_relative_to(root) or not path.is_file() or path in seen:
            continue
        if path.stat().st_size > 5_000_000:
            raise RuntimeError("截图过大，请先缩小后重试")
        seen.add(path)
        blocks.extend([{"type": "text", "text": f"截图文件：{path.name}"},
                       {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                        "data": base64.b64encode(path.read_bytes()).decode("ascii")}}])
    return blocks


def run(settings: Settings, prompt: str, timeout: int, allow_read: bool = False, image_paths: list[Path] | None = None, *, json_output: bool = False) -> str:
    module = connector(settings)
    fields = module.read_credentials()
    endpoint, key = module.claude_provider(fields)
    model = selected_model(settings, module)
    headers = {"anthropic-version": "2023-06-01", "Content-Type": "application/json"}
    if endpoint == "https://api.anthropic.com/v1":
        headers["x-api-key"] = key
    else:
        headers["Authorization"] = "Bearer " + key
    payload = {"model": model, "max_tokens": 8192, "stream": False,
               "messages": [{"role": "user", "content": content(prompt, allow_read, image_paths)}]}
    if json_output:
        # The compatible third-party Messages endpoint does not promise native
        # schema support. Use supported system guidance plus local validation.
        payload["system"] = ('只返回一个完整合法的 JSON 对象，不加说明或外层代码围栏。'
                             '长正文放入 body 字符串，换行用 \\n，双引号用 \\" 转义；'
                             '正文内的 Markdown 和 ```prompt 代码块必须完整保留在 body 字符串中。')
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            response = client.post(endpoint + "/messages", headers=headers, json=payload)
        if response.status_code != 200:
            try:
                error = response.json().get("error", {})
                quota = any(word in str(error).lower() for word in ("insufficient_quota", "insufficient credit", "insufficient balance", "余额不足", "额度不足"))
            except (ValueError, AttributeError):
                quota = False
            if response.status_code == 402 or quota:
                raise RuntimeError(f"Claude 接口额度或余额不足（HTTP {response.status_code}）；可改选 DeepSeek 后重试，原有内容保持不变")
            raise RuntimeError(f"模型服务返回 HTTP {response.status_code}；未重试或更换型号")
        data = response.json()
    except (httpx.HTTPError, ValueError):
        raise RuntimeError("模型连接失败或返回格式异常；未自动重试") from None
    if not isinstance(data, dict):
        raise RuntimeError("模型服务返回格式异常；原有内容保持不变")
    if settings.llm_model == "claude-opus-5-5" and data.get("model") != model:
        raise RuntimeError("服务返回的型号与指定的 Opus 5.5 不符，已停止保存；未自动换模型")
    if data.get("stop_reason") == "max_tokens":
        raise RuntimeError("回复超过长度限制，已停止保存不完整稿件，请缩小本次修改范围")
    result = "\n".join(b["text"] for b in data.get("content", []) if b.get("type") == "text")
    if not result.strip():
        raise RuntimeError("模型未返回文字")
    return module.redact(result, fields)
