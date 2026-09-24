"""Create, save, load, and apply H3 RefMods without another custom-node pack."""

import json
import math
import ntpath
import os
from pathlib import Path

import torch
import torch.nn.functional as F
import comfy.model_management
import comfy.utils
import folder_paths
import node_helpers

from comfy_api.latest import io, ui

from .refmod_core import RefMod
from .refmod_images import plan_images, prepared_image, source_fingerprint


folder_paths.add_model_folder_path("refmods", os.path.join(folder_paths.models_dir, "refmods"))
folder_paths.folder_names_and_paths["refmods"][1].add(".safetensors")


def resample(waveform, orig_freq, new_freq):
    # ComfyUI no longer requires torchaudio; older H3 builds predate comfy.audio.
    try:
        from comfy.audio import resample as backend
    except ImportError:
        from torchaudio.functional import resample as backend
    return backend(waveform, orig_freq, new_freq)


def refmod_path(filename, saving=False):
    if not filename and not saving:
        raise FileNotFoundError("No RefMods found. Copy .safetensors files into ComfyUI/models/refmods and refresh.")
    relative = filename.replace("\\", "/")
    if (not relative or ntpath.splitdrive(relative)[0] or relative.startswith("/")
            or any(p in ("", ".", "..") or ":" in p for p in relative.split("/"))
            or not relative.endswith(".safetensors")):
        raise ValueError("Use a relative .safetensors filename inside a registered refmods folder.")
    roots = folder_paths.get_folder_paths("refmods")
    for root in roots[:1] if saving else roots:
        resolved_root = Path(root).resolve()
        candidate = (resolved_root / relative).resolve()
        if not candidate.is_relative_to(resolved_root):
            raise ValueError("RefMod path leaves its registered model folder.")
        if saving or candidate.is_file():
            return str(candidate)
    raise FileNotFoundError(f"RefMod not found: {filename}")


def metadata(name, kind, description, concept_type):
    return {"name": name, "kind": kind, "description": description, "concept_type": concept_type,
            "mode": "encode", "optimize_steps": 0, "tags": [], "_format_version": 4,
            "sample_rate": 32000, "creator": "WepeNerd Experimental", "preprocessing_version": 2}


def image_inputs():
    return [
        io.Int.Input("short_edge", default=768, min=32, max=4096, step=32),
        io.String.Input("selected_indices", default="", tooltip="Zero-based indices, e.g. 0,2,1. Empty selects all. First selected image sets the canvas."),
        io.Boolean.Input("deduplicate", default=True),
        io.Int.Input("max_tokens", default=8192, min=0, max=1000000, tooltip="0 means unlimited."),
        io.Combo.Input("overflow_policy", options=["error", "first selected"]),
        io.Image.Input("images", optional=True),
        io.String.Input("image_paths", default="", multiline=True, optional=True,
                        tooltip="One file per line, in order. Absolute local paths or paths relative to ComfyUI/input. Use Add images to upload several files."),
        io.String.Input("folder", default="", optional=True,
                        tooltip="Folder on the ComfyUI machine; absolute path or relative to ComfyUI/input. Folder files are naturally sorted after explicit files."),
        io.Boolean.Input("recursive", default=False, optional=True),
        io.Combo.Input("resize_mode", options=["center crop", "fit with padding", "stretch"], optional=True),
        io.String.Input("background", default="#ffffff", optional=True, tooltip="RGB hex colour for transparent pixels and fit padding."),
        io.Autogrow.Input("additional_images", optional=True,
            template=io.Autogrow.TemplatePrefix(io.Image.Input("image"), prefix="image_", min=0, max=100)),
    ]


