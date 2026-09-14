# Third-party attribution

The RefMod single-latent container, native reference block layout, photo-stack
encoding, and chunked audio approach in `refmod_core.py` and `refmod_node.py` are
adapted from Luisa (luisacaotica), copyright 2026, MIT License.
Source: https://github.com/Luisacaotica/ComfyUI-MiniMaxH3Mod at
`22ca77e247375dd47fc7340286b5f391887d3410`, supplied in
`H3_RefMod_Studio_v1_2.zip`. The full license is in `licenses/refmod-LICENSE`.
This integration replaces the standalone launcher/worker with ComfyUI nodes,
uses the host VAE encode API, adds pre-encode selection and validation, and omits
latent optimization and strength curves. It does not import an external node pack.

`js/vendor/three.module.mjs`, `OBJLoader.mjs`, and `OrbitControls.mjs` were carried
from the combined WepeNerd source. Three.js revision 160, copyright 2010–2023
Three.js Authors, MIT License. Original copyright/license headers remain.
The full upstream license is in `js/vendor/LICENSE`. Only module extensions and
relative import suffixes were changed to avoid ComfyUI auto-loading vendor JS.
