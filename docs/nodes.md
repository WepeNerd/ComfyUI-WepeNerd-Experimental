# Experimental node guide

## Load OBJ


**Category:** `WepeNerd/3D`

Loads a Wavefront `.obj` model from a local path and produces a reusable `obj_model` connection for 3D placement. It also outputs a clay preview image and preview mask, so you can inspect the model before placing it over a background.

Use the `choose .obj to upload` button or drag and drop an `.obj` file onto the node. Uploaded OBJ files are saved under `ComfyUI/input/3d/`, and the node fills the path widget automatically.

| Output | Type | Description |
|---|---|---|
| `obj_model` | WN_OBJ3D | Connect this to `3D Product Placement` |
| `preview` | IMAGE | Clay preview render on a neutral background |
| `preview_mask` | MASK | Alpha mask for the preview render |

---


## 3D Product Placement


**Category:** `WepeNerd/3D`

An interactive 3D object placement node for creating guide composites. Connect a normal ComfyUI `Load Image` node as the background, connect `Load OBJ (WepeNerd)` as the object, position the object visually in a JavaScript viewport, and output a composited image with the untextured clay model over the background.

The node is designed for product/object placement guides, not final photoreal rendering.

The queued composite uses a hidden browser viewport capture when available, so the output should match what you see in the node. If the capture is unavailable, the node falls back to the server-side renderer.

**Features:**
- Use a connected `IMAGE` input as the background and final output size
- Use a connected `Load OBJ (WepeNerd)` node as the 3D model
- Preview the model as a grey untextured clay object
- Rotate, move, and scale the object interactively
- Adjust basic directional lighting
- Toggle a wireframe overlay for placement guides
- Output a composited `IMAGE` and an object `MASK`
- Store placement values as normal widgets so workflows save and reload

| Action | Result |
|---|---|
| Left drag | Rotate object |
| Shift + drag | Move object X/Y |
| Mouse wheel | Scale object |
| Right drag / Alt + drag | Adjust light direction |
| Double-click | Reset placement |

| Input | Description |
|---|---|
| `background_image` | Connected `IMAGE` used as the scene/background. Determines final output dimensions |
| `obj_model` | Connected `WN_OBJ3D` from `Load OBJ (WepeNerd)` |
| `x_offset` / `y_offset` / `z_offset` | Object position controls |
| `scale` | Object scale |
| `rotate_x` / `rotate_y` / `rotate_z` | Object rotation in degrees |
| `camera_zoom` | Orthographic camera zoom |
| `light_yaw` / `light_pitch` | Directional light position |
| `light_intensity` | Light strength |
| `wireframe_overlay` | Draw a dark wireframe over the clay object in the viewport and composite |
| `opacity` | Opacity of the clay object in the final composite |

| Output | Type | Description |
|---|---|---|
| `composite` | IMAGE | Background image with clay object composited over it |
| `object_mask` | MASK | Alpha mask of the rendered 3D object |

OBJ loading and preview accept existing `.obj` files only inside `ComfyUI/input/3d/` or `ComfyUI/models/3d/`. Relative names resolve in those folders; absolute paths must remain within them after resolving symlinks. Uploads stay in `input/3d/`, are limited to 64 MiB, and use unique filenames.

---


## Load Long Video (WepeNerd)

**Category:** `WepeNerd/Video`
**Outputs:** `video`, `images`, `audio`, `frame_count`, `fps`.

Select a short segment from a long, indexed video, including sources over two
hours. Previewing reads metadata and seeks to individual frames; it does not
decode the whole video into memory or make a full-length proxy.

1. Drop a video onto the node (it outlines blue), or click **Upload video…**.
   Uploads stream straight to `ComfyUI/input` with a progress readout and no
   size limit (ComfyUI's own upload stops at `--max-upload-size`, 100 MB by
   default). A file already there with the same name and size is reused; a
   different file with the same name is saved as `name (1).mp4`. You can also
   copy videos into `ComfyUI/input` or a subfolder yourself, click
   **Refresh files** and choose `video_file`, which avoids a second copy.
2. Scrub with the thumbnail strip (the whole source) or the slider below it.
   Set **in** and **out** at the playhead, or type `start_seconds` and
   `end_seconds` in the node widgets. **Set in** makes the frame on screen the
   first frame; **Set out** keeps the frame on screen as the last one.
3. **Fit selection** zooms into the segment. Drag the green handles to trim,
   drag the green band to move the segment, or click above/below it to scrub.
   Mouse wheel and **+ / −** zoom around the playhead/pointer. Everything snaps
   to whole frames.
