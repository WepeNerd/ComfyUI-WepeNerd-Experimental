"""Seekable previews and bounded, file-backed video imports."""

import asyncio
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
import hashlib
import io as bytes_io
import itertools
import json
import logging
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time

import av
import numpy as np
import torch
from aiohttp import web
import folder_paths
import comfy.model_management
from comfy.utils import ProgressBar
from comfy_api.latest import io, InputImpl
from server import PromptServer


VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".mpeg", ".mpg", ".ts", ".mts", ".m2ts"}
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
LIST_CACHE_SECONDS = 3.0
THUMB_HEIGHT = 48
MAX_THUMBS = 48
PROXY_MAX_SECONDS = 600
PROXY_KEEP = 4
log = logging.getLogger("WepeNerd")
_list_cache = {}
_previous_segments = {}
_proxy_lock = threading.Lock()
_proxies = OrderedDict()
_thumb_cache = OrderedDict()

# Used when the core WepeNerd pack (and its Duration Selector presets) is not installed.
FALLBACK_PRESETS = {
    "LTX-2.x (8n+1, 24 fps)": {"step": 8, "offset": 1, "min": 9, "max": 481, "fps": 24},
    "LTX-Video 0.9.x (8n+1, 25 fps)": {"step": 8, "offset": 1, "min": 9, "max": 257, "fps": 25},
    "MiniMax H3 (17n+5, 24 fps)": {"step": 17, "offset": 5, "min": 5, "max": 362, "fps": 24},
    "Wan 2.1 / 2.2 14B (4n+1, 16 fps)": {"step": 4, "offset": 1, "min": 5, "max": 161, "fps": 16},
    "Wan 2.2 5B (4n+1, 24 fps)": {"step": 4, "offset": 1, "min": 5, "max": 161, "fps": 24},
    "HunyuanVideo (4n+1, 24 fps)": {"step": 4, "offset": 1, "min": 5, "max": 129, "fps": 24},
    "Cosmos 1.0 (8n+1, 24 fps)": {"step": 8, "offset": 1, "min": 9, "max": 121, "fps": 24},
    "Mochi 1 (6n+1, 30 fps)": {"step": 6, "offset": 1, "min": 7, "max": 163, "fps": 30},
}


# ---------------------------------------------------------------- files


def video_files():
    """Video files under ComfyUI/input as sorted relative POSIX paths, skipping hidden folders.

    os.scandir reads file types from the directory listing, so Windows needs no per-file stat.
    The list is reused for a few seconds because ComfyUI asks for it on every queued prompt.
    """
    root = str(folder_paths.get_input_directory())
    now = time.monotonic()
    cached = _list_cache.get(root)
    if cached and now - cached[0] < LIST_CACHE_SECONDS:
        return list(cached[1])
    found, pending = [], [("", root)]
    while pending:
        prefix, folder = pending.pop()
        try:
            entries = os.scandir(folder)
        except OSError:
            continue
        with entries:
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if not entry.name.startswith("."):
                            pending.append((f"{prefix}{entry.name}/", entry.path))
                    elif os.path.splitext(entry.name)[1].lower() in VIDEO_EXTENSIONS and entry.is_file():
                        found.append(prefix + entry.name)
                except OSError:
                    continue
    found = sorted(found) or [""]
    _list_cache[root] = (now, found)
    return list(found)


def resolve_video(name):
    root = Path(folder_paths.get_input_directory()).resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Video must be inside ComfyUI/input (including subfolders).")
    if path.suffix.lower() not in VIDEO_EXTENSIONS or not path.is_file():
        raise ValueError("Choose an existing video in ComfyUI/input, then refresh the file list.")
    return path


def binary(name):
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"Load Long Video needs {name} on PATH. Install FFmpeg and restart ComfyUI.")
    return path


def encoder_threads():
    return max(2, min(16, os.cpu_count() or 2))


# ---------------------------------------------------------------- presets


