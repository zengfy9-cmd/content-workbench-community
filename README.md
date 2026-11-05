# 内容工作台 · 社区空白版

给自己的内容建立一个本机工作台：收集素材、研究对标、整理选题、写稿批注、管理配图，再按需连接平台。Windows 安装后打开浏览器使用，数据存放在自己电脑。

基于 [Gary（曹淦）的 content-workbench](https://github.com/garycaogan/content-workbench)（上游适配起点 `c3792f7e3821b02cc689c83415f9213a4f6538ff`），包含 Windows 适配及后续工作流扩展。保留原 MIT 许可证。此仓库从当前适配代码导出独立快照，不携带个人安装的 Git 历史、数据或配置。

公众号排版使用本版自带的五款通用配色，保留排版与预览功能；未分发未标明开源许可证的第三方主题原文件。

## 下载并交给 WorkBuddy

[下载 v1.0.1 ZIP](https://github.com/zengfy9-cmd/content-workbench-community/releases/download/v1.0.1/content-workbench-community-v1.0.1.zip) · [完整安装口令](docs/WORKBUDDY.md)

解压 ZIP，在 WorkBuddy 中选择解压后的目录，粘贴：

> 请按当前目录的 AGENTS.md、README.md 和 docs/WORKBUDDY.md 帮我安装内容工作台。先检查 Windows、uv、Node.js 和端口，再在当前目录运行 deploy/install-windows.ps1。使用全新的本机私有数据目录，不复制其他安装的内容和账号。装好后验证能新建、保存、刷新与重启后读回稿件，以及上传图片；给我可点击的网址和以后打开的方法。安装验收不调用 AI、不连接平台、不发布。需要我补充的信息一次问齐；失败时说明具体步骤与错误，不把未通过的功能说成已完成。

## 功能与接通边界

| 功能 | 开箱条件 |
|---|---|
| 素材、选题、稿件、分组、批注、图片与版本管理 | 安装后本机使用 |
| 写稿时并排参考多篇原文、链接批量导入与视频素材入口 | 提供自己的内容；网页登录、反爬和访问权限以平台实际结果为准 |
| 外部对话生成后归档正文与图片 | 本机归档接口和脚本保留，需明确确认本篇内容 |
| 正文→整组配图预览→选择编号→独立配图 | 需要自行安装并登录兼容的 Codex CLI；型号和生图能力受账户权限限制，未接通会报错，不保证免费 |
| AI 拆解、写作、改稿，DeepSeek 官方接口入口 | 自行选择、授权模型并承担相应费用；新安装默认禁用文字模型 |
| 飞书、公众号、X、知识星球、小红书及视频模块 | 代码入口保留，需要自己的账号、凭据及可用依赖；没有替新用户完成平台端到端验收 |

自动飞书采集与同步默认关闭。基础安装无需配置任何模型密钥。工作台不是平台官方客户端，接口和网页变化可能导致外部功能失效。

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

在配图页面填写自己的风格要求。需要固定角色时，将自己有权使用的 PNG/JPG/WEBP 参考图放进私有目录下 `article-images/references`，最多 5 张。不附带原安装者的角色、成品图或专属提示词。没有参考图也可按正文与补充要求设计。CLI 型号与权限不匹配时保留真实错误，由用户选择处理方式。

## 开发与验证

```powershell
uv sync --frozen
uv run pytest -q
cd web
npm.cmd ci
npm.cmd run build
```

源码不包含 .venv、node_modules、构建产物、数据库或截图。完整回归使用临时数据目录；平台投递、真实模型生成和真实账号授权需要另行验收。见 [脱敏说明](docs/PRIVACY.md)。
