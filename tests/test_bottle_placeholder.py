import importlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]

# Import just the placeholder modules, without initializing unrelated 3D HTTP routes.
package = types.ModuleType("wepenerd_bottle_tests")
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package
core = importlib.import_module(package.__name__ + ".bottle_placeholder_core")
node_module = importlib.import_module(package.__name__ + ".bottle_placeholder_node")

PARITY_CASES = [
    {},
    {"shape": "cylinder"},
    {"shape": "cylinder", "rotate_y": 63, "camera_pitch": 38, "pos_x": 0.27, "camera_orbit": -25},
    {"rotate_x": 35, "rotate_z": -20, "rotate_y": 140, "fov": 95, "camera_roll": 12, "scale": 4.5},
    {"camera_pitch": -30, "pos_y": 0.35, "front_label": False, "outline_width": 0, "grid_spacing": 2.5},
    {"shape": "cylinder", "rotate_x": 90, "body_width": 10, "body_depth": 6, "neck_height": 4, "fov": 12},
    {"fov": 120, "scale": 14, "camera_pitch": 5, "pos_y": 1.2},
    {"style": "outline", "outline_color": "#FFE000", "outline_width": 5, "rotate_y": 25},
    {"style": "outline", "shape": "cylinder", "rotate_y": 100, "camera_pitch": 30, "outline_color": "#0f0"},
]

JS_DUMP = """
import { DEFAULTS, normalizeParams, makeCamera, buildBottle, buildGrid } from %s;
const out = JSON.parse(process.argv[1]).map(({ params, width, height }) => {
    const p = normalizeParams({ ...DEFAULTS, ...params });
    const cam = makeCamera(p, width, height);
    return { bottle: buildBottle(p, cam), grid: buildGrid(p, cam) };
});
console.log(JSON.stringify({ defaults: DEFAULTS, out }));
"""


def flatten(value):
    if isinstance(value, dict):
        for key in sorted(value):
            yield from flatten(value[key])
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from flatten(item)
    else:
        yield value


def gradient(width=320, height=240, batch=1):
    ramp = np.linspace(0.2, 0.8, width, dtype=np.float32)
    image = np.broadcast_to(ramp[None, :, None], (height, width, 3))
    return np.stack([image * (0.5 + 0.5 * index / max(1, batch)) for index in range(batch)]).astype(np.float32)


class GeometryParityTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node is required to compare with the browser geometry")
    def test_python_and_javascript_build_the_same_draw_list(self):
        cases = [{"params": params, "width": 1200, "height": 900} for params in PARITY_CASES]
        cases.append({"params": {"rotate_y": 20}, "width": 768, "height": 1344})
        module_url = json.dumps((ROOT / "js" / "wn_bottle_geometry.mjs").as_uri())
        result = subprocess.run(
            ["node", "--input-type=module", "-e", JS_DUMP % module_url, json.dumps(cases)],
            capture_output=True, text=True, check=True,
        )
        browser = json.loads(result.stdout)
        self.assertEqual(browser["defaults"], core.DEFAULTS)
        for case, theirs in zip(cases, browser["out"]):
            p = core.normalize_params({**core.DEFAULTS, **case["params"]})
            cam = core.make_camera(p, case["width"], case["height"])
            ours = {"bottle": core.build_bottle(p, cam), "grid": core.build_grid(p, cam)}
            for layer in ("bottle", "grid"):
                self.assertEqual(len(ours[layer]), len(theirs[layer]), case)
                mine, other = list(flatten(ours[layer])), list(flatten(theirs[layer]))
                self.assertEqual(len(mine), len(other), case)
                for a, b in zip(mine, other):
                    if a is None or b is None:
                        self.assertEqual(a, b, case)
                    else:
                        self.assertAlmostEqual(a, b, delta=1e-6, msg=str(case))


# Opaque, strongly coloured faces, for tests that look at what is covered.
SOLID = {"fill_opacity": 1, "body_color": "#FF00FF", "neck_color": "#00E5FF"}


