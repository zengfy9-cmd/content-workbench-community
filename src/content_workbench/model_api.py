"""Use the owner's existing approved Claude provider, without copying credentials."""
import base64
import importlib.util
import json
import re
from pathlib import Path

import httpx

from .config import Settings, data_dir


def connector(settings: Settings):
    path = Path(settings.model_connector_path)
    if not path.is_file() or path.suffix != ".py":
        raise RuntimeError("尚未配置本机模型连接器")
    spec = importlib.util.spec_from_file_location("cw_owner_model_connector", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def status(settings: Settings) -> dict:
    try:
        module = connector(settings)
        module.claude_provider(module.read_credentials())
        model = module.MODELS[settings.llm_model]
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


def run(settings: Settings, prompt: str, timeout: int, allow_read: bool = False, image_paths: list[Path] | None = None) -> str:
    module = connector(settings)
    fields = module.read_credentials()
    endpoint, key = module.claude_provider(fields)
    model = module.MODELS.get(settings.llm_model)
    if not model:
        raise RuntimeError("未配置指定型号，不会自动换模型")
    headers = {"anthropic-version": "2023-06-01", "Content-Type": "application/json"}
    if endpoint == "https://api.anthropic.com/v1":
        headers["x-api-key"] = key
    else:
        headers["Authorization"] = "Bearer " + key
    payload = {"model": model, "max_tokens": 8192, "stream": False,
               "messages": [{"role": "user", "content": content(prompt, allow_read, image_paths)}]}
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            response = client.post(endpoint + "/messages", headers=headers, json=payload)
        if response.status_code != 200:
            raise RuntimeError(f"模型服务返回 HTTP {response.status_code}；未重试或更换型号")
        data = response.json()
    except (httpx.HTTPError, ValueError):
        raise RuntimeError("模型连接失败或返回格式异常；未自动重试") from None
    if data.get("stop_reason") == "max_tokens":
        raise RuntimeError("回复超过长度限制，已停止保存不完整稿件，请缩小本次修改范围")
    result = "\n".join(b["text"] for b in data.get("content", []) if b.get("type") == "text")
    if not result.strip():
        raise RuntimeError("模型未返回文字")
    return module.redact(result, fields)
