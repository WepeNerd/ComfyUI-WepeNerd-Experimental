"""Geometry for the Bottle Placeholder node.

This module and js/wn_bottle_geometry.mjs implement the same maths line for
line: the browser preview and the queued render both come from the draw list
built here, so they must stay in step. tests/test_bottle_placeholder.py compares
the two outputs numerically.

Conventions
  object space : origin at the centre of the bottle base, +Y up, +Z front
  world space  : the ground plane is Y = 0 through the base centre
  camera space : camera at the origin looking down -Z, +Y up
  image space  : pixels, origin top left
Bottle dimensions are in arbitrary units (centimetres are convenient). `scale`
is the size of one unit as a percentage of the image height, measured at the
depth of the base centre.
"""

import math
import re

SEGMENTS = 48
GRID_CELLS = 12
LABEL_SEGMENTS = 7

DEFAULTS = {
    "shape": "box", "style": "shaded",
    "body_width": 8.0, "body_height": 20.0, "body_depth": 8.0,
    "neck_width": 3.0, "neck_height": 9.0, "neck_depth": 3.0,
    "pos_x": 0.5, "pos_y": 0.8, "scale": 2.0,
    "rotate_x": 0.0, "rotate_y": 0.0, "rotate_z": 0.0,
    "fov": 40.0, "camera_pitch": 15.0, "camera_roll": 0.0, "camera_orbit": 0.0,
    "body_color": "#D0D0D0", "neck_color": "#D0D0D0", "label_color": "#FFFFFF",
    "front_label": True, "fill_opacity": 0.5, "outline_color": "#000000", "outline_width": 2.0, "grid_spacing": 5.0,
}

LIMITS = {
    "body_width": (0.1, 200), "body_height": (0.1, 200), "body_depth": (0.1, 200),
    "neck_width": (0.1, 200), "neck_height": (0.1, 200), "neck_depth": (0.1, 200),
    "pos_x": (-1, 2), "pos_y": (-1, 2), "scale": (0.01, 100),
    "rotate_x": (-360, 360), "rotate_y": (-360, 360), "rotate_z": (-360, 360),
    "fov": (5, 120), "camera_pitch": (-89, 89), "camera_roll": (-180, 180), "camera_orbit": (-360, 360),
    "fill_opacity": (0, 1), "outline_width": (0, 32), "grid_spacing": (0.1, 200),
}

DEG = math.pi / 180


def parse_color(value):
    text = str(value if value is not None else "").strip().removeprefix("#")
    if re.fullmatch(r"[0-9a-fA-F]{3}", text):
        return [int(ch * 2, 16) for ch in text]
    if re.fullmatch(r"[0-9a-fA-F]{6}", text):
        return [int(text[i:i + 2], 16) for i in (0, 2, 4)]
    return None


def normalize_params(raw):
    """Fill in defaults and clamp. Raises ValueError for an unusable colour or number."""
    p = {}
    for name, (low, high) in LIMITS.items():
        value = float(raw.get(name, DEFAULTS[name]))
        if not math.isfinite(value):
            raise ValueError(f"{name} must be a finite number.")
        p[name] = min(high, max(low, value))
    p["shape"] = "cylinder" if raw.get("shape") == "cylinder" else "box"
    p["style"] = "outline" if raw.get("style") == "outline" else "shaded"
    p["front_label"] = bool(raw.get("front_label", DEFAULTS["front_label"]))
    for name in ("body_color", "neck_color", "label_color", "outline_color"):
        value = raw.get(name, DEFAULTS[name])
        rgb = parse_color(value)
        if rgb is None:
            raise ValueError(f"{name} must be a hex colour such as #FF00FF, got {value!r}.")
        p[name] = rgb
    return p


# ---- small vector helpers ---------------------------------------------------

def _normalize3(v):
    length = math.hypot(v[0], v[1], v[2])
    return [v[0] / length, v[1] / length, v[2] / length] if length > 0 else [0.0, 0.0, 0.0]


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _rot_x(deg):
    c, s = math.cos(deg * DEG), math.sin(deg * DEG)
    return [[1, 0, 0], [0, c, -s], [0, s, c]]


def _rot_y(deg):
    c, s = math.cos(deg * DEG), math.sin(deg * DEG)
    return [[c, 0, s], [0, 1, 0], [-s, 0, c]]


def _rot_z(deg):
    c, s = math.cos(deg * DEG), math.sin(deg * DEG)
    return [[c, -s, 0], [s, c, 0], [0, 0, 1]]