class WN_H3RefModCreate(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="WN_H3RefModCreate", display_name="Create H3 Visual RefMod (WepeNerd)",
            category="WepeNerd/H3 RefMod",
            description="Encode separate images, batches, files, or a folder. Identity/style labels are metadata; no training is performed.",
            inputs=[io.Vae.Input("vae"), io.String.Input("name", default="reference"),
                    io.String.Input("description", default="", multiline=True),
                    io.Combo.Input("concept_type", options=["generic", "identity", "style", "clothing", "background", "pose_motion"]),
                    *image_inputs()],
            outputs=[io.Custom("WN_H3_REFMOD").Output(display_name="refmod"), io.String.Output(display_name="report")])

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        return source_fingerprint(**kwargs)

    @classmethod
    def execute(cls, **kwargs):
        return io.NodeOutput(*cls().create(**kwargs))

    def create(self, images=None, vae=None, name="reference", description="", concept_type="generic",
               short_edge=768, selected_indices="", deduplicate=True, max_tokens=8192, overflow_policy="error",
               additional_images=None, image_paths="", folder="", recursive=False, resize_mode="center crop", background="#ffffff"):
        if vae is None or vae.latent_channels != 24 or vae.spacial_compression_encode() != 16:
            raise ValueError("Connect the MiniMax H3 visual VAE.")
        sources, report = plan_images(images, additional_images, image_paths, folder, recursive,
                                      short_edge, selected_indices, deduplicate, max_tokens, overflow_policy, resize_mode, background)
        latents = []
        progress = comfy.utils.ProgressBar(len(report["selected_indices"]))
        for index in report["selected_indices"]:
            comfy.model_management.throw_exception_if_processing_interrupted()
            pixels = prepared_image(sources[index], index, report)
            z = vae.encode(pixels).detach().cpu()
            ref = RefMod(z, {"kind": "image"})
            if ref.token_count != report["tokens_per_image"]:
                raise ValueError("H3 VAE output does not match the planned canvas/token count.")
            latents.append(z)
            progress.update(1)
        meta = metadata(name, "image" if len(latents) == 1 else "video", description, concept_type)
        meta.update(report, source="images", representation="independently_encoded_photos")
        ref = RefMod(torch.cat(latents, dim=2), meta)
        return ref, ref.report()


class WN_H3RefModPreview(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="WN_H3RefModPreview", display_name="Preview H3 RefMod Images (WepeNerd)",
            category="WepeNerd/H3 RefMod", is_output_node=True,
            description="Check images, selected views, crops/padding, and token cost before loading a VAE. Thumbnails follow selected_indices order.",
            inputs=image_inputs(), outputs=[io.Image.Output(display_name="thumbnails"), io.String.Output(display_name="report")])

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        return source_fingerprint(**kwargs)

    @classmethod
    def execute(cls, **kwargs):
        thumbnails, report = cls.preview(**kwargs)
        return io.NodeOutput(thumbnails, report, ui=ui.PreviewImage(thumbnails))

    @classmethod
    def preview(cls, **kwargs):
        sources, report = plan_images(**kwargs)
        width, height = report["canvas"]
        scale = min(1, 512 / max(width, height))
        previews = []
        for index in report["selected_indices"]:
            comfy.model_management.throw_exception_if_processing_interrupted()
            pixels = prepared_image(sources[index], index, report)
            preview = comfy.utils.common_upscale(pixels.movedim(-1, 1), max(1, round(width * scale)),
                                                max(1, round(height * scale)), "lanczos", "disabled").movedim(1, -1)
            previews.append(preview)
        return torch.cat(previews), json.dumps(report, indent=2, ensure_ascii=False)