4. **Play selection** streams the selected portion from the original file using
   HTTP byte ranges. If the browser can't play the codec (HEVC, ProRes, MPEG-4
   and so on), it renders a small H.264 proxy of just the selection and plays
   that instead; the import always uses the original. Arrow keys step one
   frame (Shift: ten); **I / O** mark in/out; Space plays.
5. Queue the workflow. Only the selected range is extracted into a temporary
   lossless FFV1/MKV with PCM audio from the first audio track, if present.

| Input | What it does |
| --- | --- |
| `start_seconds` | In point. The frame shown at this time is the first frame imported. |
| `end_seconds` | Out point, exclusive, clamped to the end of the video. |
| `frame_rate` | `0` keeps the source frame rate. Otherwise the selection is resampled to this FPS, starting from the in frame. |
| `format` | A video model's frame-count rule, from the core pack's Duration Selector presets (built-in copies if the core pack isn't installed). The count is trimmed down to the largest legal count. Picking a preset also sets `frame_rate` to the model's FPS. |

The readout shows the selected frame numbers, the count that will be imported
(and what the format trims it to), the output FPS and length, and the RAM the
images need. With a format, **Out: fewer / Snap out to format / Out: more** move
the out point to legal counts, and a dashed line marks where a trimmed clip
ends. The readout turns amber outside the preset's frame range.

Connect `VIDEO` to native video nodes, or use `images`, `audio`, `frame_count`
and `fps` directly. `images` is decoded only when something is connected to it
(and `audio` likewise); unconnected, it is a 64×64 placeholder. Frame times use
the source's nominal frame rate; variable-frame-rate sources are stepped and
counted on that grid. Rotated phone videos import upright and report their
upright size.

Each run deletes the previous segment this node imported, so re-running with new
in/out points doesn't fill the temp folder (except with `--cache-lru` or
`--cache-ram`, where ComfyUI may still return an earlier result). Use Save Video
to keep a clip. Playback proxies are also kept in the temp folder, at most four.
Converting a clip to images still uses RAM proportional to its length and
resolution.

Requires FFmpeg and FFprobe on PATH; proxies need FFmpeg with libx264 or
libopenh264. Sources must resolve inside `ComfyUI/input`; absolute paths outside
it and escaping symlinks are rejected. Sources without a usable duration/index
should first be remuxed to MP4 or MKV.

---

## Bottle Placeholder


**Category:** `WepeNerd/3D`

Draws a simple bottle stand-in over an image: a body and a neck, as two boxes or two cylinders, in perspective. Send the composite to an image-edit workflow to show the model where the product goes and at what proportions. It is aimed at spirits bottles, but any body-plus-neck product fits.

The image is drawn in Python from the widget values, so the output matches the viewport, works through the API, and needs no OpenGL or 3D libraries.

**Viewport**

| Action | Result |
|---|---|
| Drag the bottle (or Shift + drag anywhere) | Move it |
| Drag the background | Orbit the camera: left/right turns the grid, up/down changes the pitch |
| Click the viewport, then mouse wheel | Resize the bottle |
| Drag a white dot | Change that dimension: body or neck width, depth or height |
| Drag the blue dot on the ring | Turn the bottle |
| Shift + wheel (viewport clicked) | Turn the bottle in 5° steps |
| Alt + drag | Tip and lean the bottle |

The dots and ring appear while the pointer is over the viewport. The wheel only acts on the bottle after you click the viewport, which outlines it; until then, scrolling over the node zooms the graph as usual. **Bottle preset** sets the shape and dimensions to a common proportion; the values are approximate starting points. **Reset bottle** and **Reset camera** restore placement or camera defaults without touching dimensions.

**Tracing the photo's perspective**

**Trace** works the camera out from the photo. Draw two or more lines along one edge direction of the surface the bottle stands on (red), then two or more along the direction at right angles to it (blue): the two long edges and the two short edges of a table top, for example. Draw a line by dragging, or by clicking its start and then its end; drag an end to adjust it, and press Esc to drop a line you have started. The grid follows as you draw.

| Lines drawn | What is set |
|---|---|
| Two directions that both converge on screen | `fov`, `camera_pitch`, `camera_roll` and `camera_orbit` |
| Two directions, one of them parallel on screen (a square-on view) | Pitch, roll and orbit; `fov` keeps its current value, because it cannot be measured from such lines |
| One direction only | Pitch and orbit, assuming a level horizon and the current `fov` |

More than two lines per direction are averaged, which helps when edges are soft or short. Longer lines and lines far apart give steadier results. **Undo** removes the last line, **Clear** removes all, and **Done** returns to placing the bottle; the camera settings stay, and the lines are saved with the workflow.

Tracing assumes the photo is not cropped off-centre and has no strong lens distortion. It cannot tell real-world size, so `scale` and `grid_spacing` stay yours to set. The red lines become the grid's red axis; the bottle's `rotate_y` is measured from the grid, so it turns with it.

