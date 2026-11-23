"""Bottle Placeholder: overlay a body + neck placement guide on an image.

The guide is drawn here from the widget values with Pillow, so a queued render
needs no OpenGL, no browser capture and gives the same result through the API.
The viewport in js/wn_bottle_placeholder.js draws the same geometry.
"""

import os
import re

import numpy as np
from PIL import Image, ImageDraw

from . import bottle_placeholder_core as core

# Largest supersampled canvas, in pixels, before the sampling factor drops.
_SUPERSAMPLE_BUDGET = 36_000_000
_PREVIEW_MAX_SIDE = 1536


def _rasterize(polygons, lines, width, height, edges=False):
    """Draw ops into a premultiplied RGBA crop.

    Returns (float32 array [h, w, 4] in 0..1, x0, y0), or None when nothing
    lands inside the image. By default polygon fills are painted and strokes
    skipped (the faces layer); with `edges` the strokes are drawn and each fill
    erases what is under it (the edges layer).
    """
    xs, ys, pad = [], [], 2.0
    for op in polygons:
        xs.extend(point[0] for point in op["points"])
        ys.extend(point[1] for point in op["points"])
        pad = max(pad, op["width"] + 2)
    for op in lines:
        xs.extend((op["a"][0], op["b"][0]))
        ys.extend((op["a"][1], op["b"][1]))
        pad = max(pad, op["width"] + 2)
    if not xs:
        return None
    x0 = max(0, int(np.floor(min(xs) - pad)))
    y0 = max(0, int(np.floor(min(ys) - pad)))
    x1 = min(width, int(np.ceil(max(xs) + pad)))
    y1 = min(height, int(np.ceil(max(ys) + pad)))
    if x1 <= x0 or y1 <= y0:
        return None

    crop_w, crop_h = x1 - x0, y1 - y0
    ss = next((factor for factor in (4, 3, 2) if crop_w * crop_h * factor * factor <= _SUPERSAMPLE_BUDGET), 1)
    canvas = Image.new("RGBA", (crop_w * ss, crop_h * ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    def place(point):
        return ((point[0] - x0) * ss, (point[1] - y0) * ss)

    for op in polygons:
        points = [place(point) for point in op["points"]]
        if op["fill"] is not None:
            # ImageDraw replaces pixels, so a transparent fill erases what is under it.
            colour = (0, 0, 0, 0) if edges else (*op["fill"], 255)
            draw.polygon(points, fill=colour, outline=colour)
        if edges and op["stroke"] is not None:
            # Repeating the first two points closes the loop with a rounded join.
            draw.line(points + points[:2], fill=(*op["stroke"], 255),
                      width=max(1, round(op["width"] * ss)), joint="curve")
    for op in lines:
        r, g, b, alpha = op["rgba"]
        # ImageDraw replaces pixels instead of blending, so store premultiplied colour.
        colour = (round(r * alpha), round(g * alpha), round(b * alpha), round(255 * alpha))
        draw.line([place(op["a"]), place(op["b"])], fill=colour, width=max(1, round(op["width"] * ss)))

    if ss > 1:
        canvas = canvas.reduce(ss)
    return np.asarray(canvas, dtype=np.float32) / 255.0, x0, y0


def render_placeholder(images, params, grid_in_output=False):
    """Composite the guide over a batch of images.

    images: float array [B, H, W, C>=3] in 0..1. Returns (composite [B, H, W, 3],
    mask [H, W]); the mask covers the bottle only, never the grid.
    """
    images = np.asarray(images, dtype=np.float32)
    if images.ndim != 4 or images.shape[-1] < 3:
        raise ValueError("Expected an IMAGE shaped [B, H, W, 3].")
    height, width = images.shape[1], images.shape[2]
    p = core.normalize_params(params)
    cam = core.make_camera(p, width, height)

    out = np.clip(images[..., :3], 0.0, 1.0).copy()
    mask = np.zeros((height, width), dtype=np.float32)

    def blend(layer, strength):
        if layer is None:
            return
        rgba, x0, y0 = layer
        region = out[:, y0:y0 + rgba.shape[0], x0:x0 + rgba.shape[1]]
        region *= 1.0 - rgba[..., 3:4] * strength
        region += rgba[..., :3] * strength

    if grid_in_output:
        blend(_rasterize([], core.build_grid(p, cam), width, height), 1.0)
    bottle = core.build_bottle(p, cam)
    faces = _rasterize(bottle, [], width, height)
    edges = _rasterize(bottle, [], width, height, edges=True)
    if p["style"] == "shaded":
        blend(faces, p["fill_opacity"])
    blend(edges, 1.0)
    # The mask is the whole silhouette, whatever the style or opacity.
    for layer in (faces, edges):
        if layer is not None:
            rgba, x0, y0 = layer
            region = mask[y0:y0 + rgba.shape[0], x0:x0 + rgba.shape[1]]
            region[...] = 1.0 - (1.0 - region) * (1.0 - rgba[..., 3])
    return np.clip(out, 0.0, 1.0), mask


def _save_viewport_background(image, unique_id):
    """Write the incoming image to ComfyUI's temp folder for the editor viewport.

    The viewport can read a directly connected Load Image on its own; this covers
    every other upstream node once the workflow has run.
    """
    import folder_paths

    height, width = image.shape[0], image.shape[1]
    preview = Image.fromarray((np.clip(image[..., :3], 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8), mode="RGB")
    longest = max(width, height)
    if longest > _PREVIEW_MAX_SIDE:
        ratio = _PREVIEW_MAX_SIDE / longest
        preview = preview.resize((max(1, round(width * ratio)), max(1, round(height * ratio))), Image.Resampling.BILINEAR)
    safe_id = re.sub(r"[^0-9A-Za-z_-]", "_", str(unique_id))
    filename = f"wn_bottle_placeholder_{safe_id}.jpg"
    directory = folder_paths.get_temp_directory()
    os.makedirs(directory, exist_ok=True)
    preview.save(os.path.join(directory, filename), quality=90)
    return {"filename": filename, "subfolder": "", "type": "temp", "width": width, "height": height}


def _slider(default, low, high, step, advanced=False):
    # No tooltip on sliders: it would cover the value while it is being dragged.
    # docs/nodes.md describes each one.
    return ("FLOAT", {"default": default, "min": low, "max": high, "step": step, "round": step,
                      "display": "slider", "advanced": advanced})


class WN_BottlePlaceholder:
    """Overlay a two-part bottle placeholder on an image as a placement guide."""

    DESCRIPTION = (
        "Draws a simple bottle placeholder (a body and a neck, as boxes or cylinders) in perspective over an "
        "image. Use it to show an image-edit model where a product should sit and at what proportions. "
        "Position it in the viewport: drag the bottle to move it, drag the background to orbit the camera, "
        "click the viewport and scroll to resize, and drag the dots to change dimensions. Trace sets the camera "
        "from lines you draw along the edges of the surface."
    )

    @classmethod
    def INPUT_TYPES(cls):
        d = core.DEFAULTS
        return {
            "required": {
                "image": ("IMAGE", {"tooltip": "Image to draw the placeholder on. Sets the output size."}),
                "shape": (["box", "cylinder"], {"default": d["shape"], "tooltip": "Draw both parts as boxes or as cylinders."}),
                "style": (["shaded", "outline"], {"default": d["style"], "tooltip": "Shaded faces with edges, or edges only."}),

                "body_width": _slider(d["body_width"], 0.5, 60.0, 0.1),
                "body_height": _slider(d["body_height"], 0.5, 60.0, 0.1),
                "body_depth": _slider(d["body_depth"], 0.5, 60.0, 0.1),
                "neck_width": _slider(d["neck_width"], 0.5, 60.0, 0.1),
                "neck_height": _slider(d["neck_height"], 0.5, 60.0, 0.1),
                "neck_depth": _slider(d["neck_depth"], 0.5, 60.0, 0.1),

                "pos_x": _slider(d["pos_x"], -0.5, 1.5, 0.001),
                "pos_y": _slider(d["pos_y"], -0.5, 1.5, 0.001),
                "scale": _slider(d["scale"], 0.05, 20.0, 0.01),

                "rotate_y": _slider(d["rotate_y"], -180.0, 180.0, 0.1),
                "rotate_x": _slider(d["rotate_x"], -180.0, 180.0, 0.1, advanced=True),
                "rotate_z": _slider(d["rotate_z"], -180.0, 180.0, 0.1, advanced=True),

                "fov": _slider(d["fov"], 5.0, 120.0, 0.1),
                "camera_pitch": _slider(d["camera_pitch"], -89.0, 89.0, 0.1),
                "camera_orbit": _slider(d["camera_orbit"], -180.0, 180.0, 0.1),
                "camera_roll": _slider(d["camera_roll"], -45.0, 45.0, 0.1, advanced=True),

                "body_color": ("STRING", {"default": d["body_color"], "tooltip": "Body colour as hex, e.g. #D0D0D0.", "advanced": True}),
                "neck_color": ("STRING", {"default": d["neck_color"], "tooltip": "Neck colour as hex.", "advanced": True}),
                "front_label": ("BOOLEAN", {"default": d["front_label"], "tooltip": "Draw a patch on the front of the body so the label side is unambiguous.", "advanced": True}),
                "label_color": ("STRING", {"default": d["label_color"], "tooltip": "Label patch colour as hex.", "advanced": True}),
                "outline_color": ("STRING", {"default": d["outline_color"], "tooltip": "Edge line colour as hex."}),
                "outline_width": _slider(d["outline_width"], 0.0, 12.0, 0.1),
                "fill_opacity": _slider(d["fill_opacity"], 0.0, 1.0, 0.01),

                "show_grid": ("BOOLEAN", {"default": True, "tooltip": "Show the perspective grid in the viewport. Line it up with the surface in the photo."}),
                "grid_spacing": _slider(d["grid_spacing"], 0.5, 60.0, 0.1, advanced=True),
                "grid_in_output": ("BOOLEAN", {"default": False, "tooltip": "Also draw the grid into the output image.", "advanced": True}),
            },
            "hidden": {"unique_id": "UNIQUE_ID"},
        }

    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("composite", "placeholder_mask")
    OUTPUT_TOOLTIPS = (
        "The image with the placeholder drawn over it.",
        "Mask of the placeholder. The grid is never included.",
    )
    FUNCTION = "render"
    CATEGORY = "WepeNerd/3D"

    def render(self, image, grid_in_output, show_grid=True, unique_id=None, **params):
        import torch

        source = image.detach().cpu().numpy()
        composite, mask = render_placeholder(source, params, grid_in_output=grid_in_output)
        result = (torch.from_numpy(composite), torch.from_numpy(mask).unsqueeze(0))
        if unique_id is None:
            return result
        return {"ui": {"wn_bottle_background": [_save_viewport_background(source[0], unique_id)]}, "result": result}