class RenderTests(unittest.TestCase):
    def test_placeholder_is_drawn_where_asked_and_nowhere_else(self):
        image = gradient()
        out, mask = node_module.render_placeholder(image, {"pos_x": 0.25, "pos_y": 0.9, "scale": 2, **SOLID})
        self.assertEqual(out.shape, (1, 240, 320, 3))
        self.assertEqual(mask.shape, (240, 320))
        self.assertEqual(mask.max(), 1.0)
        rows, columns = np.nonzero(mask > 0.5)
        # Base centre at x = 80, y = 216; a 29-unit bottle at 2% of 240 px is about 139 px tall.
        # Perspective pushes the near faces a few pixels outwards from there.
        self.assertLess(abs(columns.mean() - 80), 8)
        self.assertLess(abs(rows.max() - 216), 14)
        self.assertLess(abs((rows.max() - rows.min()) - 139), 20)
        np.testing.assert_array_equal(out[0][mask == 0], image[0][mask == 0])
        self.assertGreater(np.abs(out[0][mask == 1] - image[0][mask == 1]).mean(), 0.1)

    def test_colours_reach_the_output(self):
        params = {"body_color": "#00FF00", "neck_color": "#0000FF", "front_label": False,
                  "outline_width": 0, "camera_pitch": 0, "fill_opacity": 1}
        out, _mask = node_module.render_placeholder(np.zeros((1, 240, 320, 3), np.float32), params)
        body, neck = out[0, 170, 160], out[0, 65, 160]
        self.assertTrue(body[1] > 0.5 and body[0] == 0 and body[2] == 0, body)
        self.assertTrue(neck[2] > 0.5 and neck[0] == 0 and neck[1] == 0, neck)

    def test_outline_style_draws_edges_only_in_the_chosen_colour(self):
        image = np.zeros((1, 480, 640, 3), np.float32)
        params = {"style": "outline", "outline_color": "#FFE000", "outline_width": 4, "camera_pitch": 20, "rotate_y": 30}
        out, mask = node_module.render_placeholder(image, params)
        shaded, shaded_mask = node_module.render_placeholder(image, {**params, "style": "shaded", "fill_opacity": 1})
        # The mask stays the full silhouette even though only lines are drawn.
        np.testing.assert_array_equal(mask, shaded_mask)
        drawn = out[0].max(axis=-1) > 0.5
        self.assertTrue(0.02 < drawn[mask == 1].mean() < 0.5, drawn[mask == 1].mean())
        self.assertFalse(drawn[mask == 0].any())
        np.testing.assert_allclose(out[0][drawn].mean(axis=0), [1.0, 0.88, 0.0], atol=0.06)
        # Lines are in the same place as the shaded style's edges, and faces in
        # front hide the lines behind them: nothing is drawn that shaded lacks.
        edges = np.abs(shaded[0] - np.array([1.0, 0.88, 0.0], np.float32)).max(axis=-1) < 0.3
        near_edge = edges.copy()
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            near_edge |= np.roll(edges, (dy, dx), axis=(0, 1))
        self.assertLess((drawn & ~near_edge).sum(), 0.01 * drawn.sum())

    def test_fill_opacity_blends_faces_and_leaves_edges_and_mask_solid(self):
        image = gradient()
        bare = {"outline_width": 0}
        solid, solid_mask = node_module.render_placeholder(image, {**bare, "fill_opacity": 1})
        half, half_mask = node_module.render_placeholder(image, {**bare, "fill_opacity": 0.5})
        hidden, hidden_mask = node_module.render_placeholder(image, {**bare, "fill_opacity": 0})
        np.testing.assert_allclose(half, (solid + image) / 2, atol=1e-5)
        np.testing.assert_allclose(hidden, image, atol=1e-6)
        np.testing.assert_array_equal(half_mask, solid_mask)
        np.testing.assert_array_equal(hidden_mask, solid_mask)
        # With edges on, they stay at full strength: faces at zero opacity leave
        # exactly the outline style.
        edged, _mask = node_module.render_placeholder(image, {"fill_opacity": 0, "outline_width": 12})
        outline, _mask = node_module.render_placeholder(image, {"style": "outline", "outline_width": 12})
        np.testing.assert_array_equal(edged, outline)
        self.assertEqual(edged.min(), 0.0)

    def test_defaults_are_translucent_grey_with_black_edges(self):
        white = np.ones((1, 240, 320, 3), np.float32)
        # A wide outline, so that some pixels are fully covered at this small size.
        out, mask = node_module.render_placeholder(white, {"front_label": False, "camera_pitch": 0, "outline_width": 12})
        face = out[0, 170, 160]
        # Light grey at half strength over white: between the grey and white, and neutral.
        self.assertTrue(0.75 < face[0] < 0.97 and np.ptp(face) < 1e-6, face)
        self.assertEqual(out[0][mask == 1].min(), 0.0)

    def test_grid_is_optional_and_never_in_the_mask(self):
        image = gradient()
        plain, plain_mask = node_module.render_placeholder(image, SOLID)
        gridded, grid_mask = node_module.render_placeholder(image, SOLID, grid_in_output=True)
        np.testing.assert_array_equal(plain_mask, grid_mask)
        outside = plain_mask == 0
        self.assertGreater(np.abs(gridded[0][outside] - plain[0][outside]).max(), 0.2)
        np.testing.assert_allclose(gridded[0][plain_mask == 1], plain[0][plain_mask == 1], atol=1e-6)

    def test_every_image_in_a_batch_gets_the_same_placeholder(self):
        image = gradient(batch=3)
        out, mask = node_module.render_placeholder(image, {"shape": "cylinder", **SOLID})
        self.assertEqual(out.shape, (3, 240, 320, 3))
        inside = mask == 1
        np.testing.assert_array_equal(out[0][inside], out[2][inside])
        np.testing.assert_array_equal(out[2][mask == 0], image[2][mask == 0])

    def test_a_placeholder_off_the_image_changes_nothing(self):
        image = gradient()
        out, mask = node_module.render_placeholder(image, {"pos_x": -1, "pos_y": -1, "scale": 0.5})
        np.testing.assert_array_equal(out, image)
        self.assertEqual(mask.max(), 0.0)

    def test_extreme_settings_still_render(self):
        image = gradient(64, 48)
        for params in ({"fov": 120, "scale": 20, "rotate_x": 80}, {"fov": 5, "scale": 0.05},
                       {"camera_pitch": 89, "shape": "cylinder"}, {"camera_pitch": -89, "camera_roll": 45}):
            out, mask = node_module.render_placeholder(image, params, grid_in_output=True)
            self.assertTrue(np.isfinite(out).all(), params)
            self.assertTrue(0.0 <= out.min() and out.max() <= 1.0, params)

    def test_bad_input_is_reported(self):
        with self.assertRaisesRegex(ValueError, "body_color must be a hex colour"):
            node_module.render_placeholder(gradient(), {"body_color": "magenta"})
        with self.assertRaisesRegex(ValueError, r"\[B, H, W, 3\]"):
            node_module.render_placeholder(np.zeros((240, 320, 3), np.float32), {})


