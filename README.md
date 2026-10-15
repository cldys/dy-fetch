# 抖音下载器

无水印下载抖音视频、音频、封面和元数据 —— 基于 Playwright 浏览器自动化，稳定绕过 API 签名验证。

## 技术原理

抖音 `aweme/v1/web/aweme/detail` 接口需要客户端动态生成 `a_bogus` 和 `x-secsdk-web-signature` 签名，yt-dlp 等工具无法直接调用。本项目通过 Playwright 启动真实 Chromium 浏览器打开视频页面，拦截 JS 发出的 API 响应，直接获取完整数据。

## 快速开始

```bash
# 1. 安装依赖
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. 安装浏览器（仅首次，约 100MB）
playwright install chromium

# 3. 启动服务
python main.py
```

浏览器打开 **http://localhost:8800**，粘贴抖音链接即可下载。

## 使用方式

### Web 界面

启动后在页面输入框粘贴抖音链接，勾选需要的资源（视频 / 音频 / 封面 / JSON 数据），点击「开始下载」。

默认保存到 `~/Downloads/DyFetch`。点击「选择文件夹」可浏览本机文件夹，也可输入绝对路径或 `~/` 开头的路径，点击「保存位置」后生效（不存在的目录会自动创建）。配置保存在项目根目录的 `settings.json`，刷新页面或重启服务后会恢复上次保存的位置；已有任务仍使用提交时的目录。

支持的 URL 格式：
- `https://www.douyin.com/video/7653450635035118882`
- `https://www.douyin.com/jingxuan?modal_id=7653450635035118882`

### API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/download` | 提交下载任务 |
| `GET` | `/api/task/{task_id}` | 查询任务状态与进度 |
| `GET` | `/api/file/{task_id}/{filename}` | 下载已完成的文件 |
| `GET` | `/api/history` | 查看最近任务列表 |
| `GET` | `/api/settings` | 获取当前保存位置 |
| `PUT` | `/api/settings` | 保存位置，JSON 字段为 `downloads_dir` |
| `GET` | `/api/directories` | 浏览文件夹，可传入 `path` 参数 |

#### POST /api/download

```json
{
  "url": "https://www.douyin.com/video/7653450635035118882",
  "video": true,
  "audio": true,
  "cover": true,
  "save_json": false,
  "timeout": 30
}
```

返回：

```json
{
  "id": "a1b2c3d4",
  "status": "queued",
  "progress": "等待中..."
}
```

轮询 `GET /api/task/a1b2c3d4`，`status` 变为 `done` 后，可调用 `/api/file/{task_id}/video.mp4` 等接口下载。

## 下载文件

下载完成后文件默认保存在 `~/Downloads/DyFetch/<标题>/` 目录，可在网页中选择其他保存位置。未保存过位置时，也可通过环境变量 `DOWNLOADS_DIR` 指定初始目录；网页保存的设置优先。

| 文件 | 说明 |
|------|------|
| `video.mp4` | 无水印视频（最高画质） |
| `audio.m4a` | 原声音频 |
| `cover.jpeg` | 封面图 |
| `info.txt` | 标题、作者、统计数据 |
| `api_response.json` | 原始 API 响应（需勾选该选项） |

## 项目结构

```
dy-dl/
├── main.py              # FastAPI 入口，路由 & 任务管理
├── core.py              # 核心下载逻辑（Playwright 拦截 + 数据提取）
├── requirements.txt     # Python 依赖
├── static/
│   └── index.html       # Web 前端（单页，暗色主题）
└── settings.json        # 首次保存位置后生成，不纳入版本控制
```

## 配置

| 配置项 | 位置 | 默认值 |
|--------|------|--------|
| 服务端口 | `main.py` 底部 `uvicorn.run()` | `8800` |
| 下载目录 | 网页「保存位置」；未保存时读取环境变量 `DOWNLOADS_DIR` | `~/Downloads/DyFetch/` |
| 请求超时 | API 参数 `timeout` | `30` 秒 |

## 依赖

- Python ≥ 3.11
- FastAPI + Uvicorn（Web 服务）
- Playwright + Chromium（浏览器自动化）
- Requests（文件下载）

## 后续计划

- [ ] SQLite 持久化任务历史
- [ ] 批量 URL 导入
- [ ] 音频格式转换（mp3）
- [ ] 用户主页批量下载
- [ ] Cookie 登录态支持（私密视频）
