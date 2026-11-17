import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import av
import numpy as np
from PIL import Image
from aiohttp import ClientTimeout, web
from aiohttp.test_utils import TestClient, TestServer

COMFY = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(COMFY))
import comfy.cli_args
comfy.cli_args.args.cpu = True

spec = importlib.util.spec_from_file_location("long_video_node", Path(__file__).resolve().parents[1] / "long_video_node.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def make_video(path, duration, fps=10, audio=False, size="96x64"):
    args = [module.binary("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}"]
    if audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000"]
    args += ["-t", str(duration), "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", "-g", str(fps * 2)]
    if audio:
        args += ["-c:a", "aac"]
    subprocess.run(args + [str(path)], check=True, capture_output=True, creationflags=module.CREATE_NO_WINDOW)


class LongVideoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        (cls.root / "nested").mkdir()
        make_video(cls.root / "nested" / "clip.mp4", 6, audio=True)
        make_video(cls.root / "two_hours.mp4", 7260, fps=2)
        make_video(cls.root / "ntsc.mp4", 4, fps="24000/1001")
        make_video(cls.root / "flat.mp4", 2, size="64x32")
        subprocess.run([module.binary("ffmpeg"), "-v", "error", "-y", "-display_rotation", "90", "-i", str(cls.root / "flat.mp4"),
                        "-c", "copy", str(cls.root / "rotated.mp4")], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        for name in ("get_input_directory", "get_temp_directory"):
            patcher = patch.object(module.folder_paths, name, return_value=str(self.root))
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_nested_files_and_path_boundary(self):
        self.assertIn("nested/clip.mp4", module.video_files())
        for name in ("../outside.mp4", str(self.root.parent / "outside.mp4"), "https://example.com/movie.mp4", "nested/clip.txt", "missing.mp4"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                module.resolve_video(name)
        self.assertEqual(module.resolve_video("nested/clip.mp4"), self.root / "nested" / "clip.mp4")

    def test_symlink_escape(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "external.mp4"
            target.write_bytes(b"external")
            link = self.root / "escape.mp4"
            try:
                link.symlink_to(target)
            except OSError:
                self.skipTest("Symlink creation not permitted on this host")
            try:
                with self.assertRaises(ValueError):
                    module.resolve_video("escape.mp4")
            finally:
                link.unlink()

    def test_invalid_ranges(self):
        for start, end in ((-1, 2), (2, 2), (3, 2), (float("nan"), 2), (0, float("inf")), (6, 7)):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                module.validate_window(start, end, 6)
        self.assertEqual(module.validate_window(5, 8, 6), (5, 6))

    def test_segment_frames_audio_and_native_components(self):
        video = module.WN_LoadLongVideo.execute("nested/clip.mp4", 2.3, 3.5).result[0]
        output = Path(video.get_stream_source())
        self.addCleanup(output.unlink, missing_ok=True)
        with av.open(str(self.root / "nested" / "clip.mp4")) as source:
            expected = [frame.to_ndarray(format="rgb24") for frame in source.decode(video=0)][23:35]
        with av.open(str(output)) as result:
            actual = [frame.to_ndarray(format="rgb24") for frame in result.decode(video=0)]
        np.testing.assert_array_equal(actual, expected)
        components = video.get_components()
        self.assertEqual(tuple(components.images.shape), (12, 64, 96, 3))
        self.assertEqual(float(components.frame_rate), 10)
        self.assertIsNotNone(components.audio)
        self.assertAlmostEqual(components.audio["waveform"].shape[-1] / components.audio["sample_rate"], 1.2, places=2)

    def execute(self, name, start, end, **kwargs):
        result = module.WN_LoadLongVideo.execute(name, start, end, **kwargs).result
        self.addCleanup(Path(result[0].get_stream_source()).unlink, missing_ok=True)
        return result

    @staticmethod
    def source_frames(path):
        with av.open(str(path)) as source:
            return [frame.to_ndarray(format="rgb24") for frame in source.decode(video=0)]

    def test_in_point_rounded_past_frame_keeps_that_frame(self):
        # At 23.976 fps the UI's ms-rounded frame start is often just after the frame's timestamp.
        fps = 24000 / 1001
        frames = self.source_frames(self.root / "ntsc.mp4")
        for first in (1, 5, 25, 37):
            stored = round(first / fps * 1000) / 1000
            stored_end = round((first + 24) / fps * 1000) / 1000
            with self.subTest(first=first, stored=stored):
                video, images, _, count, rate = self.execute("ntsc.mp4", stored, stored_end)
                self.assertEqual(count, 24)
                self.assertAlmostEqual(rate, fps, places=3)
                np.testing.assert_array_equal(self.source_frames(video.get_stream_source()), frames[first:first + 24])
                np.testing.assert_array_equal((images.numpy() * 255).round().astype(np.uint8), frames[first:first + 24])

    def test_preview_matches_import_first_frame(self):
        fps = 24000 / 1001
        frames = self.source_frames(self.root / "ntsc.mp4")
        stored = round(5 / fps * 1000) / 1000
        jpeg = np.asarray(Image.open(io.BytesIO(module.preview_frame(self.root / "ntsc.mp4", stored, fps))).convert("RGB"), dtype=np.int16)
        errors = [np.abs(jpeg - frame).mean() for frame in frames[3:8]]
        self.assertEqual(int(np.argmin(errors)) + 3, 5)

    def test_outputs_audio_and_counts(self):
        video, images, audio, count, rate = self.execute("nested/clip.mp4", 1, 3)
        self.assertEqual((count, rate), (20, 10))
        self.assertEqual(tuple(images.shape), (20, 64, 96, 3))
        self.assertEqual(audio["waveform"].shape[1], 1)
        self.assertAlmostEqual(audio["waveform"].shape[-1] / audio["sample_rate"], 2, places=2)
        self.assertLessEqual(float(audio["waveform"].abs().max()), 1.0)
        self.assertGreater(float(audio["waveform"].abs().max()), 0.1)
        self.assertIsNone(self.execute("two_hours.mp4", 0, 2)[2])

    def test_format_trims_count_and_resamples(self):
        name = "Wan 2.2 5B (4n+1, 24 fps)"
        with patch.object(module, "load_presets", return_value=module._parse_presets(module.FALLBACK_PRESETS)):
            _, images, _, count, rate = self.execute("nested/clip.mp4", 2, 3.5, format=name)
            self.assertEqual((count, rate), (13, 10))
            self.assertEqual(images.shape[0], 13)
            _, _, audio, count, rate = self.execute("nested/clip.mp4", 2, 3, frame_rate=24, format=name)
            self.assertEqual((count, rate), (21, 24))
            self.assertAlmostEqual(audio["waveform"].shape[-1] / audio["sample_rate"], 21 / 24, places=2)
            # Clamped at EOF: frames 50-59 are 10, trimmed to 9.
            self.assertEqual(self.execute("nested/clip.mp4", 5, 99, format=name)[3], 9)
            with self.assertRaises(ValueError):
                module.WN_LoadLongVideo.execute("nested/clip.mp4", 2, 2.3, format="MiniMax H3 (17n+5, 24 fps)")

    def test_resampled_frames_follow_source(self):
        frames = self.source_frames(self.root / "nested" / "clip.mp4")
        _, images, _, count, rate = self.execute("nested/clip.mp4", 2, 4, frame_rate=5)
        self.assertEqual((count, rate), (10, 5))
        np.testing.assert_array_equal((images.numpy() * 255).round().astype(np.uint8), frames[20:40:2])

    def test_rotated_source_size(self):
        metadata = module.probe_video(self.root / "rotated.mp4")
        self.assertEqual((metadata["width"], metadata["height"]), (32, 64))
        self.assertEqual(tuple(self.execute("rotated.mp4", 0, 1)[1].shape[1:3]), (64, 32))

    def test_unlinked_images_are_not_decoded_and_previous_segment_is_removed(self):
        node = type("Hidden", (module.WN_LoadLongVideo,), {})
        node.hidden = SimpleNamespace(unique_id="7", prompt={"7": {"inputs": {}}, "8": {"inputs": {"video": ["7", 0]}}})
        first = node.execute("nested/clip.mp4", 0, 1).result
        self.assertEqual(tuple(first[1].shape), (1, 64, 64, 3))
        self.assertIsNone(first[2])
        second = node.execute("nested/clip.mp4", 1, 2).result
        self.addCleanup(Path(second[0].get_stream_source()).unlink, missing_ok=True)
        self.assertFalse(Path(first[0].get_stream_source()).exists())
        self.assertTrue(Path(second[0].get_stream_source()).exists())
        self.assertEqual(module.linked_outputs({"9": {"inputs": {"image": ["7", 1]}}}, "7"), {1})
        self.assertIsNone(module.linked_outputs(None, "7"))

    def test_thumbnails_and_proxy(self):
        strip = Image.open(io.BytesIO(module.thumbnail_strip(self.root / "two_hours.mp4", 12)))
        self.assertEqual(strip.height, module.THUMB_HEIGHT)
        self.assertEqual(strip.width % 12, 0)
        # Short GOP-sparse source: every tile must have a picture, not the dark fill.
        strip = np.asarray(Image.open(io.BytesIO(module.thumbnail_strip(self.root / "nested" / "clip.mp4", 6))).convert("L"))
        width = strip.shape[1] // 6
        self.assertTrue(all(strip[:, i * width:(i + 1) * width].mean() > 40 for i in range(6)))
        try:
            proxy, offset = module.proxy_clip(self.root / "nested" / "clip.mp4", 2, 3)
        except ValueError as error:
            self.skipTest(str(error))
        self.addCleanup(proxy.unlink, missing_ok=True)
        self.assertAlmostEqual(offset, 2)
        self.assertAlmostEqual(module.probe_video(proxy)["duration"], 1, places=1)

    def test_presets_fallback_and_rule(self):
        presets = module._parse_presets(module.FALLBACK_PRESETS)
        h3 = presets["MiniMax H3 (17n+5, 24 fps)"]
        self.assertEqual([module.largest_valid(n, h3) for n in (4, 5, 21, 22, 200)], [0, 5, 5, 22, 192])
        self.assertIn("None", module.format_names())

    def test_seek_after_two_hours(self):
        path = self.root / "two_hours.mp4"
        before = time.monotonic()
        metadata = module.probe_video(path)
        self.assertAlmostEqual(metadata["duration"], 7260, places=2)
        frame = module.preview_frame(path, 7210)
        self.assertEqual(Image.open(io.BytesIO(frame)).size, (96, 64))
        video = module.WN_LoadLongVideo.execute("two_hours.mp4", 7210, 7214).result[0]
        output = Path(video.get_stream_source())
        self.addCleanup(output.unlink, missing_ok=True)
        self.assertAlmostEqual(module.probe_video(output)["duration"], 4, places=2)
        with av.open(str(output)) as result:
            self.assertEqual(len(list(result.decode(video=0))), 8)
        print(f"\nTwo-hour seek + preview + four-second import: {time.monotonic() - before:.2f}s")

    def test_last_segment_clamps_to_source(self):
        video = module.WN_LoadLongVideo.execute("nested/clip.mp4", 5, 99).result[0]
        path = Path(video.get_stream_source())
        self.addCleanup(path.unlink, missing_ok=True)
        with av.open(str(path)) as result:
            self.assertEqual(len(list(result.decode(video=0))), 10)

    def test_interrupt_removes_partial_file(self):
        before = set(self.root.glob("wn_segment_*"))
        with patch.object(module.comfy.model_management, "throw_exception_if_processing_interrupted", side_effect=InterruptedError):
            with self.assertRaises(InterruptedError):
                module.WN_LoadLongVideo.execute("two_hours.mp4", 0, 7000)
        self.assertEqual(set(self.root.glob("wn_segment_*")), before)

    def test_preview_invalid_time_and_eof(self):
        for seconds in (-1, float("nan"), float("inf"), 999):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                module.preview_frame(self.root / "nested" / "clip.mp4", seconds)


class LongVideoRoutes(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        make_video(self.root / "clip.mp4", 1)
        self.folder = patch.object(module.folder_paths, "get_input_directory", return_value=str(self.root))
        self.folder.start()
        instance = SimpleNamespace(routes=web.RouteTableDef())
        with patch.object(module.PromptServer, "instance", instance, create=True):
            module.register_long_video_routes()
        app = web.Application()
        app.add_routes(instance.routes)
        self.client = TestClient(TestServer(app), timeout=ClientTimeout(total=10))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.folder.stop()
        self.temp.cleanup()

    async def test_metadata_frame_and_range(self):
        response = await self.client.get("/wepenerd/long_video/metadata", params={"file": "clip.mp4"})
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["duration"], 1)
        response = await self.client.get("/wepenerd/long_video/frame", params={"file": "clip.mp4", "time": ".5"})
        self.assertEqual(response.status, 200)
        self.assertEqual(response.content_type, "image/jpeg")
        await response.read()
        response = await self.client.get("/wepenerd/long_video/source", params={"file": "clip.mp4"}, headers={"Range": "bytes=100-199"})
        self.assertEqual(response.status, 206)
        self.assertEqual(await response.read(), (self.root / "clip.mp4").read_bytes()[100:200])
        response = await self.client.get("/wepenerd/long_video/thumbs", params={"file": "clip.mp4", "count": "4"})
        self.assertEqual(response.status, 200)
        self.assertEqual(response.content_type, "image/jpeg")
        await response.read()
        response = await self.client.get("/wepenerd/long_video/presets")
        self.assertIn("MiniMax H3 (17n+5, 24 fps)", await response.json())

    async def test_routes_reject_escape_and_invalid_time(self):
        for action in ("metadata", "frame", "source"):
            response = await self.client.get(f"/wepenerd/long_video/{action}", params={"file": "../outside.mp4"})
            self.assertEqual(response.status, 400)
            await response.read()
        response = await self.client.get("/wepenerd/long_video/proxy_file", params={"name": "../clip.mp4"})
        self.assertEqual(response.status, 400)
        await response.read()
        response = await self.client.get("/wepenerd/long_video/frame", params={"file": "clip.mp4", "time": "NaN"})
        self.assertEqual(response.status, 400)
        await response.read()


if __name__ == "__main__":
    unittest.main()
