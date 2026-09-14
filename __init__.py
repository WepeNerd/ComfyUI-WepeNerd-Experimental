"""ComfyUI-WepeNerd-Experimental: independent WepeNerd node package."""

from .product_placement_node import WN_LoadOBJ
from .product_placement_node import WN_3DProductPlacement
from .video_frame_count_node import WN_VideoExactFramesFPS
from .product_placement_node import _register_3d_routes
from .refmod_node import WN_H3RefModCreate, WN_H3RefModPreview, WN_H3RefModAudioCreate, WN_H3RefModSave, WN_H3RefModLoad, WN_H3RefModApply

WEB_DIRECTORY = "./js"
NODE_CLASS_MAPPINGS = {
    'WN_LoadOBJ': WN_LoadOBJ,
    'WN_3DProductPlacement': WN_3DProductPlacement,
    'WN_VideoExactFramesFPS': WN_VideoExactFramesFPS,
    'WN_H3RefModCreate': WN_H3RefModCreate,
    'WN_H3RefModPreview': WN_H3RefModPreview,
    'WN_H3RefModAudioCreate': WN_H3RefModAudioCreate,
    'WN_H3RefModSave': WN_H3RefModSave,
    'WN_H3RefModLoad': WN_H3RefModLoad,
    'WN_H3RefModApply': WN_H3RefModApply,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "WN_LoadOBJ": "Load OBJ (WepeNerd)",
    "WN_3DProductPlacement": "3D Product Placement (WepeNerd)",
    "WN_VideoExactFramesFPS": "Exact Video Frames/FPS (WepeNerd)",
    "WN_H3RefModCreate": "Create H3 Visual RefMod (WepeNerd)",
    "WN_H3RefModPreview": "Preview H3 RefMod Images (WepeNerd)",
    "WN_H3RefModAudioCreate": "Create H3 Audio RefMod (WepeNerd)",
    "WN_H3RefModSave": "Save H3 RefMod (WepeNerd)",
    "WN_H3RefModLoad": "Load H3 RefMod (WepeNerd)",
    "WN_H3RefModApply": "Apply H3 RefMod (WepeNerd)"
}

_register_3d_routes()

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