class NodeTests(unittest.TestCase):
    def inputs(self):
        spec = node_module.WN_BottlePlaceholder.INPUT_TYPES()["required"]
        return {name: options[1]["default"] for name, options in spec.items() if name != "image"}

    def test_widget_defaults_match_the_shared_defaults(self):
        values = self.inputs()
        for name, default in core.DEFAULTS.items():
            self.assertEqual(values[name], default, name)

    def test_render_returns_comfy_tensors(self):
        image = torch.from_numpy(gradient(batch=2))
        composite, mask = node_module.WN_BottlePlaceholder().render(image=image, **self.inputs())
        self.assertEqual(tuple(composite.shape), (2, 240, 320, 3))
        self.assertEqual(composite.dtype, torch.float32)
        self.assertEqual(tuple(mask.shape), (1, 240, 320))
        self.assertGreater(float(mask.sum()), 100)

    def test_render_publishes_the_incoming_image_for_the_viewport(self):
        image = torch.from_numpy(gradient(2000, 1000))
        with tempfile.TemporaryDirectory() as temp:
            import folder_paths
            with patch.object(folder_paths, "get_temp_directory", return_value=temp):
                output = node_module.WN_BottlePlaceholder().render(image=image, unique_id="7:3", **self.inputs())
            entry = output["ui"]["wn_bottle_background"][0]
            self.assertEqual((entry["type"], entry["width"], entry["height"]), ("temp", 2000, 1000))
            from PIL import Image
            with Image.open(Path(temp) / entry["filename"]) as preview:
                self.assertEqual(preview.size, (1536, 768))
        self.assertEqual(len(output["result"]), 2)


if __name__ == "__main__":
    unittest.main()
