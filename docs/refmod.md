# H3 RefMod

Find the six nodes under **WepeNerd / H3 RefMod**. They encode reference content
with the native MiniMax H3 VAE and reuse it as H3 conditioning. They do not train
weights or isolate identity, style, clothing, or voice. Concept labels and
descriptions are metadata only.

## Create and save

1. Add photographs to **Create H3 Visual RefMod** using any combination of:
   - **Add images** to select/upload multiple files in the browser.
   - **Upload folder** to upload a browser-selected folder; enable `recursive` first for subfolders.
   - `folder` for a directory already on the ComfyUI machine, e.g. `C:\photos\alex`.
     Relative paths such as `refmod_photos/alex` start inside `ComfyUI/input`.
   - `image_paths`, one local filename per line, in your chosen order.
   - The existing `images` socket or the growing `additional_images` sockets.
     Separate sockets can have different sizes; a socket may also carry an IMAGE batch.
   Connect the H3 visual VAE. No IMAGE socket is required for a file/folder job.
2. Leave `selected_indices` empty for all photos, or enter zero-based indices
   such as `0,2,1`; spaces, line breaks and a trailing comma are accepted.
   Order establishes priority for `first selected` overflow.
   Exact RGB duplicates are removed before encoding unless `deduplicate` is off.
3. Set the short edge and token budget. The default overflow policy stops before
   VAE encoding if the selected set exceeds the budget. `first selected` keeps
   the first distinct selected photos that fit. A budget of zero is unlimited.
4. **Preview H3 RefMod Images** accepts the same sources and image settings,
   requires no VAE, and displays thumbnails of the actual crops/padding. Queue it
   with the settings you intend to use in Create. Its thumbnail output is reduced
   to at most 512 pixels on the long edge; do not use thumbnails as full-size sources.
   Connect `report` to a text display node to inspect the retained indices,
   omitted indices and reasons, RGB pixel hashes, canvas, shape, and token count.
5. Connect the result to **Save H3 RefMod** and Queue. Give it a relative name
   such as `characters/alex.safetensors`. Existing files require `overwrite`.
   Drives without hard-link support, such as exFAT, still refuse to overwrite.

Source order is `images`, additional sockets in numeric order, explicit file
paths in line order, then naturally sorted folder files (`view2` before `view10`).
Repeated file paths are listed once. Edit the path list or `selected_indices` to
remove/reorder views; the first selected view is the visible canvas anchor in the
report. Folder additions, removals and file timestamp/size changes invalidate the
node cache. Local files are read one at a time, rather than loading a whole folder
of full-resolution images into a tensor batch. Files changed during preparation
fail with an instruction to Queue again.

The anchor determines the canvas, downscaled to the short-edge setting and
rounded to 32 pixels. **center crop** matches Studio's mixed-aspect behavior;
**fit with padding** preserves the whole image; **stretch** fills the canvas by
resizing both axes. Matching source sizes retain the previous IMAGE-batch resize
behavior. Followers may upscale to fill the anchor canvas. EXIF orientation is
corrected and transparency is composited onto the chosen `background` hex colour,
also used for padding. PNG, JPEG, WebP, BMP and TIFF still images are supported;
animated files fail with a clear error. No subject detection is performed.
The report includes source dimensions and approximate center-crop loss. Source
IDs contain filenames and socket indices, without absolute source paths.
No photos are averaged or latent-pooled.
Multiple photos become one `video`-kind temporal stack for Studio compatibility.
Apply's `photo_layout` can instead send them as separate native image references
(see below).

**Create H3 Audio RefMod** accepts an AUDIO input and the H3 audio VAE; it needs
no images or visual VAE. Select start and duration in seconds. Mono is duplicated
to stereo and other sample rates are resampled to 32 kHz with ComfyUI's own
resampler; `torchaudio` is used only on older ComfyUI builds without `comfy.audio`.
`truncate` shortens an over-budget excerpt; `error` stops. Encoding uses chunks of up to ten seconds.
Chunk joins have not been validated for audible continuity. Use standard ComfyUI
audio playback/trim nodes to inspect the source excerpt.

## Load and apply

Copy Studio 1.2 `.safetensors` files into `ComfyUI/models/refmods`, then refresh
the node's file list. `refmods` entries in `extra_model_paths.yaml` are respected.
Saves go to the first registered RefMod directory; loading searches all of them.
Subfolders work; paths outside the registered directories are rejected.

Connect **Load H3 RefMod** (or either creator) to **Apply H3 RefMod**. Feed positive
H3 conditioning through Apply and onward to the sampler. Keep your usual H3 model,
negative conditioning, AV latent, sampling, and decoding setup. Chain Apply nodes
to add more visual or audio files. Disabled Apply has no effect. Do not also
attach the same content through a native reference node. A Studio visual/voice
pair is loaded and applied separately.