def _parse_presets(raw):
    presets = {}
    for name, cfg in raw.items():
        if name.startswith("_") or not isinstance(cfg, dict):
            continue
        try:
            fps = float(cfg.get("fps", 24))
            if "frames" in cfg:
                frames = sorted({int(f) for f in cfg["frames"] if int(f) > 0})
                rule = None
            else:
                step, offset = int(cfg["step"]), int(cfg.get("offset", 0))
                if step <= 0 or fps <= 0:
                    continue
                low, high = int(cfg.get("min", offset)), int(cfg["max"])
                frames = [f for f in range(offset, high + 1, step) if f >= max(low, 1)]
                rule = [step, offset]
        except (KeyError, TypeError, ValueError):
            continue
        if frames:
            presets[name] = {"fps": fps, "rule": rule, "frames": frames, "min": frames[0], "max": frames[-1]}
    return presets


def _core_presets_file():
    """The core pack's Duration Selector presets, so both packs offer the same list."""
    try:
        roots = folder_paths.get_folder_paths("custom_nodes")
    except Exception:
        roots = [str(Path(__file__).resolve().parent.parent)]
    for root in roots:
        try:
            for entry in os.scandir(root):
                if entry.is_dir() and entry.name.lower() == "comfyui-wepenerd":
                    candidate = Path(entry.path) / "frame_number_presets.json"
                    if candidate.is_file():
                        return candidate
        except OSError:
            continue
    return None


def load_presets():
    source = _core_presets_file()
    if source:
        try:
            presets = _parse_presets(json.loads(source.read_text(encoding="utf-8")))
            if presets:
                return presets
        except (OSError, ValueError) as error:
            log.warning("[WepeNerd] Load Long Video: cannot read %s (%s); using built-in presets.", source, error)
    return _parse_presets(FALLBACK_PRESETS)


def format_names():
    return ["None", *load_presets()]


def resolve_format(name, presets=None):
    if name in (None, "", "None"):
        return None
    presets = load_presets() if presets is None else presets
    if name not in presets:
        raise ValueError(f"Unknown format preset: {name}")
    return presets[name]


def largest_valid(count, preset):
    """Largest frame count <= count that the preset allows (0 if none)."""
    if preset is None or count <= 0:
        return max(0, count)
    if preset["rule"]:
        step, offset = preset["rule"]
        valid = count - (count - offset) % step
        return valid if valid >= 1 else 0
    return max((f for f in preset["frames"] if f <= count), default=0)


# ---------------------------------------------------------------- probing and planning


def run_preview(command, timeout=30):
    result = subprocess.run(command, capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW)
    if result.returncode:
        raise ValueError(result.stderr.decode("utf-8", errors="replace")[-2000:] or "Cannot read this video.")
    return result.stdout


def _rotation(stream):
    for side in stream.get("side_data_list", []) or []:
        if "rotation" in side:
            try:
                return int(round(float(side["rotation"]))) % 360
            except (TypeError, ValueError):
                pass
    try:
        return int(stream.get("tags", {}).get("rotate", 0)) % 360
    except (TypeError, ValueError):
        return 0


def probe_video(path):
    # Header/index metadata only: never count packets or scan/decode the source.
    data = json.loads(run_preview([
        binary("ffprobe"), "-v", "error", "-protocol_whitelist", "file,pipe", "-select_streams", "v:0",
        "-show_entries",
        "stream=width,height,avg_frame_rate,r_frame_rate,duration:stream_side_data=rotation:stream_tags=rotate:format=duration",
        "-of", "json", str(path),
    ]))
    streams = data.get("streams", [])
    if not streams:
        raise ValueError("This file has no video stream.")
    stream = streams[0]
    duration = stream.get("duration") or data.get("format", {}).get("duration")
    if not duration or not math.isfinite(float(duration)) or float(duration) <= 0:
        raise ValueError("This video has no usable duration/index. Remux it to MP4 or MKV first.")
    rate = stream.get("avg_frame_rate", "0/0")
    if rate in ("0/0", "0"):
        rate = stream.get("r_frame_rate", "0/0")
    fps = float(Fraction(rate)) if rate not in ("0/0", "0") else 0.0
    if fps <= 0:
        raise ValueError("Cannot determine the video's frame rate.")
    # FFmpeg rotates frames on import, so report the size the frames will have.
    width, height = stream["width"], stream["height"]
    rotation = _rotation(stream)
    if rotation % 180:
        width, height = height, width
    stat = path.stat()
    return {"duration": float(duration), "fps": fps, "width": width, "height": height, "rotation": rotation,
            "frames": int(math.floor(float(duration) * fps + 1e-6)),
            "version": f"{stat.st_size}-{stat.st_mtime_ns}"}


