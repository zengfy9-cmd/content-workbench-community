# catcat.内容创造工作台

**从一条灵感，到一篇准备好发布的作品。**

一个运行在自己电脑上的内容创作工作台。收集素材、研究对标、整理选题、写作批注、管理配图，让内容和创作过程集中在一个地方。

[快速安装](#快速安装) · [Agent 安装指南](docs/AGENT_INSTALL.md) · [WorkBuddy 安装](docs/WORKBUDDY.md) · [版本下载](https://github.com/zengfy9-cmd/content-workbench-community/releases) · [问题反馈](https://github.com/zengfy9-cmd/content-workbench-community/issues)

[![Windows release installation](https://github.com/zengfy9-cmd/content-workbench-community/actions/workflows/windows-install.yml/badge.svg?event=release)](https://github.com/zengfy9-cmd/content-workbench-community/actions/workflows/windows-install.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

## 可以做什么

| 创作环节 | 工作台提供的功能 |
|---|---|
| 收集素材 | 保存文字、链接和录音素材，按需连接飞书 |
| 研究对标 | 阅读原文、整理拆解，与写作内容并排参考 |
| 整理选题 | 将材料和观点组织成选题，保留引用来源 |
| 写作与修改 | 编辑正文、添加批注、管理版本，按需连接 AI 辅助起草 |
| 制作配图 | 从正文生成编号预览，选定后制作单张图片；支持自带角色参考 |
| 排版与归档 | 五款内置通用配色、正文预览，保存文章与配图 |
| 平台连接 | 按需配置公众号、X、小红书等平台入口 |

**本机存储，按需接入。** 基础素材、稿件和图片管理无需模型密钥。AI 写作、配图、语音转写与平台连接分别配置；可用能力和费用取决于你选择的服务及账号。新安装默认关闭文字模型与飞书自动同步。

## 快速安装

目前提供 **Windows 10/11** 安装流程。选择下面任意一种方式即可：

### 方式一：交给你常用的 Agent

[下载 v1.0.2 ZIP](https://github.com/zengfy9-cmd/content-workbench-community/releases/download/v1.0.2/content-workbench-community-v1.0.2.zip)，解压到独立目录，再用支持本机文件读写与终端执行的 Agent 打开它。

| 工具 | 从哪里开始 |
|---|---|
| **WorkBuddy** | 选择解压目录，复制[WorkBuddy 逐步安装指令](docs/WORKBUDDY.md) |
| **Codex** | 将解压目录作为项目打开，在该项目聊天中粘贴下方指令 |
| **Claude Code** | 在解压目录打开终端，运行已安装的 `claude`，粘贴下方指令 |
| **Cursor** | 打开解压目录，使用支持执行终端命令的 Agent 模式，粘贴下方指令 |
| **其他本机 Agent** | 将工作目录设为解压目录，并允许其读取文件、执行安装命令，再粘贴下方指令 |

Agent 本身需要先安装并能正常使用。以上工具复用同一 Windows 安装脚本；只有聊天能力、无法访问本机文件或执行命令的工具，请改用下方手动安装。

复制给 Agent：

```text
请帮我安装当前目录的 catcat.内容创造工作台。
先阅读 AGENTS.md、README.md 和 docs/AGENT_INSTALL.md。
检查 Windows、uv、Node.js、Python 3.13 安装能力和端口，
然后运行 deploy/install-windows.ps1。
使用本次安装独立的私有数据目录。
装好后验证临时稿件和图片的保存、刷新与重启读回，
只清理本次验收创建的临时内容。
给我可点击的网址、日后启动方法和数据目录。
先完成基础安装；需要 AI 或平台功能时，再由我选择服务和连接自己的账号。
```

详细步骤与排错见 [通用 Agent 安装指南](docs/AGENT_INSTALL.md)。

### 方式二：手动运行安装脚本

准备好 uv 和 Node.js 后，在解压目录打开 PowerShell：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy\install-windows.ps1
```

安装完成后访问 **http://localhost:8765**。详细依赖、数据目录和端口说明见下方。

> v1.0.1 的 Windows ZIP 安装流程已完成云端验收，包含保存、重启读回与页面加载。[运行记录](https://github.com/zengfy9-cmd/content-workbench-community/actions/runs/41991087689) · [验收范围](docs/CLOUD_VALIDATION.md)。其他 Agent 入口共用安装脚本，未逐一进行客户端实机验收。

## 第一次使用

1. 在素材库保存一份自己的文字或链接。
2. 整理一个选题，创建稿件并添加图片。
3. 在编辑器中修改、批注和预览正文。
4. 需要 AI 写作、配图或平台功能时，在对应入口配置自己的服务。

## Windows 手动安装

需要 Windows 10/11、[uv](https://docs.astral.sh/uv/getting-started/installation/) 和 [Node.js LTS](https://nodejs.org/)。uv 按锁文件安装 Python 3.13 与项目依赖。网络需可访问 Python、npm 下载源。

视频配音预览、音视频处理和完整回归测试还需要 FFmpeg（同时提供 ffmpeg、ffprobe 命令）。Windows 可参考[构建提供方的安装说明](https://www.gyan.dev/ffmpeg/builds/)，使用 `winget install --id Gyan.FFmpeg.Essentials -e` 后重开终端；基础文字与图片管理不依赖它。生图、转写与视频渲染仍需各自额外配置，本次安装不会调用付费模型或渲染视频。

在解压目录运行 PowerShell：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy\install-windows.ps1
```

安装后运行 `deploy/start-windows.ps1` 再次打开。默认网址 http://localhost:8765，仅监听本机。关闭网页不会停止后台服务，重新打开不会重复启动。停止服务可在任务管理器中核对该工作台的 Python 进程后结束；未安装自动启动任务。

默认私有目录为 `%LOCALAPPDATA%\ContentWorkbenchCommunity\default`。可在启动前设置 `CW_DATA_DIR` 指向自己的新目录。目录包含数据库、图片、设置与账号信息，请自己备份，不要上传 GitHub。首次空库采用本机登录，本电脑其他本地用户可能访问；跨设备访问需另行配置身份验证，此安装流程不做公网部署。

若 8765 已被其他服务占用，请先选一个空闲端口；在新私有目录的 settings.json 中同步修改 port、origins、allowed_hosts 后再启动。不要把别人的服务当成本工作台验收。

## 自定义配图

在配图页面填写自己的风格要求。需要固定角色时，将自己有权使用的 PNG/JPG/WEBP 参考图放进私有目录下 `article-images/references`，最多 5 张。没有参考图也可按正文与补充要求设计。CLI 型号与权限不匹配时保留真实错误，由用户选择处理方式。

## 开发与验证

```powershell
uv sync --frozen
uv run pytest -q
cd web
npm.cmd ci
npm.cmd run build
```

源码不包含 .venv、node_modules、构建产物、数据库或截图。完整回归使用临时数据目录；平台投递、真实模型生成和真实账号授权需要另行验收。见 [数据与隐私](docs/PRIVACY.md)。

## 许可证

本项目采用 [MIT License](LICENSE)。

本次 v1.0.2 修复段末斜杠、手动对齐被主题覆盖，并新增可复制的提示词块。使用方式见 [排版与提示词](docs/EDITOR_LAYOUT.md)。新版本云端验收结果以 Actions 对应运行记录为准。
