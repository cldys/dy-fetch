# 语音转文字 — Web 手动选择方案

## 使用场景

Windows 台式机（RTX 5070 12GB），启动一个本地 Web 服务。
打开浏览器 → 选择文件夹 → 点开始 → 该文件夹下的视频/音频自动生成字幕。

```
浏览器打开 http://localhost:8800
   │
   ├── 输入文件夹路径（如 D:\videos\）
   ├── 点击「扫描」，列出该目录下所有音视频文件
   ├── 勾选要识别的文件（默认全选）
   ├── 点击「开始识别」
   │
   ▼
  faster-whisper 逐个处理
  在每个文件同目录生成:
    video.mp4 → video.srt + video.txt
```

---

## 技术栈

| 层 | 选型 | 理由 |
|----|------|------|
| 后端框架 | FastAPI | 轻量、异步、和已有项目一致 |
| Web 服务器 | uvicorn | FastAPI 标配 |
| GPU 推理 | faster-whisper large-v3 FP16 | 中文最佳、12GB 够用 |
| 音频处理 | ffmpeg | 视频提取音频、格式转换 |
| 前端 | 纯 HTML + JS | 零依赖、无需 node |
| 目录访问 | 用户手动输入路径 | 浏览器无法直接选本地文件夹，用输入框最可靠 |

---

## 项目结构

```
whisper-web/
├── main.py              # FastAPI 入口 + 路由
├── transcriber.py       # faster-whisper 封装
├── requirements.txt
├── static/
│   └── index.html       # Web UI
└── README.md
```

---

## API 设计

### `POST /api/scan`

扫描指定目录，列出可识别的文件。

```json
// 请求
{ "path": "D:\\videos" }

// 响应
{
  "path": "D:\\videos",
  "files": [
    { "name": "视频1.mp4", "size_mb": 45.3, "ready": false },
    { "name": "音频1.m4a", "size_mb": 3.2, "ready": false },
    { "name": "视频2.mkv", "size_mb": 120.0, "ready": true }
  ]
}
```

`ready: true` 表示已有 `.srt` 文件（之前处理过）。

### `POST /api/transcribe`

对指定文件执行识别。

```json
// 请求
{ "path": "D:\\videos", "files": ["视频1.mp4", "音频1.m4a"] }

// 响应（逐个文件返回进度，SSE 流式推送）
event: progress
data: {"file": "视频1.mp4", "status": "extracting_audio"}

event: progress
data: {"file": "视频1.mp4", "status": "transcribing", "pct": 45}

event: progress
data: {"file": "视频1.mp4", "status": "done", "output": "视频1.srt"}

event: done
data: {"processed": 2, "failed": 0}
```

前端实时显示每个文件的进度条。

---

## 前端页面布局

```
┌─────────────────────────────────────────────┐
│  🎙 语音转文字                                │
│  whisper large-v3 · RTX 5070                │
├─────────────────────────────────────────────┤
│                                             │
│  文件夹路径: [________________] [扫描]        │
│                                             │
│  ┌─ 已扫描: D:\videos\ ──────────────────┐  │
│  │ ☑ 视频1.mp4        45.3 MB   ✅ 已有字幕│  │
│  │ ☑ 音频1.m4a         3.2 MB            │  │
│  │ ☑ 视频2.mkv       120.0 MB            │  │
│  │                                        │  │
│  │ [全选] [开始识别]                        │  │
│  └────────────────────────────────────────┘  │
│                                             │
│  进度:                                       │
│  ████████████░░░░░░ 视频1.mp4  67%          │
│  ██░░░░░░░░░░░░░░░░ 视频2.mkv  12%          │
│                                             │
└─────────────────────────────────────────────┘
```

---

## 显存

```
RTX 5070 12 GB
├── Windows 桌面    ~1 GB
├── faster-whisper  ~6 GB  (启动时加载一次)
└── 空闲            ~5 GB
```

---

## 处理流程（单个文件）

```
视频文件 (.mp4/.mkv)
  │
  ├─ ffmpeg 提取音频 → 临时 .wav (16kHz mono)
  │
  ├─ faster-whisper 推理 → segments[]
  │
  ├─ 生成 .srt (带时间轴)
  ├─ 生成 .txt (纯文本)
  │
  └─ 删掉临时 .wav
```

音频文件 (.m4a/.mp3/.wav) 跳过提取，直接转码后推理。

---

## Windows 部署

```powershell
# 1. 创建环境
conda create -n whisper-web python=3.11
conda activate whisper-web

# 2. PyTorch (CUDA 12.4)
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124

# 3. 其他依赖
pip install faster-whisper fastapi uvicorn sse-starlette

# 4. 安装 ffmpeg
# 从 https://ffmpeg.org/download.html 下载 Windows 版
# 解压后把 bin 目录加入系统 PATH

# 5. 启动
python main.py

# 6. 浏览器打开
# http://localhost:8800
```

---

## 关键设计决策

| 决策 | 理由 |
|------|------|
| 前端 SSE 推流 | 不用 WebSocket，更简单，实时显示进度 |
| 用户手动输入路径 | 浏览器安全策略限制，无法直接选文件夹 |
| 模型启动加载一次 | 不反复加载卸载，节省时间和显存抖动 |
| 同目录输出 | .srt/.txt 紧挨着原文件，方便查找 |
| 纯 HTML 前端 | 零构建工具、零 node_modules |
