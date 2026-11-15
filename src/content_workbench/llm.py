"""Run the owner's logged-in Claude Code CLI headless.

All context goes into the prompt over stdin; the CLI runs from the private data
directory so no project instruction files are picked up, and tool use is denied.
This calls Anthropic's service under the author's own subscription — it is not
offline inference, and the UI says so."""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from .config import Settings, data_dir
from .platform_tools import command


class LlmUnavailable(RuntimeError):
    pass


class ModelBackendUnavailable(LlmUnavailable):
    """A failed text-model call, eligible for a separate task-scoped consent."""
    def __init__(self, message: str, *, backend: str, model: str, task: str):
        super().__init__(message)
        self.backend, self.model, self.task = backend, model, task


def claude_bin() -> str:
    explicit = os.environ.get("CW_CLAUDE_BIN")
    if explicit:
        return explicit
    found = shutil.which("claude")
    if found:
        return found
    bundles = []
    for path in glob.glob(str(Path.home() / "Library/Application Support/Claude/claude-code/*/*/claude.app/Contents/MacOS/claude")):
        version = Path(path).parts[-6]
        if re.fullmatch(r"\d+(\.\d+)*", version):
            bundles.append((tuple(int(p) for p in version.split(".")), path))
    if bundles:
        return max(bundles)[1]
    raise LlmUnavailable("没找到 Claude Code 命令行")


def _env() -> dict:
    env = dict(os.environ)
    token_file = Settings.load().claude_token_file
    if token_file and Path(token_file).is_file():
        env["CLAUDE_CODE_OAUTH_TOKEN"] = Path(token_file).read_text(encoding="utf-8").strip()
    return env


DENY = "Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,NotebookEdit,Task,Agent"

# Read-only 生财 MCP tools for finding cases (深海圈 posts, 精华, project cases,
# 航海 material). The MCP server and its login come from the owner's Claude Code
# user config; only these tools are allowed, nothing that writes.
SCYS_TOOLS = [f"mcp__scys-mcp__{name}" for name in (
    "searchDeepseaPublicContent", "getDeepseaContentDetail", "contentSearch", "searchTopic", "topicDetail",
    "searchProjectCases", "projectLibDetail", "searchTrustedContent", "getTrustedContentSection",
    "searchNavigatorContent", "getNavigatorContentDetail", "searchDeepseaCourseChapters")]


def task_settings(settings: Settings, task: str) -> Settings:
    if task == "write" and settings.writing_model:
        from dataclasses import replace
        if settings.writing_model == "deepseek-v4-pro":
            return replace(settings, llm_backend="deepseek-api", writing_model="")
        if settings.writing_model != "claude-opus-5-5":
            raise LlmUnavailable("未配置指定写稿型号，不会自动替换")
        return replace(settings, llm_backend="model-api", llm_model=settings.writing_model)
    return settings


WRITING_MODELS = ("claude-opus-5-5", "deepseek-v4-pro")


def writing_models(settings: Settings) -> dict:
    from dataclasses import replace
    from . import deepseek_api, model_api
    claude = model_api.status(task_settings(replace(settings, writing_model=WRITING_MODELS[0]), "write"))
    deepseek = deepseek_api.status()
    default = settings.writing_model if settings.writing_model in WRITING_MODELS else (
        WRITING_MODELS[1] if settings.llm_backend == "deepseek-api" else WRITING_MODELS[0])
    return {"default": default, "models": [
        {"id": WRITING_MODELS[0], "label": "Claude Opus 5.5 · 代兔", "available": claude["ok"], "detail": claude["detail"]},
        {"id": WRITING_MODELS[1], "label": "DeepSeek V4 Pro · 官方 API", "available": deepseek["ok"], "detail": deepseek["detail"]},
    ]}