def frame_index(seconds, fps):
    """Frame shown at a stored time. The UI stores frame starts (k / fps) rounded to ms."""
    return max(0, int(math.floor(seconds * fps + 0.5)))


def seek_time(frame, fps):
    """Seek half a frame early: FFmpeg keeps frames at or after the seek time, so this lands on
    ``frame`` even when the stored time was rounded a little past its timestamp."""
    return max(0.0, (frame - 0.5) / fps)


def validate_window(start, end, duration):
    if not all(math.isfinite(v) for v in (start, end)) or start < 0 or end <= start:
        raise ValueError("Set an out point later than the in point; times must be finite and nonnegative.")
    if start >= duration:
        raise ValueError("The in point is past the end of the video.")
    return start, min(end, duration)


def plan_segment(metadata, start_seconds, end_seconds, frame_rate=0.0, preset=None):
    """Frames the node will import (js/wn_long_video.js mirrors this).

    Native rate: source frames in [in, out), counted on the nominal frame grid.
    With frame_rate: the selected span resampled to that rate from the in frame.
    A format preset then trims the count down to the model's rule.
    """
    if not math.isfinite(frame_rate) or frame_rate < 0:
        raise ValueError("frame_rate must be finite and nonnegative (0 keeps the source rate).")
    start, end = validate_window(start_seconds, end_seconds, metadata["duration"])
    fps = metadata["fps"]
    first = min(frame_index(start, fps), max(0, metadata["frames"] - 1))
    last = min(max(first + 1, frame_index(end, fps)), max(first + 1, metadata["frames"]))
    source_count = last - first
    rate = frame_rate or fps
    selected = source_count if not frame_rate else max(1, int(math.floor(source_count / fps * frame_rate + 0.5)))
    count = largest_valid(selected, preset)
    return {"first_frame": first, "source_frames": source_count, "selected": selected, "count": count,
            "fps": rate, "start": first / fps, "seek": seek_time(first, fps), "seconds": count / rate}


# ---------------------------------------------------------------- preview


def preview_frame(path, seconds, fps=None):
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("Preview time must be a finite, nonnegative number.")
    position = seek_time(frame_index(seconds, fps), fps) if fps else seconds
    data = run_preview([
        binary("ffmpeg"), "-v", "error", "-nostdin", "-threads", "2", "-ss", f"{position:.6f}",
        "-protocol_whitelist", "file,pipe", "-i", str(path), "-map", "0:v:0", "-frames:v", "1",
        "-vf", "scale=w='min(960,iw)':h=-2", "-an", "-sn", "-threads", "2",
        "-c:v", "mjpeg", "-q:v", "4", "-f", "image2pipe", "pipe:1",
    ])
    if not data:
        raise ValueError("No frame at this position. Move the playhead slightly earlier.")
    return data


def _thumb(path, seconds):
    # Keyframe-only seek: cheap even hours into the file; the strip only needs the gist.
    return run_preview([
        binary("ffmpeg"), "-v", "error", "-nostdin", "-noaccurate_seek", "-ss", f"{seconds:.3f}",
        "-skip_frame", "nokey", "-i", str(path), "-map", "0:v:0", "-frames:v", "1",
        # The keyframe usually sits before the seek point; passthrough keeps it instead of dropping it.
        "-fps_mode", "passthrough",
        "-vf", f"scale=-2:{THUMB_HEIGHT}", "-an", "-sn", "-c:v", "mjpeg", "-q:v", "6", "-f", "image2pipe", "pipe:1",
    ])


