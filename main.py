"""
抖音下载器 —— FastAPI 后端

启动:
    python main.py
    uvicorn main:app --host 0.0.0.0 --port 8800 --reload
"""

import asyncio
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core import download_all, safe_name

# ── 配置 ──────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent
DOWNLOADS_DIR = Path.home() / "Downloads" / "douyin"
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="抖音下载器", version="1.0.0")

# 静态文件
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


# ── 任务管理（内存） ──────────────────────────────────────────────────────────

class Task:
    def __init__(self, task_id: str, url: str):
        self.id = task_id
        self.url = url
        self.status: str = "queued"
        self.progress: str = "等待中..."
        self.result: Optional[dict] = None
        self.error: Optional[str] = None
        self.files: dict[str, dict] = {}


tasks: dict[str, Task] = {}


def _progress_callback(task: Task):
    def cb(step: str, detail: str):
        if step == "fetch":
            task.status = "fetching"
        elif step == "download":
            task.status = "downloading"
        elif step == "done":
            task.status = "done"
        elif step == "error":
            task.status = "error"
            task.error = detail
        task.progress = detail
    return cb


async def _run_download(task: Task, options: dict):
    try:
        result = await download_all(
            task.url,
            DOWNLOADS_DIR,
            download_video=options.get("video", True),
            download_audio=options.get("audio", True),
            download_cover=options.get("cover", True),
            save_json=options.get("save_json", False),
            timeout=options.get("timeout", 30),
            progress_callback=_progress_callback(task),
        )
        task.result = result
        if result["success"]:
            task.status = "done"
            task.files = result.get("files", {})
            task.progress = "下载完成！"
        else:
            task.status = "error"
            task.error = result.get("error", "未知错误")
            task.progress = task.error
    except Exception as e:
        task.status = "error"
        task.error = str(e)
        task.progress = str(e)


# ── API 模型 ──────────────────────────────────────────────────────────────────

class DownloadRequest(BaseModel):
    url: str
    video: bool = True
    audio: bool = True
    cover: bool = True
    save_json: bool = False
    timeout: int = 30


class TaskResponse(BaseModel):
    id: str
    url: str
    status: str
    progress: str
    error: Optional[str] = None
    files: dict = {}


# ── 首页 ──────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    return (BASE_DIR / "static" / "index.html").read_text(encoding="utf-8")


# ── API ───────────────────────────────────────────────────────────────────────

@app.post("/api/download", response_model=TaskResponse)
async def api_download(req: DownloadRequest):
    """提交下载任务"""
    task_id = uuid.uuid4().hex[:8]
    task = Task(task_id, req.url)
    tasks[task_id] = task

    options = {
        "video": req.video,
        "audio": req.audio,
        "cover": req.cover,
        "save_json": req.save_json,
        "timeout": req.timeout,
    }

    asyncio.create_task(_run_download(task, options))
    return TaskResponse(id=task.id, url=task.url, status=task.status, progress=task.progress)


@app.get("/api/task/{task_id}", response_model=TaskResponse)
async def api_task(task_id: str):
    """查询任务状态"""
    task = tasks.get(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return TaskResponse(
        id=task.id, url=task.url, status=task.status,
        progress=task.progress, error=task.error, files=task.files,
    )


@app.get("/api/file/{task_id}/{filename}")
async def api_file(task_id: str, filename: str):
    """下载已完成的文件"""
    task = tasks.get(task_id)
    if not task or not task.result:
        raise HTTPException(404, "任务不存在")
    meta = task.result.get("meta") or {}
    folder_name = safe_name(meta.get("desc", "") or task_id)
    file_path = DOWNLOADS_DIR / folder_name / filename
    if not file_path.exists():
        # try finding the file by crawling the directory
        for f in (DOWNLOADS_DIR / folder_name).iterdir() if (DOWNLOADS_DIR / folder_name).exists() else []:
            if f.name == filename:
                file_path = f
                break
    if not file_path.exists():
        raise HTTPException(404, f"文件 {filename} 不存在")
    return FileResponse(file_path, filename=filename)


@app.get("/api/history")
async def api_history():
    """获取最近的任务列表"""
    return [
        {"id": t.id, "url": t.url, "status": t.status, "progress": t.progress}
        for t in list(tasks.values())[-20:]
    ]


# ── 启动 ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8800, reload=True)
