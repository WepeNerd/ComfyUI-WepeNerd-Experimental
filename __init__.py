"""ComfyUI-WepeNerd-Experimental: independent WepeNerd node package."""

from .product_placement_node import WN_LoadOBJ
from .product_placement_node import WN_3DProductPlacement
from .video_frame_count_node import WN_VideoExactFramesFPS
from .product_placement_node import _register_3d_routes

WEB_DIRECTORY = "./js"
NODE_CLASS_MAPPINGS = {
    'WN_LoadOBJ': WN_LoadOBJ,
    'WN_3DProductPlacement': WN_3DProductPlacement,
    'WN_VideoExactFramesFPS': WN_VideoExactFramesFPS,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "WN_LoadOBJ": "Load OBJ (WepeNerd)",
    "WN_3DProductPlacement": "3D Product Placement (WepeNerd)",
    "WN_VideoExactFramesFPS": "Exact Video Frames/FPS (WepeNerd)"
}

_register_3d_routes()

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