def _mul(a, b):
    return [[a[i][0] * b[0][j] + a[i][1] * b[1][j] + a[i][2] * b[2][j] for j in range(3)] for i in range(3)]


def _apply(m, v):
    return [_dot(m[0], v), _dot(m[1], v), _dot(m[2], v)]


def _apply_transposed(m, v):
    return [m[0][i] * v[0] + m[1][i] * v[1] + m[2][i] * v[2] for i in range(3)]


def _add(a, b):
    return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]


LIGHT = _normalize3([-0.4, 0.75, 0.55])


# ---- camera -----------------------------------------------------------------

def make_camera(p, width, height):
    cx, cy = width / 2, height / 2
    f = (height / 2) / math.tan((p["fov"] * DEG) / 2)
    ppu = (p["scale"] / 100) * height
    T = [(p["pos_x"] * width - cx) / ppu, -(p["pos_y"] * height - cy) / ppu, -f / ppu]
    Rw = _mul(_rot_z(p["camera_roll"]), _mul(_rot_x(p["camera_pitch"]), _rot_y(p["camera_orbit"])))
    Ro = _mul(Rw, _mul(_rot_y(p["rotate_y"]), _mul(_rot_x(p["rotate_x"]), _rot_z(p["rotate_z"]))))
    return {"width": width, "height": height, "cx": cx, "cy": cy, "f": f, "ppu": ppu,
            "T": T, "Rw": Rw, "Ro": Ro, "near": 0.02 * (f / ppu)}


def _project(cam, v):
    return [cam["cx"] + (cam["f"] * v[0]) / -v[2], cam["cy"] - (cam["f"] * v[1]) / -v[2]]


def _object_to_camera(cam, v):
    return _add(_apply(cam["Ro"], v), cam["T"])


def _world_to_camera(cam, v):
    return _add(_apply(cam["Rw"], v), cam["T"])


def _clip_polygon_near(points, near):
    """Sutherland-Hodgman against the near plane z = -near."""
    out = []
    for i, a in enumerate(points):
        b = points[(i + 1) % len(points)]
        a_in, b_in = a[2] <= -near, b[2] <= -near
        if a_in:
            out.append(a)
        if a_in != b_in:
            t = (-near - a[2]) / (b[2] - a[2])
            out.append([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, -near])
    return out


def _clip_segment_near(a, b, near):
    a_in, b_in = a[2] <= -near, b[2] <= -near
    if not a_in and not b_in:
        return None
    if a_in and b_in:
        return [a, b]
    t = (-near - a[2]) / (b[2] - a[2])
    mid = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, -near]
    return [a, mid] if a_in else [mid, b]


