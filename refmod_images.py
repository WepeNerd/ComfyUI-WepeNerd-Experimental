"""Photo discovery and one shared preparation path for preview and encoding."""

import hashlib
from pathlib import Path
import re

import numpy as np
from PIL import Image, ImageOps
import torch

import comfy.model_management
import comfy.utils
import folder_paths

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


def natural_key(value):
    return tuple((0, int(p)) if p.isdigit() else (1, p.casefold()) for p in re.split(r"(\d+)", str(value)))


def local_path(value):
    value = value.strip().strip('"')
    if not value or value.startswith(("\\\\", "//")) or "://" in value:
        raise ValueError("Use a local path, or a path relative to ComfyUI/input.")
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    root = Path(folder_paths.get_input_directory()).resolve()
    path = (root / path).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Relative image paths must stay inside ComfyUI/input. Use an absolute path for another local folder.")
    return path


def image_files(image_paths="", folder="", recursive=False):
    paths = [local_path(line) for line in image_paths.splitlines() if line.strip()]
    if folder.strip():
        root = local_path(folder)
        if not root.is_dir():
            raise ValueError(f"Image folder does not exist: {root}")
        found = root.rglob("*") if recursive else root.iterdir()
        for path in sorted(found, key=natural_key):
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
                if not path.resolve().is_relative_to(root):
                    raise ValueError(f"Image link leaves the selected folder: {path.name}")
                paths.append(path.resolve())
    paths = list(dict.fromkeys(paths))
    for path in paths:
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
            raise ValueError(f"Missing or unsupported image: {path}")
    return paths


def collect_sources(images=None, additional_images=None, image_paths="", folder="", recursive=False):
    sources = []
    batches = [("images", images)] + sorted((additional_images or {}).items(), key=lambda item: natural_key(item[0]))
    for name, batch in batches:
        if batch is None:
            continue
        if batch.ndim != 4 or not all(batch.shape[:3]) or batch.shape[-1] not in (3, 4):
            raise ValueError(f"{name}: expected a nonempty RGB/RGBA IMAGE batch.")
        sources.extend({"id": f"{name}[{i}]", "pixels": batch[i:i + 1]} for i in range(batch.shape[0]))
    paths = image_files(image_paths, folder, recursive)
    # Stable IDs distinguish same-named files without embedding absolute source paths.
    sources.extend({"id": f"file[{i}]/{path.name}", "path": path} for i, path in enumerate(paths))
    if not sources:
        raise ValueError("Add images, enter image file paths, or select a folder first.")
    return sources


def background_rgb(background):
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", background):
        raise ValueError("Background must be a hex colour such as #ffffff.")
    return tuple(int(background[i:i + 2], 16) / 255 for i in (1, 3, 5))


def read_image(source, background):
    if "path" in source:
        with Image.open(source["path"]) as original:
            if getattr(original, "n_frames", 1) != 1:
                raise ValueError(f"{source['id']}: animated images are not supported; export a still frame.")
            corrected = ImageOps.exif_transpose(original).convert("RGBA")
            image = torch.from_numpy(np.asarray(corrected).copy()).float().unsqueeze(0) / 255
    else:
        image = source["pixels"]
    image = image.detach().cpu().float()
    if not torch.isfinite(image).all():
        raise ValueError(f"{source['id']}: image contains non-finite pixels.")
    if image.shape[-1] == 4:
        alpha = image[..., 3:4]
        image = image[..., :3] * alpha + image.new_tensor(background) * (1 - alpha)
    return image


def pixel_hash(image):
    array = image.contiguous().numpy()
    return hashlib.sha256(str(array.shape).encode() + array.tobytes()).hexdigest()


