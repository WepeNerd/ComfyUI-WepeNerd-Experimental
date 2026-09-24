"""Studio-compatible H3 reference storage and native conditioning blocks.

Adapted from Luisa's MIT RefMod implementation; see licenses/refmod-LICENSE.
"""

import json
import os
import tempfile
from dataclasses import dataclass

import torch
from safetensors import safe_open
from safetensors.torch import save_file


@dataclass
class RefMod:
    latent: torch.Tensor
    metadata: dict

    def __post_init__(self):
        kind = self.metadata.get("kind")
        z = self.latent
        if kind == "audio":
            valid = z.ndim == 4 and tuple(z.shape[:3]) == (1, 32, 2) and z.shape[-1] > 0
        elif kind in ("image", "video"):
            valid = (z.ndim == 5 and tuple(z.shape[:2]) == (1, 24)
                     and all(n > 0 for n in z.shape[2:])
                     and z.shape[-2] % 2 == 0 and z.shape[-1] % 2 == 0)
            if kind == "image":
                valid = valid and z.shape[2] == 1
        else:
            raise ValueError("RefMod kind must be image, video, or audio.")
        if not valid or not z.is_floating_point() or not torch.isfinite(z).all():
            raise ValueError(f"Invalid H3 {kind} RefMod latent: {tuple(z.shape)}. Connect the matching H3 VAE.")
        dims = ({"latent_t": z.shape[-1], "latent_h": 0, "latent_w": 0} if kind == "audio"
                else dict(zip(("latent_t", "latent_h", "latent_w"), z.shape[2:])))
        for key, value in dims.items():
            if key in self.metadata and self.metadata[key] != value:
                raise ValueError(f"RefMod metadata {key} does not match its tensor.")
        self.metadata = {**self.metadata, **dims}

    @property
    def token_count(self):
        if self.metadata["kind"] == "audio":
            return 2 * self.latent.shape[-1]
        return self.latent.shape[2] * (self.latent.shape[3] // 2) * (self.latent.shape[4] // 2)

    def block(self):
        kind = self.metadata["kind"]
        if kind == "audio":
            return {"kind": kind, "audio_latent": self.latent, "ref_audio_t": self.latent.shape[-1]}
        block = {"kind": kind, "latent": self.latent,
                 "latent_h": self.latent.shape[3], "latent_w": self.latent.shape[4]}
        if kind == "video":
            block.update(latent_t=self.latent.shape[2], ref_audio_t=0, audio_latent=None)
        return block

    def blocks(self, separate_photos=False):
        # Each photo in a created stack is a single-frame encode, i.e. a native image block.
        if (separate_photos and self.metadata["kind"] == "video"
                and self.metadata.get("representation") == "independently_encoded_photos"):
            return [{"kind": "image", "latent": self.latent[:, :, i:i + 1],
                     "latent_h": self.latent.shape[3], "latent_w": self.latent.shape[4]}
                    for i in range(self.latent.shape[2])]
        return [self.block()]

    def report(self):
        return json.dumps({**self.metadata, "tokens": self.token_count,
                           "shape": list(self.latent.shape)}, indent=2, ensure_ascii=False)

    def save(self, path, overwrite=False):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".refmod-", suffix=".tmp", dir=os.path.dirname(path))
        os.close(fd)
        try:
            save_file({"latent": self.latent.detach().cpu().contiguous()}, temporary,
                      metadata={"refmod_meta": json.dumps(self.metadata)})
            if overwrite:
                os.replace(temporary, path)
            else:
                # Publish a complete file atomically, without racing another save.
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    raise
                except OSError:
                    # No hard links (e.g. exFAT, some network shares): claim the name, then swap in.
                    os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                    os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @classmethod
    def load(cls, path):
        with safe_open(path, framework="pt", device="cpu") as handle:
            header = handle.metadata() or {}
            raw = header.get("refmod_meta", header.get("audio_refmod_meta"))
            if raw is None:
                raise ValueError("Missing embedded RefMod metadata. Use a Studio 1.2 safetensors export.")
            metadata = json.loads(raw)
            if not isinstance(metadata, dict):
                raise ValueError("RefMod metadata must be a JSON object.")
            if metadata.get("_format_version", 4) != 4 or set(handle.keys()) != {"latent"}:
                raise ValueError("Expected a single-latent version-4 RefMod; bundles are not supported.")
            latent = handle.get_tensor("latent").clone()
        return cls(latent, metadata)
