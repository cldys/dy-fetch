import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import quote, urlsplit

import main


class SettingsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.default = self.root / "Downloads" / "DyFetch"
        for name, value in {
            "BASE_DIR": self.root,
            "SETTINGS_FILE": self.root / "settings.json",
            "DEFAULT_DOWNLOADS_DIR": self.default,
            "DOWNLOADS_DIR": self.default,
            "tasks": {},
        }.items():
            patcher = patch.object(main, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        environment = patch.dict(os.environ, {"DOWNLOADS_DIR": ""})
        environment.start()
        self.addCleanup(environment.stop)

    async def request(self, method, url, data=None):
        parsed = urlsplit(url)
        messages = []
        body = json.dumps(data).encode() if data is not None else b""

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            messages.append(message)

        await main.app({
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1", "method": method, "scheme": "http",
            "path": parsed.path, "query_string": parsed.query.encode(),
            "headers": [(b"content-type", b"application/json")],
            "client": ("127.0.0.1", 12345), "server": ("localhost", 8800),
        }, receive, send)
        status = next(m["status"] for m in messages if m["type"] == "http.response.start")
        content = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
        return status, content

    async def save(self, path):
        status, content = await self.request("PUT", "/api/settings", {"downloads_dir": str(path)})
        return status, json.loads(content)

    async def test_default_and_initial_environment_directory(self):
        self.assertEqual(main.load_downloads_dir(), self.default)
        with patch.dict(os.environ, {"DOWNLOADS_DIR": str(self.root / "initial")}):
            self.assertEqual(main.load_downloads_dir(), self.root / "initial")
        status, content = await self.request("GET", "/api/settings")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(content)["downloads_dir"], str(self.default))

    async def test_save_creates_directory_and_restores_persisted_choice(self):
        selected = self.root / "保存位置 with spaces" / "videos"
        status, settings = await self.save(selected)
        self.assertEqual(status, 200)
        self.assertTrue(selected.is_dir())
        self.assertEqual(settings, json.loads(main.SETTINGS_FILE.read_text()))
        main.DOWNLOADS_DIR = self.default
        with patch.dict(os.environ, {"DOWNLOADS_DIR": str(self.root / "initial")}):
            main.DOWNLOADS_DIR = main.load_downloads_dir()
        status, content = await self.request("GET", "/api/settings")
        self.assertEqual(json.loads(content)["downloads_dir"], str(selected))

    async def test_home_directory_shorthand(self):
        home_relative = os.path.relpath(self.default, Path.home())
        status, settings = await self.save(f"~/{home_relative}")
        self.assertEqual(status, 200)
        self.assertEqual(settings["downloads_dir"], str(self.default))

    async def test_invalid_or_unwritable_directory_keeps_previous_choice(self):
        await self.save(self.default)
        previous = main.SETTINGS_FILE.read_text()
        existing_file = self.root / "file.txt"
        existing_file.write_text("file")
        for path in ("", "relative/path", str(existing_file)):
            with self.subTest(path=path):
                status, _ = await self.save(path)
                self.assertEqual(status, 400)
                self.assertEqual(main.DOWNLOADS_DIR, self.default)
                self.assertEqual(main.SETTINGS_FILE.read_text(), previous)
        with patch.object(main.tempfile, "TemporaryFile", side_effect=PermissionError("Read-only directory")):
            status, _ = await self.save(self.root / "unwritable")
        self.assertEqual(status, 400)
        self.assertEqual(main.SETTINGS_FILE.read_text(), previous)
        self.assertEqual(main.DOWNLOADS_DIR, self.default)

    async def test_failed_persistence_keeps_previous_choice_and_cleans_tempfile(self):
        await self.save(self.default)
        previous = main.SETTINGS_FILE.read_text()
        with patch.object(Path, "replace", side_effect=PermissionError("Settings are read-only")):
            status, _ = await self.save(self.root / "new")
        self.assertEqual(status, 400)
        self.assertEqual(main.DOWNLOADS_DIR, self.default)
        self.assertEqual(main.SETTINGS_FILE.read_text(), previous)
        self.assertEqual(list(self.root.glob(".settings-*.tmp")), [])

    async def test_directory_browser_and_nonexistent_default(self):
        child = self.root / "文件夹 & spaces"
        child.mkdir()
        (self.root / ".hidden").mkdir()
        (self.root / "file.txt").write_text("file")
        status, content = await self.request("GET", "/api/directories?path=" + quote(str(self.root)))
        self.assertEqual(status, 200)
        data = json.loads(content)
        self.assertEqual(data["directories"], [child.name])
        self.assertEqual(data["parent"], str(self.root.parent))
        status, content = await self.request("GET", "/api/directories")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(content)["path"], str(self.root))
        status, _ = await self.request("GET", "/api/directories?path=" + quote(str(child / "missing")))
        self.assertEqual(status, 400)

    async def test_existing_task_keeps_its_directory_after_switch(self):
        await self.save(self.default)
        task = main.Task("old-task", "https://www.douyin.com/video/123")
        main.tasks[task.id] = task
        folder = self.default / "123"
        folder.mkdir()
        (folder / "info.txt").write_text("original task file")
        await self.save(self.root / "new")
        result = {"success": True, "meta": {"desc": "", "aweme_id": "123"}, "files": {}}
        with patch.object(main, "download_all", new_callable=AsyncMock, return_value=result) as download:
            await main._run_download(task, {})
        self.assertEqual(download.call_args.args[1], self.default)
        self.assertEqual(main.Task("new-task", "url").downloads_dir, self.root / "new")
        status, content = await self.request("GET", "/api/file/old-task/info.txt")
        self.assertEqual(status, 200)
        self.assertEqual(content, b"original task file")

    async def test_corrupt_settings_fall_back_to_default(self):
        main.SETTINGS_FILE.write_text("invalid JSON")
        with self.assertLogs(level="WARNING"):
            self.assertEqual(main.load_downloads_dir(), self.default)


if __name__ == "__main__":
    unittest.main()
