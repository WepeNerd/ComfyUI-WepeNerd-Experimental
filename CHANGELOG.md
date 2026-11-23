# Changelog

## Unreleased

- Add Load Long Video: scrub long sources with a thumbnail strip and import only the selected range, frame-accurately, as VIDEO, images, audio, frame count and FPS.
- Load Long Video: optional `frame_rate` resampling and `format` presets (shared with the core Duration Selector) that trim the count to a model's rule.
- Load Long Video: drag-and-drop and an Upload button, streaming any size of video into ComfyUI/input with progress.
- Load Long Video: proxy playback for codecs the browser can't play, multi-threaded FFV1 import with a progress bar, and cleanup of the node's previous segment.
- Add Bottle Placeholder: a body-and-neck placement guide (boxes or cylinders) drawn in perspective over an image, with an interactive viewport, perspective grid, dimension handles and bottle presets, in a shaded or outline-only style. A trace tool sets the camera from lines drawn along the surface's edges. Rendering needs no 3D libraries.

- Add an Apply `photo_layout` option that sends multi-photo RefMods as separate native H3 image references.
- Resample audio with ComfyUI's resampler, so audio RefMods no longer need `torchaudio`.
- Save RefMods on drives without hard links, accept looser `selected_indices`, and clarify empty-folder and single-image budget errors.

- Add multiple image uploads, growing IMAGE sockets, local file lists, and recursive folder inputs to H3 visual RefMod creation.
- Add VAE-free previews, shared crop/fit preparation, EXIF correction, configurable transparency backgrounds, and folder cache invalidation.

- Add H3 visual/audio RefMod creation, Studio 1.2 save/load, and positive-conditioning Apply nodes.
- Select and exactly deduplicate photos before encoding, with explicit overflow policies and retained-view reports.
- Respect registered RefMod model paths and publish complete files atomically.

## 0.2.0

- Liquify now belongs to ComfyUI-WepeNerd core 0.2.0 or newer.
- Remove the previous Liquify registration and editor from this package.
- Update both packages together; existing workflows keep their Liquify node IDs.

## 0.1.0

- Standalone package for Load OBJ, 3D Product Placement, Liquify, and Exact Video Frames/FPS.
- Restrict OBJ loading to approved folders and bound uploads without overwriting files.
- Load browser 3D assets on demand and release editor resources on removal.
- Keep Python 3D dependencies optional for image and video utilities.