`photo_layout` only affects multi-photo files made by Create. `video stack` (the
default, matching Studio) sends one video reference, so H3 positions each photo
after the first as a four-frame step of continuous motion. `separate images`
sends one image reference per photo, as native Reference to Video does for
several reference images; each photo was encoded on its own, so this matches how
it was encoded. Which layout gives better likeness has not been tested in
generation. Loaded Studio files, single images and audio always send one reference.

Apply modifies `minimax_refs` on positive conditioning. It does **not** send
reference pixels to the multimodal text encoder or establish `<Picture i>`,
`<Video i>`, or `<Audio i>` prompt bindings. Names/descriptions are not trigger
words. Native numbered-reference conditioning requires the native H3 reference
workflow; packaging a face and voice does not guarantee speaker assignment.

The loader accepts version-4 single-latent files with embedded `refmod_meta`
or `audio_refmod_meta`. It validates tensor kind, dimensions and finite values.
Version-5 bundles and old JSON-sidecar-only files are not supported.
Studio training/pooled files can be loaded, but this creator only performs full
VAE encoding. Stored retention/curve recommendations are preserved as metadata;
Apply uses the saved latent unchanged.

## Validation

Run `python -m unittest discover -s tests -p "test_refmod.py"` with ComfyUI on
PYTHONPATH and its dependencies installed. Tests use real Torch/safetensors and
small deterministic VAE doubles to check selection, budgets, storage, path
containment, audio preparation, cancellation, and conditioning behavior.
They do not establish generation quality or real H3 VAE/CUDA performance.

Upload behavior tests run with `node --test tests/refmod_upload.test.mjs`.

Local smoke checks on 14 September 2026 also imported all nine package nodes,
roundtripped image/video/audio files through Studio 1.2's original serializer,
and constructed native H3 packed layouts. On an RTX 4060 Ti, the installed H3
FP32 audio VAE encoded a synthetic 0.1-second waveform and the INT8 ConvRot visual
VAE encoded a 64-square image. These checks validate the encode/API path; full
H3 generation, likeness, speaker binding, and audio join quality remain untested.

The attached research brief informed selection/reporting changes. Its proposed
crop editor, numbered text conditioning, perceptual selection, learned identity
research, and version-5 bundles are not part of this integration.

## Comparison with standalone Studio 1.2

Compared against the supplied `H3_RefMod_Studio_v1_2/H3_RefMod_Studio_v1_2`
launcher, core, worker and bundled encoder sources.

| Feature | Standalone Studio | Updated nodes |
| --- | --- | --- |
| Multiple files / folders | Desktop file and folder dialogs | Browser uploads, local paths, folders, growing IMAGE sockets |
| Include subfolders / natural order | Supported | Supported |
| Reorder / remove images | List buttons | Edit file list or ordered selection indices |
| Dataset inspection | Dimensions, crop warnings, token estimates in log | Same preparation as encode, visible thumbnails and JSON report |
| Different sizes / aspect ratios | First source canvas; center-crop followers | First selected canvas; crop, fit/pad, or stretch |
| EXIF / transparency | Rotation correction; white background | Rotation correction; configurable background |
| Visual encoding | Independent full encodes, temporal stack; metadata labels | Same representation and no training; Apply can send separate image references |
| Budget fitting | Encodes all, then latent deduplication and uniform selection | Exact pixel deduplication and explicit budget policy before encode |
| Defaults | Identity, 1024 short edge, 8192 tokens | Generic, 768 short edge, 8192 tokens; retained for existing node workflows |
| Voice | File reader, start/duration, separate VAE, up to 10-second chunks | Standard AUDIO input, start/duration, separate VAE, same chunk length; audio-only supported |
| Visual/voice publication | Pair naming, combined cost, group rollback, creation JSON | Separate Save nodes and per-file reports; no atomic pair publication or combined-cost report |
| Output / runtime selection | Arbitrary output folder and separate interpreter process | Registered `refmods` roots; native ComfyUI model management and Queue cancellation |
| Session logs / setup | Saved desktop settings and logs | Workflow persistence and ComfyUI execution history; no dedicated session-log export |
| Generation integration | External loader required | Native H3 Apply; no numbered multimodal text-reference binding |

The nodes do not import or execute the standalone application. Existing visual,
audio, Save/Load/Apply node IDs and connections remain valid. Visual Create uses
ComfyUI's V3 schema for growing sockets and requires a recent ComfyUI frontend.