def thumbnail_strip(path, count):
    """One JPEG with ``count`` thumbnails side by side, evenly spaced over the source."""
    from PIL import Image

    count = max(1, min(MAX_THUMBS, int(count)))
    metadata = probe_video(path)
    key = (str(path), metadata["version"], count)
    if key in _thumb_cache:
        _thumb_cache.move_to_end(key)
        return _thumb_cache[key]
    times = [(i + 0.5) * metadata["duration"] / count for i in range(count)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda t: _safe_thumb(path, t), times))
    tiles = [Image.open(bytes_io.BytesIO(data)).convert("RGB") if data else None for data in results]
    width = next((tile.width for tile in tiles if tile), max(1, round(THUMB_HEIGHT * metadata["width"] / metadata["height"])))
    sheet = Image.new("RGB", (width * count, THUMB_HEIGHT), (16, 18, 22))
    for i, tile in enumerate(tiles):
        if tile:
            sheet.paste(tile.resize((width, THUMB_HEIGHT)), (i * width, 0))
    buffer = bytes_io.BytesIO()
    sheet.save(buffer, format="JPEG", quality=70)
    _thumb_cache[key] = buffer.getvalue()
    while len(_thumb_cache) > 16:
        _thumb_cache.popitem(last=False)
    return _thumb_cache[key]


def _safe_thumb(path, seconds):
    try:
        return _thumb(path, seconds)
    except (ValueError, subprocess.TimeoutExpired):
        return b""


def proxy_clip(path, start, end):
    """A small H.264/AAC MP4 of just [start, end) so any source codec can play in the browser."""
    metadata = probe_video(path)
    start, end = validate_window(start, end, metadata["duration"])
    if end - start > PROXY_MAX_SECONDS:
        raise ValueError(f"Proxy playback is limited to {PROXY_MAX_SECONDS // 60} minutes. Shorten the selection.")
    first = frame_index(start, metadata["fps"])
    key = hashlib.sha1(f"{path}|{metadata['version']}|{first}|{end:.3f}".encode()).hexdigest()[:16]
    output = Path(folder_paths.get_temp_directory()) / f"wn_proxy_{key}.mp4"
    with _proxy_lock:
        if output.is_file():
            if key in _proxies:
                _proxies.move_to_end(key)
            return output, first / metadata["fps"]
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_suffix(".part.mp4")
    errors = []
    for encoder in ("libx264", "libopenh264"):
        command = [
            binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-ss", f"{seek_time(first, metadata['fps']):.6f}", "-i", str(path), "-t", f"{end - first / metadata['fps']:.6f}",
            "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn",
            "-vf", "setpts=PTS-STARTPTS,scale=w='min(960,iw)':h=-2,format=yuv420p",
            "-af", "asetpts=PTS-STARTPTS", "-c:v", encoder,
            *(["-preset", "veryfast", "-crf", "26"] if encoder == "libx264" else ["-b:v", "3M"]),
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(partial),
        ]
        result = subprocess.run(command, capture_output=True, timeout=900, creationflags=CREATE_NO_WINDOW)
        if result.returncode == 0:
            break
        errors.append(result.stderr.decode("utf-8", errors="replace")[-800:])
    else:
        partial.unlink(missing_ok=True)
        raise ValueError("Cannot make a playback proxy (FFmpeg needs libx264 or libopenh264): " + errors[-1])
    os.replace(partial, output)
    with _proxy_lock:
        _proxies[key] = output
        while len(_proxies) > PROXY_KEEP:
            _, old = _proxies.popitem(last=False)
            try:
                old.unlink(missing_ok=True)
            except OSError:
                pass
    return output, first / metadata["fps"]


# ---------------------------------------------------------------- import


