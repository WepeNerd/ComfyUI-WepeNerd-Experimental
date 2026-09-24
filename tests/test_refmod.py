import importlib
import json
import math
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import torch
from PIL import Image
from safetensors.torch import save_file

import comfy.cli_args

# ComfyUI otherwise probes CUDA on import; the tests only need the CPU.
if not torch.cuda.is_available():
    comfy.cli_args.args.cpu = True

# Import just the RefMod modules, without initializing unrelated 3D HTTP routes.
package = types.ModuleType("wepenerd_refmod_tests")
package.__path__ = [str(Path(__file__).resolve().parents[1])]
sys.modules[package.__name__] = package
core = importlib.import_module(package.__name__ + ".refmod_core")
nodes = importlib.import_module(package.__name__ + ".refmod_node")
images_module = importlib.import_module(package.__name__ + ".refmod_images")
DEFAULT_IMAGES = object()


class VisualVAE:
    latent_channels = 24

    def __init__(self):
        self.calls = []

    def spacial_compression_encode(self):
        return 16

    def encode(self, image):
        self.calls.append(image.clone())
        return torch.full((1, 24, 1, image.shape[1] // 16, image.shape[2] // 16), image.mean().item())


class RefModTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        paths = patch.dict(nodes.folder_paths.folder_names_and_paths,
                           {"refmods": ([str(self.root)], {".safetensors"})})
        paths.start()
        self.addCleanup(paths.stop)

    def create(self, images=DEFAULT_IMAGES, **settings):
        if images is DEFAULT_IMAGES:
            images = torch.stack([torch.zeros(64, 64, 3), torch.ones(64, 64, 3), torch.zeros(64, 64, 3)])
        vae = VisualVAE()
        args = dict(images=images, vae=vae, name="test", description="subject", concept_type="identity",
                    short_edge=64, selected_indices="", deduplicate=True, max_tokens=8, overflow_policy="error")
        args.update(settings)
        ref, report = nodes.WN_H3RefModCreate().create(**args)
        return ref, json.loads(report), vae

    def test_aba_deduplicates_before_encoding(self):
        ref, report, vae = self.create()
        self.assertEqual(len(vae.calls), 2)
        self.assertEqual(report["selected_indices"], [0, 1])
        self.assertEqual(report["omitted"], [{"index": 2, "reason": "exact duplicate", "duplicate_of": 0}])
        self.assertEqual(ref.token_count, 8)
        self.assertEqual(ref.metadata["kind"], "video")
        self.assertEqual(ref.latent[0, 0, :, 0, 0].tolist(), [0, 1])

    def test_selection_order_and_budget(self):
        ref, report, vae = self.create(selected_indices="1,2,0", max_tokens=4, overflow_policy="first selected")
        self.assertEqual(report["selected_indices"], [1])
        self.assertEqual(ref.metadata["kind"], "image")
        self.assertEqual(len(vae.calls), 1)
        self.assertTrue(any(item["reason"] == "token budget" for item in report["omitted"]))

    def test_overflow_fails_without_vae_work(self):
        vae = VisualVAE()
        with self.assertRaisesRegex(ValueError, "budget"):
            self.create(vae=vae, max_tokens=4)
        self.assertEqual(vae.calls, [])

    def test_keep_intentional_duplicates_unlimited(self):
        ref, report, vae = self.create(deduplicate=False, max_tokens=0)
        self.assertEqual(len(vae.calls), 3)
        self.assertEqual(ref.token_count, 12)

    def test_invalid_indices_and_policy(self):
        for indices in ("-1", "3", "0,0", "hello"):
            with self.subTest(indices=indices), self.assertRaises(ValueError):
                self.create(selected_indices=indices)
        with self.assertRaises(ValueError):
            self.create(overflow_policy="silently average")

    def test_portrait_canvas_and_independent_encoding(self):
        ref, report, vae = self.create(images=torch.rand(2, 128, 64, 3), max_tokens=0)
        self.assertEqual(report["canvas"], [64, 128])
        self.assertEqual(ref.latent.shape, (1, 24, 2, 8, 4))
        self.assertTrue(all(image.shape == (1, 128, 64, 3) for image in vae.calls))

    def test_cancel_before_vae_and_save(self):
        vae = VisualVAE()
        with patch.object(nodes.comfy.model_management, "throw_exception_if_processing_interrupted", side_effect=RuntimeError("cancelled")):
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                self.create(vae=vae)
            ref = core.RefMod(torch.zeros(1, 24, 1, 4, 4), {"kind": "image"})
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                nodes.WN_H3RefModSave().save(ref, "cancelled.safetensors")
        self.assertEqual(vae.calls, [])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_roundtrip_all_kinds_and_atomic_overwrite(self):
        for kind, shape in (("image", (1, 24, 1, 4, 6)), ("video", (1, 24, 3, 4, 6)), ("audio", (1, 32, 2, 5))):
            ref = core.RefMod(torch.rand(shape), nodes.metadata("ref", kind, "description", "generic"))
            filename = f"sub/{kind}.safetensors"
            path, = nodes.WN_H3RefModSave().save(ref, filename)
            loaded, _ = nodes.WN_H3RefModLoad().load(filename)
            self.assertTrue(torch.equal(ref.latent, loaded.latent))
            self.assertEqual(ref.metadata, loaded.metadata)
            with self.assertRaises(FileExistsError):
                nodes.WN_H3RefModSave().save(ref, filename)
            nodes.WN_H3RefModSave().save(ref, filename, overwrite=True)
            self.assertEqual(list(Path(path).parent.glob(".refmod-*")), [])

    def test_legacy_audio_metadata_key(self):
        path = self.root / "studio.safetensors"
        meta = {"_format_version": 4, "kind": "audio", "latent_t": 3, "latent_h": 0, "latent_w": 0}
        save_file({"latent": torch.ones(1, 32, 2, 3)}, str(path), metadata={"audio_refmod_meta": json.dumps(meta)})
        self.assertEqual(core.RefMod.load(str(path)).token_count, 6)

    def test_reject_bad_files(self):
        path = str(self.root / "bad.safetensors")
        for meta, latent in (({"kind": "image", "latent_h": 99}, torch.ones(1, 24, 1, 4, 4)),
                             ({"kind": "image"}, torch.ones(1, 4, 1, 4, 4)),
                             ({"kind": "image"}, torch.ones(1, 24, 2, 4, 4)),
                             ({"kind": "audio"}, torch.ones(1, 32, 1, 4)),
                             ({"kind": "audio"}, torch.tensor(1.0)),
                             ({"kind": "image"}, torch.full((1, 24, 1, 4, 4), float("nan"))),
                             ({"kind": "image", "_format_version": 5}, torch.ones(1, 24, 1, 4, 4))):
            save_file({"latent": latent}, path, metadata={"refmod_meta": json.dumps(meta)})
            with self.subTest(meta=meta), self.assertRaises(ValueError):
                core.RefMod.load(path)
        save_file({"latent": torch.ones(1)}, path)
        with self.assertRaisesRegex(ValueError, "metadata"):
            core.RefMod.load(path)

    def test_model_roots_and_path_containment(self):
        for filename in ("../escape.safetensors", "/outside.safetensors", "C:\\outside.safetensors",
                         "a/../../b.safetensors", "a:stream.safetensors", "bad.pt", "a//b.safetensors"):
            for saving in (False, True):
                with self.subTest(filename=filename, saving=saving), self.assertRaises(ValueError):
                    nodes.refmod_path(filename, saving)
        extra = self.root / "extra"
        extra.mkdir()
        ref = core.RefMod(torch.ones(1, 24, 1, 4, 4), {"kind": "image"})
        ref.save(str(extra / "extra.safetensors"))
        with patch.dict(nodes.folder_paths.folder_names_and_paths, {"refmods": ([str(self.root / "first"), str(extra)], {".safetensors"})}):
            self.assertEqual(nodes.refmod_path("extra.safetensors"), str(extra / "extra.safetensors"))
            self.assertEqual(nodes.refmod_path("new.safetensors", True), str(self.root / "first" / "new.safetensors"))

    def test_symlink_escape(self):
        inside = self.root / "inside"
        outside = self.root / "outside"
        inside.mkdir()
        outside.mkdir()
        try:
            (inside / "link").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Symlink creation unavailable")
        with patch.dict(nodes.folder_paths.folder_names_and_paths, {"refmods": ([str(inside)], {".safetensors"})}):
            with self.assertRaises(ValueError):
                nodes.refmod_path("link/escape.safetensors", True)

    def test_apply_preserves_each_conditioning_and_chains(self):
        visual, _, _ = self.create()
        audio = core.RefMod(torch.ones(1, 32, 2, 3), {"kind": "audio"})
        old = {"kind": "image"}
        positive = [[torch.zeros(1), {"minimax_refs": [old], "other": 7}], [torch.ones(1), {"other": 9}]]
        applied, = nodes.WN_H3RefModApply().apply(positive, visual)
        applied, = nodes.WN_H3RefModApply().apply(applied, audio)
        self.assertEqual([len(item[1]["minimax_refs"]) for item in applied], [3, 2])
        self.assertEqual(applied[0][1]["other"], 7)
        self.assertEqual(applied[1][1]["other"], 9)
        self.assertEqual(positive[0][1]["minimax_refs"], [old])
        self.assertNotIn("minimax_refs", positive[1][1])
        self.assertEqual(applied[0][1]["minimax_refs"][-1]["ref_audio_t"], 3)
        self.assertIs(nodes.WN_H3RefModApply().apply(positive, visual, False)[0], positive)

    def test_apply_separate_photo_layout(self):
        visual, _, _ = self.create()
        positive = [[torch.zeros(1), {}]]
        stacked, = nodes.WN_H3RefModApply().apply(positive, visual)
        split, = nodes.WN_H3RefModApply().apply(positive, visual, True, "separate images")
        self.assertEqual([b["kind"] for b in stacked[0][1]["minimax_refs"]], ["video"])
        blocks = split[0][1]["minimax_refs"]
        self.assertEqual([b["kind"] for b in blocks], ["image", "image"])
        self.assertTrue(torch.equal(torch.cat([b["latent"] for b in blocks], dim=2), visual.latent))
        self.assertEqual((blocks[0]["latent_h"], blocks[0]["latent_w"]), (4, 4))
        # The layout survives Save/Load; other files keep their single block.
        nodes.WN_H3RefModSave().save(visual, "photos.safetensors")
        loaded, _ = nodes.WN_H3RefModLoad().load("photos.safetensors")
        self.assertEqual(len(loaded.blocks(True)), 2)
        studio = core.RefMod(torch.ones(1, 24, 2, 4, 4), {"kind": "video"})
        audio = core.RefMod(torch.ones(1, 32, 2, 3), {"kind": "audio"})
        self.assertEqual([len(r.blocks(True)) for r in (studio, audio)], [1, 1])
        with self.assertRaises(ValueError):
            nodes.WN_H3RefModApply().apply(positive, visual, True, "average")

    def test_blocks_fit_native_h3_layout(self):
        try:
            from comfy.ldm.minimax.model import PackedLayout
        except ImportError:
            self.skipTest("ComfyUI without MiniMax H3")
        visual, _, _ = self.create()
        audio = core.RefMod(torch.ones(1, 32, 2, 3), {"kind": "audio"})
        for separate in (False, True):
            refs = visual.blocks(separate) + audio.blocks()
            layout = PackedLayout(7, 2, 4, 4, 5, refs=refs)
            ref_rows = sum(b["latent"].shape[2] * 4 for b in refs if "latent" in b) + 2 * 3
            self.assertEqual(layout.seq_len, 7 + ref_rows + 2 * 5 + 2 * 4)

    def test_index_parsing_and_budget_messages(self):
        _, report, _ = self.create(selected_indices=" 1,\n0, ", max_tokens=0)
        self.assertEqual(report["selected_indices"], [1, 0])
        with self.assertRaisesRegex(ValueError, "from 0 to 2"):
            self.create(selected_indices="0;1")
        with self.assertRaisesRegex(ValueError, "One image at 64x64 needs 4 tokens"):
            self.create(max_tokens=3, overflow_policy="first selected")

    def test_save_without_hard_links_and_empty_load(self):
        ref = core.RefMod(torch.ones(1, 24, 1, 4, 4), {"kind": "image"})
        with patch.object(core.os, "link", side_effect=PermissionError("no hard links")):
            path, = nodes.WN_H3RefModSave().save(ref, "nolink.safetensors")
            self.assertTrue(torch.equal(core.RefMod.load(path).latent, ref.latent))
            with self.assertRaises(FileExistsError):
                nodes.WN_H3RefModSave().save(ref, "nolink.safetensors")
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["nolink.safetensors"])
        with self.assertRaisesRegex(FileNotFoundError, "No RefMods found"):
            nodes.WN_H3RefModLoad().load("")

    def audio_vae(self):
        from comfy.ldm.minimax.audio_vae import MiniMaxH3AudioVAE

        class AudioVAE:
            def __init__(self):
                self.first_stage_model = MiniMaxH3AudioVAE.__new__(MiniMaxH3AudioVAE)
                self.calls = []

            def encode(self, audio):
                self.calls.append(audio.clone())
                return torch.ones(1, 32, 2, math.ceil(audio.shape[1] / 800))

        return AudioVAE()

    def test_audio_resamples_duplicates_mono_and_chunks(self):
        vae = self.audio_vae()
        ref, report = nodes.WN_H3RefModAudioCreate().create(
            {"waveform": torch.ones(1, 1, 12 * 16000), "sample_rate": 16000}, vae,
            "voice", "", 1, 11, 0, "error")
        self.assertEqual([tuple(a.shape) for a in vae.calls], [(1, 320000, 2), (1, 32000, 2)])
        self.assertTrue(torch.equal(vae.calls[0][..., 0], vae.calls[0][..., 1]))
        self.assertEqual(ref.token_count, 880)
        self.assertEqual(json.loads(report)["encoded_seconds"], 11)

    def test_audio_budget_before_encoding(self):
        audio = {"waveform": torch.ones(1, 2, 32000), "sample_rate": 32000}
        vae = self.audio_vae()
        with self.assertRaisesRegex(ValueError, "budget"):
            nodes.WN_H3RefModAudioCreate().create(audio, vae, "voice", "", 0, 1, 40, "error")
        self.assertEqual(vae.calls, [])
        ref, _ = nodes.WN_H3RefModAudioCreate().create(audio, vae, "voice", "", 0, 1, 40, "truncate")
        self.assertEqual(vae.calls[0].shape[1], 16000)
        self.assertEqual(ref.token_count, 40)

    def test_audio_invalid_excerpt(self):
        for waveform, start in ((torch.zeros(1, 1, 32000), 0), (torch.ones(1, 1, 32000), 2),
                                (torch.full((1, 1, 32000), float("nan")), 0)):
            vae = self.audio_vae()
            with self.assertRaises(ValueError):
                nodes.WN_H3RefModAudioCreate().create(
                    {"waveform": waveform, "sample_rate": 32000}, vae, "voice", "", start, 1, 0, "error")
            self.assertEqual(vae.calls, [])

    def test_audio_preserves_partial_hop(self):
        vae = self.audio_vae()
        ref, _ = nodes.WN_H3RefModAudioCreate().create(
            {"waveform": torch.ones(1, 1, 100), "sample_rate": 32000}, vae,
            "voice", "", 0, 1, 0, "error")
        self.assertEqual(vae.calls[0].shape, (1, 800, 2))
        self.assertTrue(torch.all(vae.calls[0][:, :100] == 1))
        self.assertTrue(torch.all(vae.calls[0][:, 100:] == 0))
        self.assertEqual(ref.token_count, 2)

    def write_image(self, name, size=(64, 64), colour="red"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", size, colour).save(path)
        return path

    def test_folder_natural_order_recursion_and_dedup(self):
        self.write_image("view10.png", colour="red")
        self.write_image("view2.png", colour="blue")
        self.write_image("sub/extra.png", colour="green")
        kwargs = dict(images=None, folder=str(self.root), short_edge=64, max_tokens=0)
        ref, report, vae = self.create(**kwargs)
        self.assertEqual([item["id"] for item in report["sources"]], ["file[0]/view2.png", "file[1]/view10.png"])
        self.assertEqual(len(vae.calls), 2)
        ref, report, vae = self.create(**kwargs, recursive=True)
        self.assertEqual(len(vae.calls), 3)
        ref, report, vae = self.create(**kwargs, image_paths=str(self.root / "view10.png"))
        self.assertEqual(report["input_count"], 2)
        self.assertEqual(report["sources"][0]["id"], "file[0]/view10.png")

    def test_multiple_sockets_and_files_mixed_sizes(self):
        file = self.write_image("wide.png", (128, 64), "red")
        ref, report, vae = self.create(images=torch.zeros(1, 128, 64, 3),
            additional_images={"image_10": torch.ones(1, 64, 96, 3), "image_2": torch.full((1, 80, 64, 3), 0.5)},
            image_paths=str(file), max_tokens=0, deduplicate=False)
        self.assertEqual(report["input_count"], 4)
        self.assertEqual([s["id"] for s in report["sources"]][:3], ["images[0]", "image_2[0]", "image_10[0]"])
        self.assertEqual(report["canvas"], [64, 128])
        self.assertEqual(report["sources"][-1]["crop_percent"], 75)
        self.assertTrue(all(tuple(p.shape) == (1, 128, 64, 3) for p in vae.calls))

    def test_preview_matches_vae_input_and_padding(self):
        portrait = self.write_image("portrait.png", (64, 128), "red")
        wide = self.write_image("wide.png", (128, 64), "blue")
        paths = str(portrait) + "\n" + str(wide)
        kwargs = dict(image_paths=paths, short_edge=64, max_tokens=0, resize_mode="fit with padding", background="#00ff00")
        preview, raw = nodes.WN_H3RefModPreview.preview(**kwargs)
        ref, report, vae = self.create(images=None, **kwargs)
        self.assertTrue(torch.equal(preview, torch.cat(vae.calls)))
        self.assertEqual(json.loads(raw)["selected_indices"], report["selected_indices"])
        self.assertTrue(torch.equal(preview[1, 0, 0], torch.tensor([0., 1., 0.])))
        self.assertTrue(torch.equal(preview[1, 64, 32], torch.tensor([0., 0., 1.])))

    def test_connected_batch_alignment_unchanged(self):
        batch = torch.rand(1, 75, 101, 3)
        _, _, vae = self.create(images=batch, max_tokens=0)
        expected = nodes.comfy.utils.common_upscale(batch.movedim(-1, 1), 96, 64, "lanczos", "disabled").movedim(1, -1)
        self.assertTrue(torch.equal(vae.calls[0], expected))

    def test_selected_file_sets_anchor_and_omitted_not_encoded(self):
        a = self.write_image("a.png", (64, 128), "red")
        b = self.write_image("b.png", (128, 64), "blue")
        ref, report, vae = self.create(images=None, image_paths=f"{a}\n{b}", selected_indices="1", max_tokens=0)
        self.assertEqual(report["canvas"], [128, 64])
        self.assertEqual(report["anchor_index"], 1)
        self.assertEqual(len(vae.calls), 1)

    def test_exif_orientation_and_transparency(self):
        path = self.root / "rotate.jpg"
        exif = Image.Exif()
        exif[274] = 6
        Image.new("RGB", (64, 128), "red").save(path, exif=exif)
        _, report, _ = self.create(images=None, image_paths=str(path), max_tokens=0)
        self.assertEqual(report["canvas"], [128, 64])
        alpha = self.root / "alpha.png"
        Image.new("RGBA", (64, 64), (255, 0, 0, 0)).save(alpha)
        _, _, vae = self.create(images=None, image_paths=str(alpha), background="#0000ff")
        self.assertTrue(torch.equal(vae.calls[0][0, 0, 0], torch.tensor([0., 0., 1.])))

    def test_relative_paths_corrupt_files_and_empty_folder(self):
        with patch.object(nodes.folder_paths, "get_input_directory", return_value=str(self.root)):
            p = self.write_image("sub/reference.png")
            _, report, _ = self.create(images=None, image_paths='"sub/reference.png"')
            self.assertEqual(report["input_count"], 1)
            with self.assertRaisesRegex(ValueError, "stay inside"):
                images_module.image_files("../outside.png")
            p.write_text("not an image")
            with self.assertRaises(OSError):
                self.create(images=None, image_paths=str(p))
        empty = self.root / "empty"
        empty.mkdir()
        with self.assertRaisesRegex(ValueError, "Add images"):
            nodes.WN_H3RefModCreate().create(vae=VisualVAE(), folder=str(empty))

    def test_file_cache_invalidation_and_changes_during_preparation(self):
        first = self.write_image("first.png")
        kwargs = {"folder": str(self.root)}
        old = nodes.WN_H3RefModCreate.fingerprint_inputs(**kwargs)
        second = self.write_image("second.png", colour="blue")
        self.assertNotEqual(old, nodes.WN_H3RefModCreate.fingerprint_inputs(**kwargs))
        sources, report = images_module.plan_images(**kwargs)
        self.write_image("first.png", colour="green")
        with self.assertRaisesRegex(ValueError, "changed after inspection"):
            images_module.prepared_image(sources[0], 0, report)
        second.unlink()
        self.assertNotEqual(old, nodes.WN_H3RefModCreate.fingerprint_inputs(**kwargs))

    def test_v3_schema_and_original_node_contract(self):
        for cls in (nodes.WN_H3RefModCreate, nodes.WN_H3RefModPreview):
            schema = cls.GET_SCHEMA()
            inputs = cls.INPUT_TYPES()
            self.assertIn("images", inputs["optional"])
            self.assertIn("additional_images", inputs["optional"])
            self.assertIn("folder", inputs["optional"])
        self.assertEqual(tuple(nodes.WN_H3RefModCreate.RETURN_TYPES), ("WN_H3_REFMOD", "STRING"))


if __name__ == "__main__":
    unittest.main()