def _clip_segment_rect(a, b, x0, y0, x1, y1):
    """Liang-Barsky clip of a 2D segment to a rectangle."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    t0, t1 = 0.0, 1.0
    for p_edge, q in ((-dx, a[0] - x0), (dx, x1 - a[0]), (-dy, a[1] - y0), (dy, y1 - a[1])):
        if p_edge == 0:
            if q < 0:
                return None
        else:
            r = q / p_edge
            if p_edge < 0:
                if r > t1:
                    return None
                t0 = max(t0, r)
            else:
                if r < t0:
                    return None
                t1 = min(t1, r)
    return [[a[0] + dx * t0, a[1] + dy * t0], [a[0] + dx * t1, a[1] + dy * t1]]


# ---- bottle parts -----------------------------------------------------------

def _ellipse_point(hx, hz, index, y):
    angle = (2 * math.pi * index) / SEGMENTS
    return [hx * math.sin(angle), y, hz * math.cos(angle)]


def _part_faces(shape, width, height, depth, y0):
    """Faces wind counter-clockwise seen from outside.

    The flag marks faces whose own border is a visible edge (flat faces);
    curved sides only get a silhouette.
    """
    hx, hz, y1 = width / 2, depth / 2, y0 + height
    if shape == "cylinder":
        faces, top, bottom = [], [], []
        for i in range(SEGMENTS):
            faces.append(([_ellipse_point(hx, hz, i, y0), _ellipse_point(hx, hz, i + 1, y0),
                           _ellipse_point(hx, hz, i + 1, y1), _ellipse_point(hx, hz, i, y1)], False))
            top.append(_ellipse_point(hx, hz, i, y1))
            bottom.append(_ellipse_point(hx, hz, SEGMENTS - i, y0))
        faces.append((top, True))
        faces.append((bottom, True))
        return faces
    return [
        ([[-hx, y0, hz], [hx, y0, hz], [hx, y1, hz], [-hx, y1, hz]], True),
        ([[hx, y0, hz], [hx, y0, -hz], [hx, y1, -hz], [hx, y1, hz]], True),
        ([[hx, y0, -hz], [-hx, y0, -hz], [-hx, y1, -hz], [hx, y1, -hz]], True),
        ([[-hx, y0, -hz], [-hx, y0, hz], [-hx, y1, hz], [-hx, y1, -hz]], True),
        ([[-hx, y1, hz], [hx, y1, hz], [hx, y1, -hz], [-hx, y1, -hz]], True),
        ([[-hx, y0, -hz], [hx, y0, -hz], [hx, y0, hz], [-hx, y0, hz]], True),
    ]


def _label_faces(shape, width, height, depth):
    """A patch on the front (+Z) of the body, standing in for the product label."""
    hx, hz = width / 2, depth / 2
    y0, y1 = 0.18 * height, 0.72 * height
    if shape == "cylinder":
        return [([_ellipse_point(hx, hz, i, y0), _ellipse_point(hx, hz, i + 1, y0),
                  _ellipse_point(hx, hz, i + 1, y1), _ellipse_point(hx, hz, i, y1)], False)
                for i in range(-LABEL_SEGMENTS, LABEL_SEGMENTS)]
    lx = 0.8 * hx
    return [([[-lx, y0, hz], [lx, y0, hz], [lx, y1, hz], [-lx, y1, hz]], False)]


def _face_geometry(cam, points):
    cam_points = [_object_to_camera(cam, point) for point in points]
    # Newell normal and centroid.
    normal, centre = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
    for i, a in enumerate(cam_points):
        b = cam_points[(i + 1) % len(cam_points)]
        normal[0] += (a[1] - b[1]) * (a[2] + b[2])
        normal[1] += (a[2] - b[2]) * (a[0] + b[0])
        normal[2] += (a[0] - b[0]) * (a[1] + b[1])
        centre = _add(centre, a)
    unit = _normalize3(normal)
    # The camera sits at the origin, so the centroid is also the view vector.
    if _dot(unit, centre) >= 0:
        return None
    clipped = _clip_polygon_near(cam_points, cam["near"])
    if len(clipped) < 3:
        return None
    return unit, [_project(cam, point) for point in clipped]


def _shade(rgb, normal):
    k = 0.45 + 0.55 * max(0.0, _dot(normal, LIGHT))
    return [math.floor(channel * k + 0.5) for channel in rgb]


def _convex_hull(points):
    ordered = sorted(points, key=lambda point: (point[0], point[1]))
    if len(ordered) < 3:
        return ordered

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for point in ordered:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper = []
    for point in reversed(ordered):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def _label_border(quads):
    """Border of the label patch.

    The visible quads form one strip, so walk along its bottom edge and back
    along its top.
    """
    if not quads or any(len(points) != 4 for points in quads):
        return None
    last = quads[-1]
    bottom = [points[0] for points in quads] + [last[1]]
    top = [last[2]] + [points[3] for points in reversed(quads)]
    return bottom + top


def _part_ops(cam, faces, rgb, look, labels, label_rgb):
    """`look` is (outline rgb, outline width)."""
    outline_rgb, line_width = look
    fills, strokes, outline_points, label_quads = [], [], [], []

    def stroke(points):
        strokes.append({"points": points, "fill": None, "stroke": outline_rgb, "width": line_width})

    for points, outline in faces:
        geometry = _face_geometry(cam, points)
        if geometry is None:
            continue
        normal, projected = geometry
        fills.append({"points": projected, "fill": _shade(rgb, normal), "stroke": None, "width": 0})
        outline_points.extend(projected)
        if outline and line_width > 0:
            stroke(projected)
    for points, _outline in labels:
        geometry = _face_geometry(cam, points)
        if geometry is None:
            continue
        fills.append({"points": geometry[1], "fill": _shade(label_rgb, geometry[0]), "stroke": None, "width": 0})
        label_quads.append(geometry[1])
    # Each part is convex, so the hull of its visible faces is its silhouette.
    if line_width > 0 and len(outline_points) >= 3:
        stroke(_convex_hull(outline_points))
    border = _label_border(label_quads) if line_width > 0 else None
    if border:
        stroke(border)
    return fills + strokes


def build_bottle(p, cam):
    """Draw list for the bottle, back to front.

    Each op is a closed polygon with either a fill (also stroked hairline in its
    own colour to hide seams) or a stroke of `width` image pixels.

    Renderers draw it as two layers. Faces: the fills, painted, blended at
    fill_opacity (shaded style only). Edges: the strokes, with every fill erasing
    what is under it so lines behind a nearer face stay hidden; always solid.
    """
    look = (p["outline_color"], (p["outline_width"] * cam["height"]) / 1024)
    shape = p["shape"]
    labels = _label_faces(shape, p["body_width"], p["body_height"], p["body_depth"]) if p["front_label"] else []
    body = _part_ops(cam, _part_faces(shape, p["body_width"], p["body_height"], p["body_depth"], 0),
                     p["body_color"], look, labels, p["label_color"])
    neck = _part_ops(cam, _part_faces(shape, p["neck_width"], p["neck_height"], p["neck_depth"], p["body_height"]),
                     p["neck_color"], look, [], p["label_color"])
    # The plane y = body_height separates the two convex parts; whichever side
    # the camera is on is drawn last.
    camera_y = -_apply_transposed(cam["Ro"], cam["T"])[1]
    return body + neck if camera_y > p["body_height"] else neck + body


# ---- perspective grid -------------------------------------------------------

def _push_line(lines, cam, a, b, rgba, width):
    clipped = _clip_segment_near(a, b, cam["near"])
    if clipped is None:
        return
    margin = 8 + width
    segment = _clip_segment_rect(_project(cam, clipped[0]), _project(cam, clipped[1]),
                                 -margin, -margin, cam["width"] + margin, cam["height"] + margin)
    if segment is not None:
        lines.append({"a": segment[0], "b": segment[1], "rgba": rgba, "width": width})


def build_grid(p, cam):
    """Ground-plane grid through the base centre, the vertical axis and the horizon.

    Returned back to front: dark underlays first, so light lines stay readable
    on any background.
    """
    unit = cam["height"] / 1024
    spacing = p["grid_spacing"]
    extent = GRID_CELLS * spacing
    light, dark = [], []

    def line(a, b, rgba, width):
        _push_line(dark, cam, a, b, [0, 0, 0, 0.35], (width + 1.5) * unit)
        _push_line(light, cam, a, b, rgba, width * unit)

    for i in range(-GRID_CELLS, GRID_CELLS + 1):
        if i == 0:
            continue
        offset = i * spacing
        rgba = [255, 255, 255, 0.8 if i % 4 == 0 else 0.45]
        line(_world_to_camera(cam, [offset, 0, -extent]), _world_to_camera(cam, [offset, 0, extent]), rgba, 1)
        line(_world_to_camera(cam, [-extent, 0, offset]), _world_to_camera(cam, [extent, 0, offset]), rgba, 1)
    line(_world_to_camera(cam, [-extent, 0, 0]), _world_to_camera(cam, [extent, 0, 0]), [255, 80, 80, 0.95], 1.5)
    line(_world_to_camera(cam, [0, 0, -extent]), _world_to_camera(cam, [0, 0, extent]), [80, 140, 255, 0.95], 1.5)
    line(_world_to_camera(cam, [0, 0, 0]), _world_to_camera(cam, [0, extent, 0]), [90, 220, 90, 0.95], 1.5)

    horizon = _horizon_segment(cam)
    if horizon is not None:
        dark.append({"a": horizon[0], "b": horizon[1], "rgba": [0, 0, 0, 0.35], "width": 3 * unit})
        light.append({"a": horizon[0], "b": horizon[1], "rgba": [255, 190, 60, 0.95], "width": 1.5 * unit})
    return dark + light


def _horizon_segment(cam):
    """Vanishing line of the ground plane, clipped to the image."""
    Rw, f = cam["Rw"], cam["f"]
    n = [Rw[0][1], Rw[1][1], Rw[2][1]]
    reach = 4 * (cam["width"] + cam["height"])
    if abs(n[1]) >= abs(n[0]):
        if abs(n[1]) < 1e-6:
            return None
        a = [-reach, (n[2] * f + n[0] * reach) / n[1]]
        b = [reach, (n[2] * f - n[0] * reach) / n[1]]
    else:
        a = [(n[2] * f + n[1] * reach) / n[0], -reach]
        b = [(n[2] * f - n[1] * reach) / n[0], reach]
    return _clip_segment_rect([cam["cx"] + a[0], cam["cy"] - a[1]], [cam["cx"] + b[0], cam["cy"] - b[1]],
                              -8, -8, cam["width"] + 8, cam["height"] + 8)