def plan_images(images=None, additional_images=None, image_paths="", folder="", recursive=False,
                short_edge=768, selected_indices="", deduplicate=True, max_tokens=8192,
                overflow_policy="error", resize_mode="center crop", background="#ffffff"):
    if short_edge <= 0 or max_tokens < 0:
        raise ValueError("Resolution must be positive and token budget non-negative.")
    if resize_mode not in ("center crop", "fit with padding", "stretch"):
        raise ValueError("Unknown image resize mode.")
    if overflow_policy not in ("error", "first selected"):
        raise ValueError("Unknown RefMod overflow policy.")
    colour = background_rgb(background)
    sources = collect_sources(images, additional_images, image_paths, folder, recursive)
    try:
        # Tolerate spaces, line breaks and a trailing comma from hand-edited lists.
        indices = [int(i) for i in re.split(r"[,\s]+", selected_indices.strip()) if i] or list(range(len(sources)))
    except ValueError:
        indices = []
    if not indices or len(set(indices)) != len(indices) or any(i < 0 or i >= len(sources) for i in indices):
        raise ValueError(f"Select distinct, zero-based image indices from 0 to {len(sources) - 1}, e.g. 0,2,1.")
    omitted = [{"index": i, "reason": "not selected"} for i in range(len(sources)) if i not in indices]
    seen, kept, hashes, sizes = {}, [], {}, {}
    for index in indices:
        comfy.model_management.throw_exception_if_processing_interrupted()
        pixels = read_image(sources[index], colour)
        digest = pixel_hash(pixels)
        hashes[str(index)] = digest
        sizes[index] = (pixels.shape[2], pixels.shape[1])
        if deduplicate and digest in seen:
            omitted.append({"index": index, "reason": "exact duplicate", "duplicate_of": seen[digest]})
        else:
            seen[digest] = index
            kept.append(index)
    w, h = sizes[kept[0]]
    scale = min(1.0, short_edge / min(w, h))
    width, height = max(32, round(w * scale / 32) * 32), max(32, round(h * scale / 32) * 32)
    per_image = (width // 32) * (height // 32)
    tokens_before_fit = len(kept) * per_image
    if max_tokens and tokens_before_fit > max_tokens:
        capacity = max_tokens // per_image
        if capacity < 1:
            raise ValueError(f"One image at {width}x{height} needs {per_image} tokens; budget is {max_tokens}. "
                             "Lower short_edge or raise max_tokens.")
        if overflow_policy == "error":
            raise ValueError(f"Selected images need {tokens_before_fit} tokens; budget is {max_tokens}. "
                             "Select fewer views, lower resolution, or choose first selected.")
        omitted.extend({"index": i, "reason": "token budget"} for i in kept[capacity:])
        kept = kept[:capacity]
    info = []
    for index, source in enumerate(sources):
        item = {"index": index, "id": source["id"], "selected": index in kept}
        if index in sizes:
            sw, sh = sizes[index]
            lost = 1 - min((sw / sh) / (width / height), (width / height) / (sw / sh))
            item.update(size=[sw, sh], crop_percent=round(100 * lost, 2)
                        if resize_mode == "center crop" and (sw, sh) != (w, h) else 0)
        info.append(item)
    report = dict(canvas=[width, height], anchor_source_size=[w, h], anchor_index=kept[0], selected_indices=kept, omitted=omitted,
                  sources=info, source_pixel_sha256=hashes, input_count=len(sources),
                  max_tokens=max_tokens, overflow_policy=overflow_policy, deduplicate=deduplicate,
                  resize_mode=resize_mode, background=background, tokens_per_image=per_image,
                  tokens_before_fit=tokens_before_fit, tokens=len(kept) * per_image)
    return sources, report


def prepared_image(source, index, report):
    colour = background_rgb(report["background"])
    pixels = read_image(source, colour)
    if pixel_hash(pixels) != report["source_pixel_sha256"][str(index)]:
        raise ValueError(f"{source['id']} changed after inspection. Queue again.")
    width, height = report["canvas"]
    samples = pixels.movedim(-1, 1)
    mode = report["resize_mode"]
    if mode == "center crop" and [pixels.shape[2], pixels.shape[1]] == report["anchor_source_size"]:
        mode = "stretch"  # Preserve the existing IMAGE-batch path and Studio's anchor alignment.
    if mode == "fit with padding":
        scale = min(width / pixels.shape[2], height / pixels.shape[1])
        rw, rh = max(1, round(pixels.shape[2] * scale)), max(1, round(pixels.shape[1] * scale))
        resized = comfy.utils.common_upscale(samples, rw, rh, "lanczos", "disabled").movedim(1, -1)
        result = pixels.new_tensor(colour).expand(1, height, width, 3).clone()
        x, y = (width - rw) // 2, (height - rh) // 2
        result[:, y:y + rh, x:x + rw] = resized
        return result
    return comfy.utils.common_upscale(samples, width, height, "lanczos",
                                     "center" if mode == "center crop" else "disabled").movedim(1, -1)


def source_fingerprint(image_paths="", folder="", recursive=False, **kwargs):
    # Also detects additions/deletions, so a cached folder node is never a frozen file list.
    return [(str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in image_files(image_paths, folder, recursive)]
