# ComfyUI-WepeNerd-Experimental

Experimental creative tools for ComfyUI: 3D placement guides and
precise video frame controls. Useful features can move into the core toolkit as
they mature; this package carries a lower stability commitment.

| Tool | What it does | Extra requirements |
|---|---|---|
| **Load OBJ** | Load an OBJ model and render a clay preview | Python 3D libraries and working OpenGL |
| **3D Product Placement** | Position a clay object over an image and output a composite and mask | Python 3D libraries and working OpenGL |
| **Exact Video Frames/FPS** | Produce a chosen frame count and frame rate | FFmpeg and FFprobe |
| **H3 RefMod** | Create, save, load, and apply visual or voice references, compatible with Studio 1.2 | ComfyUI with native MiniMax H3 and the matching VAE |

## Installation

From your ComfyUI `custom_nodes` directory:

```sh
git clone https://github.com/WepeNerd/ComfyUI-WepeNerd-Experimental.git
```

Using the Python environment that runs ComfyUI:

```sh
python -m pip install -r ComfyUI-WepeNerd-Experimental/requirements.txt
```

Restart ComfyUI and refresh the browser. The base install is lightweight and
loads video nodes without the optional 3D renderer. It does not install
GPU wheels, models, or external programs.

For **3D rendering**, also run:

```sh
python -m pip install -r ComfyUI-WepeNerd-Experimental/requirements-3d.txt
```

For **video tools**, install FFmpeg separately and make both `ffmpeg` and `ffprobe`
available on PATH. See the [node guide](docs/nodes.md) for controls and formats.

## H3 RefMod

Use **Create H3 Visual RefMod** or **Create H3 Audio RefMod**, then **Save H3 RefMod**.
Visual Create accepts multiple uploads, individual IMAGE sockets, file lists, or
a local folder with optional subfolders. **Preview H3 RefMod Images** shows the
selected crops/padding and budget without a VAE.
Load Studio 1.2 exports from `models/refmods` with **Load H3 RefMod** and connect
**Apply H3 RefMod** between your H3 positive conditioning and sampler. Visual
selection and token budgets are resolved before encoding. See the
[RefMod guide](docs/refmod.md) for setup, reports, and conditioning limitations.

## Try 3D placement

Open the [example workflow](examples/obj-placement.json). Upload the included
[cube](examples/example-cube.obj) through Load OBJ, or copy it into
`ComfyUI/input/3d`. Connect Load OBJ to 3D Product Placement, supply a background
IMAGE, position the object, and click **Queue**.

OBJ files must be inside `ComfyUI/input/3d` or `ComfyUI/models/3d`. Relative paths
resolve in those folders. Uploads are limited to 64 MiB and choose unique names.
Absolute paths and symlinks that resolve outside those folders are rejected.

## Known limitations

- **3D:** these are untextured clay guides. Python preview/rendering requires a
  working OpenGL context. Browser WebGL alone is not sufficient. The tested
  Windows environment encountered `EGL_BAD_PARAMETER` in the Python renderer;
  verify your graphics stack before relying on this path.
- **Video:** exact modes re-encode. Stream-copy FPS is best effort, and compatible
  players/codecs are required for lossless output. Audio is dropped by default.

Package loading, file routes, and browser initialization were checked with
ComfyUI 0.34.0, frontend 1.51.10, Python 3.12, and Windows. Other platforms and
graphics stacks need validation.

This package works independently of [core](https://github.com/WepeNerd/ComfyUI-WepeNerd)
and [LocalAI](https://github.com/WepeNerd/ComfyUI-WepeNerd-LocalAI). If installing
core too, use its current version; the older all-in-one core includes duplicate
experimental nodes.

**Liquify has moved to [core](https://github.com/WepeNerd/ComfyUI-WepeNerd).**
For workflows containing Liquify, install core **0.2.0 or newer** and update
Experimental to **0.2.0 or newer** together. Restart ComfyUI and refresh the
browser. Existing node IDs, connections, and saved paintings are preserved.

[Report an issue](https://github.com/WepeNerd/ComfyUI-WepeNerd-Experimental/issues)
with a minimal workflow and console output. Originally part of ComfyUI-WepeNerd.
Licensed under [MIT](LICENSE); see [third-party attribution](THIRD_PARTY.md).