**Matching the photo's perspective by hand**

The grid lies on the surface the bottle stands on and always passes through its base. Drag the background until the grid lines run along the table edges or floor lines in the photo, then set `fov`: lower values flatten the perspective, higher values exaggerate it. The amber line is the horizon; the red, blue and green lines are the surface's two axes and the vertical through the bottle. `camera_roll` levels a tilted photo.

A directly connected `Load Image` appears in the viewport immediately. For any other source, run the workflow once and the viewport shows the image the node received.

| Input | Description |
|---|---|
| `image` | Image to draw on. Sets the output size; every image in a batch gets the same placeholder |
| `shape` | `box` or `cylinder`, applied to both parts |
| `style` | `shaded` draws coloured faces with edges; `outline` draws the edges only, with lines behind a nearer face hidden |
| `body_width` / `body_height` / `body_depth` | Body dimensions. Units are arbitrary; centimetres work well |
| `neck_width` / `neck_height` / `neck_depth` | Neck dimensions, in the same units |
| `pos_x` / `pos_y` | Position of the base centre, 0 to 1 across and down the image |
| `scale` | Size of one unit as a percentage of the image height, so placement carries over between resolutions |
| `rotate_y` | Turn around the vertical axis, relative to the grid |
| `rotate_x` / `rotate_z` | Tip forwards/backwards and lean left/right about the base |
| `fov` | Vertical field of view of the camera |
| `camera_pitch` | How far the camera looks down at the surface |
| `camera_orbit` | Rotates the surface grid, and the bottle with it |
| `camera_roll` | Tilts the horizon |
| `body_color` / `neck_color` / `label_color` | Hex colours, e.g. `#D0D0D0` |
| `front_label` | Draw a patch on the front of the body, so the label side is clear even on a cylinder |
| `fill_opacity` | Opacity of the shaded faces. Edges are always drawn solid |
| `outline_color` | Edge line colour as hex. In the `outline` style this is the only colour drawn |
| `outline_width` | Edge line width in pixels at 1024 px image height; 0 turns edges off |
| `show_grid` | Show the grid in the viewport |
| `grid_spacing` | Grid cell size, in bottle units |
| `grid_in_output` | Also draw the grid into the output image |

| Output | Type | Description |
|---|---|---|
| `composite` | IMAGE | The image with the placeholder drawn over it |
| `placeholder_mask` | MASK | Mask of the placeholder's whole silhouette, in either style; never includes the grid |

Sliders have no hover text, so nothing covers the value while you drag; the table above describes each one.

The defaults are light grey faces at 50% opacity with black edges.

If you train a LoRA on these placeholders, keep the style, colours, opacity, outline and label settings the same between training data and use.

---


## Exact Video Frames/FPS (WepeNerd)


**Category:** `WepeNerd/Video`

Loads a video from ComfyUI's input folder, or accepts a file-backed `VIDEO` input, and writes a new video with an exact target frame count and FPS.

Because exact frame count/FPS changes require frame timing work, the node has two quality paths:

- `lossless exact (FFV1/MKV)` decodes and writes a lossless MKV. This is the default because it verifies exact frame count and FPS without adding lossy generation loss.
- `lossless exact (H.264 RGB/MP4)` writes a lossless H.264 MP4 for workflows that need MP4 output.
- `stream copy best effort (no re-encode)` copies compressed video packets without re-encoding. It verifies the requested frame count, but FPS metadata is best effort because many containers preserve source packet timing during stream copy.

Use `extension_mode` to choose what happens when the requested output is longer than the source at the requested FPS:

- `hold_last_frame` extends with the final frame.
- `loop_source` repeats the source video.

Audio is dropped by default. `copy_trim_audio` copies the source audio packets and trims them to the new video duration without re-encoding, when the source/container supports it.

| Input | Description |
|---|---|
| `video_file` | Video file from the ComfyUI input directory |
| `video` | Optional connected file-backed ComfyUI `VIDEO` input |
| `target_frame_count` | Exact number of output video frames |
| `target_fps` | Exact output frame rate for lossless exact modes |
| `quality_mode` | Lossless exact output or best-effort packet stream copy |
| `extension_mode` | Hold the final frame or loop the source when more frames are needed |
| `audio_mode` | Drop audio or copy/trim source audio packets |
| `filename_prefix` | Output path prefix under the ComfyUI output directory |

| Output | Type | Description |
|---|---|---|
| `video` | VIDEO | Generated video, ready for ComfyUI video nodes |
| `output_path` | STRING | Absolute path to the generated file |
| `info` | STRING | Source/output probe details and mode notes |

Requires FFmpeg and FFprobe on PATH.

---
