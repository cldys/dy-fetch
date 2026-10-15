"""
抖音下载器 —— FastAPI 后端

启动:
    python main.py
    uvicorn main:app --host 0.0.0.0 --port 8800 --reload
"""

import asyncio
import json
import logging
import os
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core import download_all, safe_name

# ── 配置 ──────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = BASE_DIR / "settings.json"
DEFAULT_DOWNLOADS_DIR = Path.home() / "Downloads" / "DyFetch"


def normalize_directory(value: str) -> Path:
    path = Path(value.strip()).expanduser()
    if not value.strip() or not path.is_absolute():
        raise ValueError("请输入绝对路径，或以 ~/ 开头的路径")
    return path.resolve()


def load_downloads_dir() -> Path:
    try:
        settings = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return normalize_directory(settings["downloads_dir"])
    except FileNotFoundError:
        pass
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError):
        logging.warning("Unable to load download settings; using the default directory")
    return normalize_directory(os.getenv("DOWNLOADS_DIR") or str(DEFAULT_DOWNLOADS_DIR))


DOWNLOADS_DIR = load_downloads_dir()

app = FastAPI(title="抖音下载器", version="1.0.0")

# 静态文件
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


# ── 任务管理（内存） ──────────────────────────────────────────────────────────

class Task:
    def __init__(self, task_id: str, url: str):
        self.id = task_id
        self.url = url
        self.downloads_dir = DOWNLOADS_DIR
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
            task.downloads_dir,
            download_video=options.get("video", False),
            download_audio=options.get("audio", True),
            download_cover=options.get("cover", False),
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


async def _run_batch(batch: list[Task], options: dict):
    for task in batch:
        await _run_download(task, options)


# ── API 模型 ──────────────────────────────────────────────────────────────────

class DownloadOptions(BaseModel):
    video: bool = False
    audio: bool = True
    cover: bool = False
    save_json: bool = False
    timeout: int = 30


class DownloadRequest(DownloadOptions):
    url: str


class BatchDownloadRequest(DownloadOptions):
    urls: list[str]


class SettingsRequest(BaseModel):
    downloads_dir: str


class TaskResponse(BaseModel):
    id: str
    url: str
    status: str
    progress: Optional[str] = None
    error: Optional[str] = None
    files: dict[str, dict] = {}


# ── 首页 ──────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    return (BASE_DIR / "static" / "index.html").read_text(encoding="utf-8")


# ── API ───────────────────────────────────────────────────────────────────────

@app.get("/api/settings")
async def api_settings():
    return {"downloads_dir": str(DOWNLOADS_DIR)}


@app.put("/api/settings")
async def api_save_settings(req: SettingsRequest):
    global DOWNLOADS_DIR
    temporary_path = None
    try:
        directory = normalize_directory(req.downloads_dir)
        directory.mkdir(parents=True, exist_ok=True)
        # Check actual write access before persisting the new location.
        with tempfile.TemporaryFile(dir=directory):
            pass
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=BASE_DIR,
                                         prefix=".settings-", suffix=".tmp", delete=False) as f:
            temporary_path = Path(f.name)
            json.dump({"downloads_dir": str(directory)}, f, ensure_ascii=False, indent=2)
            f.write("\n")
        temporary_path.replace(SETTINGS_FILE)
    except (OSError, ValueError, RuntimeError) as e:
        raise HTTPException(400, f"无法保存目录：{e}") from e
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    DOWNLOADS_DIR = directory
    return {"downloads_dir": str(DOWNLOADS_DIR)}


@app.get("/api/directories")
async def api_directories(path: Optional[str] = None):
    """浏览本机文件夹，供保存位置选择器使用。"""
    try:
        directory = normalize_directory(path) if path is not None else DOWNLOADS_DIR
        if path is None:
            while not directory.exists() and directory != directory.parent:
                directory = directory.parent
        children = sorted(
            (child.name for child in directory.iterdir()
             if not child.name.startswith(".") and child.is_dir()),
            key=str.casefold,
        )
    except (OSError, ValueError, RuntimeError) as e:
        raise HTTPException(400, f"无法打开目录：{e}") from e
    return {
        "path": str(directory),
        "parent": str(directory.parent),
        "home": str(Path.home()),
        "directories": children,
    }


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


@app.post("/api/download/batch", response_model=list[TaskResponse])
async def api_download_batch(req: BatchDownloadRequest):
    """按顺序下载多个链接，每个任务独立记录结果。"""
    urls = [url.strip() for url in req.urls if url.strip()]
    if not urls:
        raise HTTPException(400, "请至少输入一个抖音视频链接")
    batch = [Task(uuid.uuid4().hex[:8], url) for url in urls]
    for task in batch:
        tasks[task.id] = task
    asyncio.create_task(_run_batch(batch, req.model_dump(exclude={"urls"})))
    return [
        TaskResponse(id=task.id, url=task.url, status=task.status, progress=task.progress)
        for task in batch
    ]


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
    folder_name = safe_name(meta.get("desc", "") or meta.get("aweme_id", "") or task_id)
    file_path = task.downloads_dir / folder_name / filename
    if not file_path.exists():
        # try finding the file by crawling the directory
        for f in (task.downloads_dir / folder_name).iterdir() if (task.downloads_dir / folder_name).exists() else []:
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