def run(prompt: str, timeout: int = 600, tools: list[str] | None = None, max_turns: int = 2, allow_read: bool = False, image_paths: list[Path] | None = None, *, task: str = "write", json_output: bool = False, configuration: Settings | None = None, cancelled=None) -> str:
    """allow_read lets the model open files we put in its working directory (the
    video planner looks at the article's screenshots that way); nothing else changes."""
    settings = task_settings(configuration or Settings.load(), task)
    if settings.llm_backend == 'disabled':
        raise ModelBackendUnavailable('文字 AI 已暂停；本机 Codex 当前仅用于正文配图。已有正文可继续手动编辑。', backend='disabled', model='', task=task)
    if settings.llm_backend == 'codex-cli':
        if tools or allow_read or image_paths:
            raise LlmUnavailable('Codex 订阅接入当前支持文字任务；此功能需要图片或外部工具，请先在 Codex 桌面对话完成并归档')
        from . import codex_native
        try:
            return codex_native.run(prompt,model=settings.codex_model,timeout=timeout,cancelled=cancelled)
        except (codex_native.CodexError, OSError, subprocess.SubprocessError) as exc:
            message = str(exc) if isinstance(exc, codex_native.CodexError) else '本机 Codex 无法启动，请检查 CLI 安装和登录'
            if (cancelled and cancelled()) or any(word in message for word in ('已暂停','已停止')):
                raise LlmUnavailable(message) from None
            raise ModelBackendUnavailable(message, backend='codex-cli', model=settings.codex_model, task=task) from None
    if settings.llm_backend in ("model-api", "deepseek-api"):
        if any(t != "Read" for t in (tools or [])):
            raise LlmUnavailable("独立工作台的生财工具连接尚未配置；可先在 Codex 检索并将来源放入稿件，或配置原版 Claude Code MCP 后使用该后端")
        try:
            if settings.llm_backend == "deepseek-api":
                from . import deepseek_api
                return deepseek_api.run(prompt, timeout, task, json_output=json_output,
                                        allow_read=allow_read, image_paths=image_paths, cancelled=cancelled)
            from . import model_api
            return model_api.run(settings, prompt, timeout, allow_read, image_paths, **({"json_output": True} if json_output else {}))
        except Exception as exc:
            if isinstance(exc, RuntimeError):
                if settings.llm_backend == 'model-api' and not (cancelled and cancelled()):
                    raise ModelBackendUnavailable(str(exc), backend='model-api', model=settings.llm_model, task=task) from None
                raise LlmUnavailable(str(exc)) from None
            raise LlmUnavailable("模型调用失败，请检查本机连接器；未自动更换型号") from None
    if settings.llm_backend != "claude-code":
        raise LlmUnavailable("未配置受支持的模型接入方式；未自动更换服务")
    deny = ",".join(t for t in DENY.split(",") if not (allow_read and t == "Read"))
    binary = [os.environ["CW_CLAUDE_BIN"]] if os.environ.get("CW_CLAUDE_BIN") else command("claude")
    cmd = [*binary, "-p", "--output-format", "json", "--max-turns", str(max_turns), "--disallowedTools", deny]
    if configuration is not None and settings.llm_model:
        cmd += ['--model', settings.llm_model]
    if tools:
        cmd += ["--allowedTools", ",".join(tools)]
    workdir = data_dir() / "llm-cwd"
    workdir.mkdir(exist_ok=True)
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, cwd=workdir, env=_env())
    except subprocess.TimeoutExpired:
        raise LlmUnavailable("模型调用超时") from None
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        raise LlmUnavailable(f"模型没有返回结果：{(proc.stderr or proc.stdout)[:300]}") from None
    result = str(out.get("result", ""))
    if out.get("is_error"):
        if "login" in result.lower() or "logged in" in result.lower():
            raise LlmUnavailable("Claude Code 命令行还没登录，需要在终端里运行一次 claude setup-token 或 claude login")
        raise LlmUnavailable(f"模型调用失败：{result[:300]}")
    return result


def status() -> dict:
    settings = Settings.load()
    if settings.writing_model:
        if settings.writing_model == "deepseek-v4-pro":
            from . import deepseek_api
            return deepseek_api.status()
        from . import model_api
        result = model_api.status(task_settings(settings, "write"))
        result["detail"] = "文章写作：" + result["detail"]
        return result
    if settings.llm_backend == 'disabled':
        return {'ok':False,'detail':'文字 AI 已暂停 · Codex 仅用于配图'}
    if settings.llm_backend == 'codex-cli':
        from . import codex_native
        return codex_native.cached_status()
    if settings.llm_backend == "deepseek-api":
        from . import deepseek_api
        return deepseek_api.status()
    if settings.llm_backend == "model-api":
        from . import model_api
        return model_api.status(settings)
    try:
        binary = claude_bin()
    except LlmUnavailable as exc:
        return {"ok": False, "detail": str(exc)}
    try:
        binary_args = [binary] if os.environ.get("CW_CLAUDE_BIN") else command("claude")
        proc = subprocess.run([*binary_args, "auth", "status"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20, env=_env())
        info = json.loads(proc.stdout or "{}")
        if info.get("loggedIn"):
            return {"ok": True, "detail": f"已登录（{info.get('authMethod', '')}）"}
        return {"ok": False, "detail": "Claude Code 命令行还没登录，需要在终端里运行一次 claude setup-token"}
    except Exception as exc:  # status is advisory only
        return {"ok": False, "detail": f"无法确认登录状态：{exc}"}


def extract_json(text: str):
    """Read the outer object first; article code fences are JSON string content.

    Accept an outer Markdown fence/preamble and literal line breaks in strings.
    Never repair missing quotes/braces or salvage a nested piece of a broken root.
    """
    candidate = text.lstrip("\ufeff \r\n\t")
    fence = re.match(r"^`{3,}(?:json)?[ \t]*\r?\n", candidate, re.I)
    if fence:
        candidate = candidate[fence.end():].lstrip()
    if not candidate.startswith(("{", "[")):
        line = re.search(r"(?m)^[ \t]*(?=[{\[])", candidate)
        if line:
            candidate = candidate[line.end():]
    start = min([i for i in (candidate.find("{"), candidate.find("[")) if i >= 0], default=-1)
    try:
        if start < 0:
            raise ValueError
        return json.JSONDecoder(strict=False).raw_decode(candidate[start:])[0]
    except ValueError:
        raise ValueError("模型返回的 JSON 格式无法读取，请重试；原有内容保持不变") from None