def extract_segment(path, plan, output, has_audio=True, progress=None):
    """Write plan["count"] frames from plan["seek"] to a lossless FFV1/MKV with PCM audio.

    Video and audio are opened separately: video seeks half a frame early so the in frame is
    kept, audio starts exactly at the in frame's time, and both are cut to the clip's length.
    """
    fps_filter = f",fps={Fraction(plan['fps']).limit_denominator(1001000)}:round=near" if plan.get("resample") else ""
    command = [
        binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-nostats",
        "-progress", "pipe:1",
        "-ss", f"{plan['seek']:.6f}", "-protocol_whitelist", "file,pipe", "-i", str(path),
    ]
    if has_audio:
        command += ["-ss", f"{plan['start']:.6f}", "-protocol_whitelist", "file,pipe", "-i", str(path)]
    command += [
        "-map", "0:v:0", "-sn", "-dn", "-map_metadata", "-1",
        "-vf", f"setpts=PTS-STARTPTS{fps_filter}", "-frames:v", str(plan["count"]),
        "-c:v", "ffv1", "-level", "3", "-slices", "16", "-slicecrc", "0", "-g", "1",
        "-threads", str(encoder_threads()), "-fps_mode", "passthrough",
    ]
    if has_audio:
        command += ["-map", "1:a:0?", "-af", f"atrim=duration={plan['seconds']:.6f},asetpts=PTS-STARTPTS",
                    "-c:a", "pcm_s16le"]
    command.append(str(output))
    # FFmpeg streams directly to disk; no source-sized tensor or pipe buffer.
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors, creationflags=CREATE_NO_WINDOW)
        done = [0]

        def read_progress():
            for line in process.stdout:
                if line.startswith(b"frame="):
                    try:
                        done[0] = int(line[6:].strip() or 0)
                    except ValueError:
                        pass

        reader = threading.Thread(target=read_progress, daemon=True)
        reader.start()
        try:
            while True:
                comfy.model_management.throw_exception_if_processing_interrupted()
                try:
                    code = process.wait(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    if progress:
                        progress.update_absolute(min(done[0], plan["count"]))
            if code:
                errors.seek(max(0, errors.tell() - 4000))
                raise RuntimeError("Segment import failed: " + errors.read().decode("utf-8", errors="replace"))
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            reader.join(timeout=1)
            process.stdout.close()


def has_audio_stream(path):
    with av.open(str(path)) as container:
        return bool(container.streams.audio)


def count_frames(path):
    # FFV1 here is intra-only, so packets are frames; reading packets doesn't decode anything.
    data = json.loads(run_preview([
        binary("ffprobe"), "-v", "error", "-select_streams", "v:0", "-count_packets",
        "-show_entries", "stream=nb_read_packets", "-of", "json", str(path),
    ], timeout=120))
    return int(data["streams"][0]["nb_read_packets"])


def decode_images(path, count):
    """Decode the small FFV1 clip into one float32 batch, keeping only a uint8 buffer on the way."""
    pixels, filled = None, 0
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        for frame in container.decode(stream):
            comfy.model_management.throw_exception_if_processing_interrupted()
            if filled >= count:
                break
            image = frame.to_ndarray(format="rgb24")
            if pixels is None:
                pixels = np.empty((count, *image.shape), dtype=np.uint8)
            elif image.shape != pixels.shape[1:]:
                raise ValueError("Imported frames changed size mid-clip.")
            pixels[filled] = image
            filled += 1
    if not filled:
        raise ValueError("No frames in the selected range.")
    return torch.from_numpy(pixels[:filled]).to(dtype=torch.float32).div_(255)


def decode_audio(path):
    with av.open(str(path)) as container:
        if not container.streams.audio:
            return None
        stream = container.streams.audio[0]
        rate = stream.codec_context.sample_rate
        resampler = av.AudioResampler(format="fltp", layout=stream.codec_context.layout, rate=rate)
        chunks = []
        for frame in itertools.chain(container.decode(stream), (None,)):
            for samples in resampler.resample(frame):
                chunks.append(samples.to_ndarray())
    if not chunks:
        return None
    waveform = np.ascontiguousarray(np.concatenate(chunks, axis=1), dtype=np.float32)
    return {"waveform": torch.from_numpy(waveform).unsqueeze(0), "sample_rate": rate}


def linked_outputs(prompt, unique_id):
    """Output slots of this node that other nodes use, or None when unknown (run outside ComfyUI)."""
    if not prompt or unique_id is None:
        return None
    used = set()
    for node in prompt.values():
        for value in (node.get("inputs") or {}).values():
            if isinstance(value, list) and len(value) == 2 and str(value[0]) == str(unique_id):
                used.add(int(value[1]))
    return used


def _lru_cache_enabled():
    try:
        from comfy.cli_args import args
    except ImportError:
        return False
    return bool(getattr(args, "cache_lru", 0) or getattr(args, "cache_ram", 0))


def remember_segment(node_id, output):
    """Delete this node's previous import, so re-runs don't fill the temp folder.

    Skipped with LRU/RAM caching, where ComfyUI may still hand out the earlier result."""
    if node_id is None or _lru_cache_enabled():
        return
    previous = _previous_segments.get(node_id)
    _previous_segments[node_id] = output
    if previous and previous != output:
        try:
            Path(previous).unlink(missing_ok=True)
        except OSError:
            pass


class WN_LoadLongVideo(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="WN_LoadLongVideo", display_name="Load Long Video (WepeNerd)", category="WepeNerd/Video",
            description="Scrub a long source and import only the selected range, frame-accurately. "
                        "Outputs the clip as VIDEO plus images, audio, frame count and FPS.",
            inputs=[
                io.Combo.Input("video_file", options=video_files(), tooltip="Copy videos into ComfyUI/input or a subfolder. Refresh files in the preview panel."),
                io.Float.Input("start_seconds", default=0, min=0, max=31536000, step=0.001, tooltip="In point: the frame shown at this time is the first frame imported."),
                io.Float.Input("end_seconds", default=5, min=0.001, max=31536000, step=0.001, tooltip="Out point (exclusive): the frame shown at this time is the first one left out."),
                io.Float.Input("frame_rate", default=0, min=0, max=240, step=0.001, optional=True,
                               tooltip="0 keeps the source frame rate. Otherwise resample the selection to this FPS."),
                io.Combo.Input("format", options=format_names(), default="None", optional=True,
                               tooltip="Trim the frame count down to a video model's rule (the Duration Selector presets). "
                                       "Picking one also sets frame_rate to the model's FPS."),
            ],
            outputs=[
                io.Video.Output("video", tooltip="The imported clip as a lossless FFV1/MKV file."),
                io.Image.Output("images", tooltip="The clip's frames. Decoded only when this output is connected."),
                io.Audio.Output("audio", tooltip="Audio for exactly the imported frames, or None if the source has none."),
                io.Int.Output("frame_count"),
                io.Float.Output("fps"),
            ],
            hidden=[io.Hidden.prompt, io.Hidden.unique_id],
        )

    @classmethod
    def validate_inputs(cls, video_file, format="None", **kwargs):
        try:
            resolve_video(video_file)
            resolve_format(format)
        except ValueError as error:
            return str(error)
        return True

    @classmethod
    def fingerprint_inputs(cls, video_file, **kwargs):
        stat = resolve_video(video_file).stat()
        return stat.st_size, stat.st_mtime_ns

    @classmethod
    def execute(cls, video_file, start_seconds, end_seconds, frame_rate=0.0, format="None"):
        path = resolve_video(video_file)
        metadata = probe_video(path)
        preset = resolve_format(format)
        plan = plan_segment(metadata, start_seconds, end_seconds, frame_rate or 0.0, preset)
        if plan["count"] <= 0:
            raise ValueError(f"The selection is {plan['selected']} frames, shorter than {format} allows. "
                             f"Move the out point later or use format None.")
        plan["resample"] = bool(frame_rate)
        hidden = getattr(cls, "hidden", None)
        node_id = getattr(hidden, "unique_id", None)
        used = linked_outputs(getattr(hidden, "prompt", None), node_id)
        temp_dir = folder_paths.get_temp_directory()
        os.makedirs(temp_dir, exist_ok=True)
        fd, output = tempfile.mkstemp(prefix="wn_segment_", suffix=".mkv", dir=temp_dir)
        os.close(fd)
        try:
            audio_source = has_audio_stream(path)
            extract_segment(path, plan, output, audio_source, ProgressBar(plan["count"]))
            count = count_frames(output)
            if count != plan["count"]:
                # The file ended early (headers can overstate length). Re-cut to a legal count.
                plan["count"] = largest_valid(count, preset)
                if plan["count"] <= 0:
                    raise ValueError("Too few frames before the end of the video for this format.")
                if plan["count"] != count:
                    plan["seconds"] = plan["count"] / plan["fps"]
                    extract_segment(path, plan, output, audio_source)
                    count = count_frames(output)
            if count <= 0:
                raise ValueError("No frames in the selected range.")
            if used is None or 1 in used:
                images = decode_images(output, count)
            else:
                images = torch.zeros((1, 64, 64, 3), dtype=torch.float32)
            audio = decode_audio(output) if audio_source and (used is None or 2 in used) else None
        except BaseException:
            Path(output).unlink(missing_ok=True)
            raise
        remember_segment(node_id, output)
        return io.NodeOutput(InputImpl.VideoFromFile(output), images, audio, count, float(plan["fps"]))


# ---------------------------------------------------------------- routes


def register_long_video_routes():
    instance = getattr(PromptServer, "instance", None)
    if instance is None or getattr(instance, "_wn_long_video_routes_registered", False):
        return
    workers = asyncio.Semaphore(2)
    heavy = asyncio.Semaphore(1)

    @instance.routes.get("/wepenerd/long_video/{action}")
    async def long_video(request):
        try:
            action = request.match_info["action"]
            if action == "files":
                return web.json_response(await asyncio.to_thread(video_files))
            if action == "presets":
                return web.json_response(await asyncio.to_thread(load_presets))
            if action == "proxy_file":
                name = request.query.get("name", "")
                path = Path(folder_paths.get_temp_directory()) / name
                if not (name.startswith("wn_proxy_") and name.endswith(".mp4") and "/" not in name and "\\" not in name
                        and path.is_file()):
                    raise ValueError("Proxy not found. Play the selection again.")
                return web.FileResponse(path, headers={"Cache-Control": "no-cache"})
            path = resolve_video(request.query.get("file", ""))
            if action == "source":
                # aiohttp handles HTTP Range, so the browser can seek without downloading everything.
                return web.FileResponse(path, headers={"Cache-Control": "no-cache"})
            if action in ("proxy", "thumbs"):
                async with heavy:
                    if action == "thumbs":
                        data = await asyncio.to_thread(thumbnail_strip, path, int(request.query.get("count", "24")))
                        return web.Response(body=data, content_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})
                    start, end = float(request.query.get("start", "0")), float(request.query.get("end", "0"))
                    output, offset = await asyncio.to_thread(proxy_clip, path, start, end)
                    return web.json_response({"name": output.name, "offset": offset})
            async with workers:
                if action == "metadata":
                    return web.json_response(await asyncio.to_thread(probe_video, path))
                if action == "frame":
                    fps = float(request.query.get("fps", "0") or 0)
                    data = await asyncio.to_thread(preview_frame, path, float(request.query.get("time", "0")),
                                                   fps if math.isfinite(fps) and fps > 0 else None)
                    return web.Response(body=data, content_type="image/jpeg", headers={"Cache-Control": "no-store"})
            raise web.HTTPNotFound()
        except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            return web.json_response({"error": str(error)}, status=400)

    instance._wn_long_video_routes_registered = True
