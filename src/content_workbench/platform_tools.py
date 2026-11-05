"""Cross-platform executable and font resolution; no shell interpolation."""
import os
import shutil
from pathlib import Path


def command(name: str) -> list[str]:
    executable = shutil.which(name)
    if not executable:
        raise FileNotFoundError(f"未找到 {name}，请先安装对应组件")
    path = Path(executable)
    # npm's Windows launchers require cmd.exe. Invoke their JS entry directly.
    if os.name == "nt" and path.suffix.lower() in {".cmd", ".bat", ".ps1"}:
        entries = {
            "npx": "npm/bin/npx-cli.js",
            "claude": "@anthropic-ai/claude-code/cli.js",
            "codex": "@openai/codex/bin/codex.js",
            "lark-cli": "@larksuite/cli/scripts/run.js",
        }
        entry = path.parent / "node_modules" / entries.get(name, "")
        node = shutil.which("node")
        if node and entry.is_file():
            return [node, str(entry)]
        # Some CLIs ship a native executable beside the npm shim.
        native = path.with_suffix(".exe")
        if native.is_file():
            return [str(native)]
        raise FileNotFoundError(f"{name} 的 Windows 入口尚未配置，请设置可执行文件路径")
    return [str(path)]


def chinese_font(bold: bool = False) -> tuple[str, int]:
    explicit = os.environ.get("CW_FONT_BOLD" if bold else "CW_FONT")
    if explicit:
        if not Path(explicit).is_file():
            raise FileNotFoundError("配置的中文字体不存在")
        return explicit, 0
    fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    candidates = [
        (str(fonts / ("msyhbd.ttc" if bold else "msyh.ttc")), 0),
        ("/System/Library/Fonts/Hiragino Sans GB.ttc", 2 if bold else 0),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else
         "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),
    ]
    for path, index in candidates:
        if Path(path).is_file():
            return path, index
    raise FileNotFoundError("未找到中文字体，请设置 CW_FONT 和 CW_FONT_BOLD")
