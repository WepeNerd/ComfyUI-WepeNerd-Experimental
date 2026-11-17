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

1. Copy the source into `ComfyUI/input` or a subfolder. Click **Refresh files**
   and choose `video_file`. Large files do not need a browser upload.
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