class WN_H3RefModAudioCreate:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "audio": ("AUDIO",), "audio_vae": ("VAE",),
            "name": ("STRING", {"default": "voice"}),
            "description": ("STRING", {"default": "", "multiline": True}),
            "start_seconds": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 86400.0}),
            "duration_seconds": ("FLOAT", {"default": 10.0, "min": 0.025, "max": 600.0}),
            "max_tokens": ("INT", {"default": 2400, "min": 0, "max": 1000000, "tooltip": "0 means unlimited."}),
            "overflow_policy": (["error", "truncate"],),
        }}

    RETURN_TYPES = ("WN_H3_REFMOD", "STRING")
    RETURN_NAMES = ("refmod", "report")
    FUNCTION = "create"
    CATEGORY = "WepeNerd/H3 RefMod"
    DESCRIPTION = "Encode an audio excerpt with the H3 audio VAE in chunks of up to 10 seconds. No visual VAE is required."

    def create(self, audio, audio_vae, name, description, start_seconds, duration_seconds, max_tokens, overflow_policy):
        # Optional backend import: other nodes can load on ComfyUI versions without H3 audio.
        from comfy.ldm.minimax.audio_vae import MiniMaxH3AudioVAE

        if not isinstance(audio_vae.first_stage_model, MiniMaxH3AudioVAE):
            raise ValueError("Connect the MiniMax H3 audio VAE.")
        waveform, sr = audio["waveform"], int(audio["sample_rate"])
        if waveform.ndim != 3 or waveform.shape[0] != 1 or waveform.shape[1] not in (1, 2) or sr <= 0:
            raise ValueError("Audio must be one mono/stereo waveform [1,C,L] with a positive sample rate.")
        if (not math.isfinite(start_seconds) or not math.isfinite(duration_seconds)
                or start_seconds < 0 or duration_seconds <= 0 or max_tokens < 0):
            raise ValueError("Invalid audio excerpt or token budget.")
        if overflow_policy not in ("error", "truncate"):
            raise ValueError("Unknown audio overflow policy.")
        start = round(start_seconds * sr)
        excerpt = waveform[..., start:start + round(duration_seconds * sr)]
        if excerpt.shape[-1] == 0 or not torch.isfinite(excerpt).all() or not torch.count_nonzero(excerpt):
            raise ValueError("The selected audio excerpt is empty, silent, or contains non-finite samples.")
        actual_seconds = excerpt.shape[-1] / sr
        planned_frames = math.ceil(excerpt.shape[-1] * 40 / sr)
        if max_tokens and planned_frames * 2 > max_tokens:
            if overflow_policy == "error" or max_tokens < 2:
                raise ValueError(f"Audio needs about {planned_frames * 2} tokens; budget is {max_tokens}.")
            excerpt = excerpt[..., :max(1, math.floor(max_tokens // 2 * sr / 40))]
        if excerpt.shape[1] == 1:
            excerpt = excerpt.repeat(1, 2, 1)
        if sr != 32000:
            excerpt = resample(excerpt.float(), sr, 32000)
        latents = []
        progress = comfy.utils.ProgressBar(math.ceil(excerpt.shape[-1] / 320000))
        for offset in range(0, excerpt.shape[-1], 320000):
            comfy.model_management.throw_exception_if_processing_interrupted()
            piece = excerpt[..., offset:offset + 320000]
            # The generic VAE wrapper crops to /800; preserve the final partial hop.
            piece = F.pad(piece, (0, (-piece.shape[-1]) % 800))
            z = audio_vae.encode(piece.movedim(1, -1)).detach().cpu()
            RefMod(z, {"kind": "audio"})
            latents.append(z)
            progress.update(1)
        latent = torch.cat(latents, dim=-1)
        if max_tokens and latent.shape[-1] * 2 > max_tokens:
            if overflow_policy == "error":
                raise ValueError("Encoded audio exceeds the token budget; shorten the excerpt or choose truncate.")
            latent = latent[..., :max_tokens // 2].clone()
        meta = metadata(name, "audio", description, "voice")
        meta.update(source="audio", start_seconds=start_seconds, requested_seconds=duration_seconds,
                    available_excerpt_seconds=actual_seconds, encoded_seconds=latent.shape[-1] / 40,
                    max_tokens=max_tokens, overflow_policy=overflow_policy, chunk_seconds=10)
        ref = RefMod(latent, meta)
        return ref, ref.report()


class WN_H3RefModSave:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"refmod": ("WN_H3_REFMOD",),
                             "filename": ("STRING", {"default": "reference.safetensors"}),
                             "overwrite": ("BOOLEAN", {"default": False})}}

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("saved_path",)
    FUNCTION = "save"
    CATEGORY = "WepeNerd/H3 RefMod"
    OUTPUT_NODE = True

    def save(self, refmod, filename, overwrite=False):
        path = refmod_path(filename, saving=True)
        comfy.model_management.throw_exception_if_processing_interrupted()
        refmod.save(path, overwrite)
        return (path,)


class WN_H3RefModLoad:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"filename": (folder_paths.get_filename_list("refmods") or [""],)}}

    RETURN_TYPES = ("WN_H3_REFMOD", "STRING")
    RETURN_NAMES = ("refmod", "report")
    FUNCTION = "load"
    CATEGORY = "WepeNerd/H3 RefMod"

    @classmethod
    def IS_CHANGED(cls, filename):
        stat = os.stat(refmod_path(filename))
        return stat.st_mtime_ns, stat.st_size

    def load(self, filename):
        ref = RefMod.load(refmod_path(filename))
        return ref, ref.report()


class WN_H3RefModApply:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"positive": ("CONDITIONING",), "refmod": ("WN_H3_REFMOD",),
                             "enabled": ("BOOLEAN", {"default": True})},
                "optional": {"photo_layout": (["video stack", "separate images"], {
                    "tooltip": "For several photos made by Create: 'video stack' matches Studio; "
                               "'separate images' adds one native image reference per photo. "
                               "Loaded Studio files and single images are unaffected."})}}

    RETURN_TYPES = ("CONDITIONING",)
    RETURN_NAMES = ("positive",)
    FUNCTION = "apply"
    CATEGORY = "WepeNerd/H3 RefMod"
    DESCRIPTION = "Append a saved reference to H3 positive conditioning. Chain for multiple references. This does not supply reference pixels or numbered tags to the text encoder."

    def apply(self, positive, refmod, enabled=True, photo_layout="video stack"):
        if not enabled:
            return (positive,)
        if photo_layout not in ("video stack", "separate images"):
            raise ValueError("Unknown RefMod photo layout.")
        blocks = refmod.blocks(photo_layout == "separate images")
        result = []
        for conditioning in positive:
            refs = list(conditioning[1].get("minimax_refs", []))
            result.extend(node_helpers.conditioning_set_values([conditioning], {"minimax_refs": refs + blocks}))
        return (result,)
