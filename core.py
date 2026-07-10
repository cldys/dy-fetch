"""
核心下载模块 —— 复用 Playwright 拦截 API 响应，提取并下载抖音视频。
"""

import asyncio
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from playwright.async_api import async_playwright


# ── URL 解析 ──────────────────────────────────────────────────────────────────

def normalize_url(url: str) -> str:
    """支持 /video/<id>、/jingxuan?modal_id=<id> 等格式。"""
    m = re.search(r"/video/(\d+)", url)
    if m:
        return f"https://www.douyin.com/video/{m.group(1)}"
    m = re.search(r"modal_id=(\d+)", url)
    if m:
        return f"https://www.douyin.com/video/{m.group(1)}"
    raise ValueError(f"无法从 URL 提取视频 ID: {url}")


def safe_name(text: str, max_len: int = 60) -> str:
    cleaned = "".join(c if c.isalnum() or c in " ._-" else "_" for c in (text or "unknown"))
    cleaned = re.sub(r"_+", "_", cleaned).strip("_ ")
    return cleaned[:max_len] or "unknown"


# ── API 拦截 ──────────────────────────────────────────────────────────────────

async def fetch_aweme(url: str, timeout: int = 30) -> Optional[dict]:
    """启动 headless Chromium，拦截 aweme/detail API 响应。"""
    captured = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
        )
        page = await context.new_page()

        async def on_response(response):
            if "aweme/v1/web/aweme/detail" in response.url and "aweme_detail" not in captured:
                try:
                    body = await response.json()
                    captured["aweme_detail"] = body
                except Exception:
                    pass

        page.on("response", on_response)

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            for _ in range(timeout * 2):
                if captured:
                    break
                await asyncio.sleep(0.5)
            if not captured:
                await page.evaluate("window.scrollTo(0, 300)")
                await asyncio.sleep(2)
        except Exception:
            pass
        finally:
            await browser.close()

    if not captured:
        return None

    body = captured["aweme_detail"]
    return (body.get("aweme_detail") or body) if isinstance(body, dict) else None


# ── 数据提取 ──────────────────────────────────────────────────────────────────

def extract_meta(aweme: dict) -> dict:
    """从 aweme 提取元信息。"""
    author = aweme.get("author") or {}
    video = aweme.get("video") or {}
    stats = aweme.get("statistics") or {}
    music = aweme.get("music") or {}
    return {
        "aweme_id": aweme.get("aweme_id", ""),
        "desc": (aweme.get("desc") or "").strip(),
        "author_name": author.get("nickname", "?"),
        "author_uid": author.get("uid", ""),
        "duration_ms": video.get("duration", 0),
        "create_time": aweme.get("create_time", 0),
        "digg_count": stats.get("digg_count", 0),
        "comment_count": stats.get("comment_count", 0),
        "share_count": stats.get("share_count", 0),
        "collect_count": stats.get("collect_count", 0),
        "music_title": music.get("title", "") or music.get("author", ""),
    }


def extract_video_url(aweme: dict) -> Optional[str]:
    video = aweme.get("video") or {}
    for k in ("play_addr_h264", "play_addr", "download_addr"):
        addr = video.get(k) or {}
        urls = addr.get("url_list", [])
        if urls:
            return urls[0]
    return None


def extract_audio_url(aweme: dict) -> Optional[str]:
    music = aweme.get("music") or {}
    play_url = music.get("play_url") or {}
    urls = play_url.get("url_list", [])
    return urls[0] if urls else None


def extract_cover_url(aweme: dict) -> Optional[str]:
    video = aweme.get("video") or {}
    for k in ("cover", "origin_cover", "dynamic_cover"):
        c = video.get(k) or {}
        urls = c.get("url_list", [])
        if urls:
            return urls[0]
    return None


# ── 文件下载 ──────────────────────────────────────────────────────────────────

def _download(url: str, dest: Path) -> tuple[bool, int]:
    """下载文件到 dest，返回 (成功, 字节数)。"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Referer": "https://www.douyin.com/",
    }
    try:
        r = requests.get(url, headers=headers, stream=True, timeout=120)
        r.raise_for_status()
        total = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(65536):
                f.write(chunk)
                total += len(chunk)
        return True, total
    except Exception:
        return False, 0


async def download_all(
    url: str,
    output_dir: Path,
    *,
    download_video: bool = True,
    download_audio: bool = True,
    download_cover: bool = True,
    save_json: bool = False,
    timeout: int = 30,
    progress_callback=None,
) -> dict:
    """
    完整下载流程。

    progress_callback(step, detail): 报告进度
       step: "fetch" | "download" | "done" | "error"
       detail: 描述字符串或进度百分比
    """
    result = {"success": False, "aweme_id": None, "files": {}, "meta": None, "error": None}

    # 1. 解析 URL
    try:
        normalized = normalize_url(url)
    except ValueError as e:
        result["error"] = str(e)
        if progress_callback:
            progress_callback("error", str(e))
        return result

    # 2. 获取数据
    if progress_callback:
        progress_callback("fetch", "正在打开抖音页面...")
    aweme = await fetch_aweme(normalized, timeout=timeout)
    if not aweme:
        result["error"] = "无法获取视频数据，API 响应为空"
        if progress_callback:
            progress_callback("error", result["error"])
        return result

    meta = extract_meta(aweme)
    result["meta"] = meta
    result["aweme_id"] = meta["aweme_id"]
    folder = output_dir / safe_name(meta["desc"] or meta["aweme_id"])
    folder.mkdir(parents=True, exist_ok=True)

    # 3. 保存元数据
    info = (
        f"标题: {meta['desc'] or '(无)'}\n"
        f"作者: {meta['author_name']} (UID: {meta['author_uid']})\n"
        f"时长: {meta['duration_ms'] / 1000:.1f} 秒\n"
        f"发布时间: {datetime.fromtimestamp(meta['create_time']).strftime('%Y-%m-%d %H:%M:%S') if meta['create_time'] else '?'}\n"
        f"链接: {normalized}\n"
        f"\n点赞: {meta['digg_count']}  评论: {meta['comment_count']}  分享: {meta['share_count']}\n"
        f"原声: {meta['music_title']}\n"
    )
    (folder / "info.txt").write_text(info, encoding="utf-8")

    if save_json:
        (folder / "api_response.json").write_text(json.dumps(aweme, ensure_ascii=False, indent=2), encoding="utf-8")

    # 4. 下载各资源
    tasks = []
    if download_cover:
        tasks.append(("封面", extract_cover_url(aweme), folder / "cover.jpeg"))
    if download_audio:
        tasks.append(("音频", extract_audio_url(aweme), folder / "audio.m4a"))
    if download_video:
        tasks.append(("视频", extract_video_url(aweme), folder / "video.mp4"))

    total_items = len(tasks)
    for i, (label, dl_url, dest) in enumerate(tasks):
        if not dl_url:
            continue
        if progress_callback:
            progress_callback("download", f"正在下载{label} ({i + 1}/{total_items})...")
        ok, size = _download(dl_url, dest)
        if ok:
            result["files"][label] = {"path": str(dest), "size": size}
        else:
            result["files"][label] = {"path": None, "size": 0, "error": "下载失败"}

    result["success"] = bool(result["files"].get("视频") or result["files"].get("音频"))
    if progress_callback:
        progress_callback("done", f"完成！{folder}")
    return result
