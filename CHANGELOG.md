# Changelog

## Unreleased

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
