import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

import main


class DownloadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.background = []
        self.directory = Path("/tmp/dyfetch-download-tests")
        self.download = AsyncMock(return_value={"success": True, "files": {}})

        def schedule(coroutine):
            task = asyncio.get_running_loop().create_task(coroutine)
            self.background.append(task)
            return task

        for patcher in (
            patch.object(main, "tasks", {}),
            patch.object(main, "DOWNLOADS_DIR", self.directory),
            patch.object(main, "download_all", self.download),
            patch.object(main.asyncio, "create_task", side_effect=schedule),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        for task in self.background:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.background, return_exceptions=True)

    async def test_batch_continues_after_failed_result_and_exception(self):
        self.download.side_effect = [
            {"success": False, "error": "Invalid URL"},
            RuntimeError("Browser failed"),
            {"success": True, "files": {}},
        ]
        responses = await main.api_download_batch(main.BatchDownloadRequest(
            urls=[" invalid-url ", "", "https://www.douyin.com/video/2", "  ",
                  "https://www.douyin.com/video/3"],
        ))
        self.assertEqual(len(responses), 3)
        self.assertEqual(len({response.id for response in responses}), 3)
        await asyncio.gather(*self.background)
        self.assertEqual([main.tasks[r.id].status for r in responses], ["error", "error", "done"])
        self.assertEqual(main.tasks[responses[0].id].error, "Invalid URL")
        self.assertEqual(main.tasks[responses[1].id].error, "Browser failed")
        self.assertEqual([call.args[0] for call in self.download.call_args_list],
                         [response.url for response in responses])
        for response in responses:
            status = await main.api_task(response.id)
            self.assertEqual(status.status, main.tasks[response.id].status)

    async def test_batch_waits_for_previous_task_and_keeps_original_location(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def download(url, directory, **options):
            if url == "first":
                started.set()
                await release.wait()
            return {"success": True, "files": {}}

        self.download.side_effect = download
        responses = await main.api_download_batch(main.BatchDownloadRequest(urls=["first", "second"]))
        await asyncio.wait_for(started.wait(), timeout=1)
        self.assertEqual(self.download.await_count, 1)
        self.assertEqual(main.tasks[responses[1].id].status, "queued")
        main.DOWNLOADS_DIR = self.directory / "new-location"
        release.set()
        await asyncio.gather(*self.background)
        self.assertEqual([call.args[0] for call in self.download.call_args_list], ["first", "second"])
        self.assertTrue(all(call.args[1] == self.directory for call in self.download.call_args_list))

    async def test_empty_batch_does_not_create_tasks(self):
        for urls in ([], ["", " ", "\t"]):
            with self.subTest(urls=urls):
                with self.assertRaises(HTTPException) as error:
                    await main.api_download_batch(main.BatchDownloadRequest(urls=urls))
                self.assertEqual(error.exception.status_code, 400)
        self.assertEqual(main.tasks, {})
        self.assertEqual(self.background, [])

    async def test_single_download_defaults_to_audio_only(self):
        response = await main.api_download(main.DownloadRequest(url="single-url"))
        await asyncio.gather(*self.background)
        self.assertEqual(main.tasks[response.id].status, "done")
        options = self.download.call_args.kwargs
        self.assertTrue(options["download_audio"])
        self.assertFalse(options["download_video"])
        self.assertFalse(options["download_cover"])
        self.assertFalse(options["save_json"])

    async def test_batch_defaults_and_explicit_resource_options(self):
        await main.api_download_batch(main.BatchDownloadRequest(urls=["audio-only"]))
        await asyncio.gather(*self.background)
        options = self.download.call_args.kwargs
        self.assertTrue(options["download_audio"])
        self.assertFalse(options["download_video"])
        self.assertFalse(options["download_cover"])
        self.assertFalse(options["save_json"])

        await main.api_download_batch(main.BatchDownloadRequest(
            urls=["custom-options"], video=True, audio=False, cover=True, save_json=True, timeout=12,
        ))
        await asyncio.gather(*self.background)
        options = self.download.call_args.kwargs
        self.assertFalse(options["download_audio"])
        self.assertTrue(options["download_video"])
        self.assertTrue(options["download_cover"])
        self.assertTrue(options["save_json"])
        self.assertEqual(options["timeout"], 12)


if __name__ == "__main__":
    unittest.main()
