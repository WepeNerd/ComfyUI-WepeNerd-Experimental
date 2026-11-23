// Geometry for the Bottle Placeholder node.
//
// This file and bottle_placeholder_core.py implement the same maths line for
// line: the browser preview and the queued render both come from the draw list
// built here, so they must stay in step. tests/test_bottle_placeholder.py
// compares the two outputs numerically.
//
// Conventions
//   object space : origin at the centre of the bottle base, +Y up, +Z front
//   world space  : the ground plane is Y = 0 through the base centre
//   camera space : camera at the origin looking down -Z, +Y up
//   image space  : pixels, origin top left
// Bottle dimensions are in arbitrary units (centimetres are convenient).
// `scale` is the size of one unit as a percentage of the image height, measured
// at the depth of the base centre.

export const SEGMENTS = 48;
export const GRID_CELLS = 12;
export const LABEL_SEGMENTS = 7;

export const DEFAULTS = Object.freeze({
    shape: "box", style: "shaded",
    body_width: 8, body_height: 20, body_depth: 8,
    neck_width: 3, neck_height: 9, neck_depth: 3,
    pos_x: 0.5, pos_y: 0.8, scale: 2,
    rotate_x: 0, rotate_y: 0, rotate_z: 0,
    fov: 40, camera_pitch: 15, camera_roll: 0, camera_orbit: 0,
    body_color: "#D0D0D0", neck_color: "#D0D0D0", label_color: "#FFFFFF",
    front_label: true, fill_opacity: 0.5, outline_color: "#000000", outline_width: 2, grid_spacing: 5,
});

const LIMITS = {
    body_width: [0.1, 200], body_height: [0.1, 200], body_depth: [0.1, 200],
    neck_width: [0.1, 200], neck_height: [0.1, 200], neck_depth: [0.1, 200],
    pos_x: [-1, 2], pos_y: [-1, 2], scale: [0.01, 100],
    rotate_x: [-360, 360], rotate_y: [-360, 360], rotate_z: [-360, 360],
    fov: [5, 120], camera_pitch: [-89, 89], camera_roll: [-180, 180], camera_orbit: [-360, 360],
    fill_opacity: [0, 1], outline_width: [0, 32], grid_spacing: [0.1, 200],
};

const LIGHT = normalize3([-0.4, 0.75, 0.55]);
const DEG = Math.PI / 180;

export function parseColor(value) {
    const text = String(value ?? "").trim().replace(/^#/, "");
    if (/^[0-9a-fA-F]{3}$/.test(text)) {
        return [0, 1, 2].map((i) => parseInt(text[i] + text[i], 16));
    }
    if (/^[0-9a-fA-F]{6}$/.test(text)) {
        return [0, 2, 4].map((i) => parseInt(text.slice(i, i + 2), 16));
    }
    return null;
}

// Fill in defaults and clamp. Unparseable colours fall back to the default so
// the preview keeps drawing while the user is still typing a hex value; the
// Python side raises instead.
export function normalizeParams(raw = {}) {
    const p = {};
    for (const [name, [low, high]] of Object.entries(LIMITS)) {
        const value = Number(raw[name]);
        p[name] = Math.min(high, Math.max(low, Number.isFinite(value) ? value : DEFAULTS[name]));
    }
    p.shape = raw.shape === "cylinder" ? "cylinder" : "box";
    p.style = raw.style === "outline" ? "outline" : "shaded";
    p.front_label = raw.front_label === undefined ? DEFAULTS.front_label : Boolean(raw.front_label);
    for (const name of ["body_color", "neck_color", "label_color", "outline_color"]) {
        p[name] = parseColor(raw[name]) ?? parseColor(DEFAULTS[name]);
    }
    return p;
}

// ---- small vector helpers -------------------------------------------------

function normalize3(v) {
    const length = Math.hypot(v[0], v[1], v[2]);
    return length > 0 ? [v[0] / length, v[1] / length, v[2] / length] : [0, 0, 0];
}

function dot(a, b) {
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

function rotX(deg) {
    const c = Math.cos(deg * DEG), s = Math.sin(deg * DEG);
    return [[1, 0, 0], [0, c, -s], [0, s, c]];
}

function rotY(deg) {
    const c = Math.cos(deg * DEG), s = Math.sin(deg * DEG);
    return [[c, 0, s], [0, 1, 0], [-s, 0, c]];
}

function rotZ(deg) {
    const c = Math.cos(deg * DEG), s = Math.sin(deg * DEG);
    return [[c, -s, 0], [s, c, 0], [0, 0, 1]];
}

function mul(a, b) {
    const out = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
    for (let i = 0; i < 3; i++) {
        for (let j = 0; j < 3; j++) {
            out[i][j] = a[i][0] * b[0][j] + a[i][1] * b[1][j] + a[i][2] * b[2][j];
        }
    }
    return out;
}

function apply(m, v) {
    return [dot(m[0], v), dot(m[1], v), dot(m[2], v)];
}

function applyTransposed(m, v) {
    return [
        m[0][0] * v[0] + m[1][0] * v[1] + m[2][0] * v[2],
        m[0][1] * v[0] + m[1][1] * v[1] + m[2][1] * v[2],
        m[0][2] * v[0] + m[1][2] * v[1] + m[2][2] * v[2],
    ];
}

function add(a, b) {
    return [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
}

// ---- camera ---------------------------------------------------------------

export function makeCamera(p, width, height) {
    const cx = width / 2, cy = height / 2;
    const f = (height / 2) / Math.tan((p.fov * DEG) / 2);
    const ppu = (p.scale / 100) * height;
    const T = [(p.pos_x * width - cx) / ppu, -(p.pos_y * height - cy) / ppu, -f / ppu];
    const Rw = mul(rotZ(p.camera_roll), mul(rotX(p.camera_pitch), rotY(p.camera_orbit)));
    const Ro = mul(Rw, mul(rotY(p.rotate_y), mul(rotX(p.rotate_x), rotZ(p.rotate_z))));
    return { width, height, cx, cy, f, ppu, T, Rw, Ro, near: 0.02 * (f / ppu) };
}

function project(cam, v) {
    return [cam.cx + (cam.f * v[0]) / -v[2], cam.cy - (cam.f * v[1]) / -v[2]];
}

function objectToCamera(cam, v) {
    return add(apply(cam.Ro, v), cam.T);
}

function worldToCamera(cam, v) {
    return add(apply(cam.Rw, v), cam.T);
}

// Sutherland-Hodgman against the near plane z = -near.
function clipPolygonNear(points, near) {
    const out = [];
    for (let i = 0; i < points.length; i++) {
        const a = points[i], b = points[(i + 1) % points.length];
        const aIn = a[2] <= -near, bIn = b[2] <= -near;
        if (aIn) out.push(a);
        if (aIn !== bIn) {
            const t = (-near - a[2]) / (b[2] - a[2]);
            out.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, -near]);
        }
    }
    return out;
}

function clipSegmentNear(a, b, near) {
    const aIn = a[2] <= -near, bIn = b[2] <= -near;
    if (!aIn && !bIn) return null;
    if (aIn && bIn) return [a, b];
    const t = (-near - a[2]) / (b[2] - a[2]);
    const mid = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, -near];
    return aIn ? [a, mid] : [mid, b];
}

// Liang-Barsky clip of a 2D segment to a rectangle.
function clipSegmentRect(a, b, x0, y0, x1, y1) {
    const dx = b[0] - a[0], dy = b[1] - a[1];
    let t0 = 0, t1 = 1;
    const edges = [[-dx, a[0] - x0], [dx, x1 - a[0]], [-dy, a[1] - y0], [dy, y1 - a[1]]];
    for (const [pEdge, q] of edges) {
        if (pEdge === 0) {
            if (q < 0) return null;
        } else {
            const r = q / pEdge;
            if (pEdge < 0) {
                if (r > t1) return null;
                if (r > t0) t0 = r;
            } else {
                if (r < t0) return null;
                if (r < t1) t1 = r;
            }
        }
    }
    return [[a[0] + dx * t0, a[1] + dy * t0], [a[0] + dx * t1, a[1] + dy * t1]];
}

// ---- bottle parts ---------------------------------------------------------

function ellipsePoint(hx, hz, index, y) {
    const angle = (2 * Math.PI * index) / SEGMENTS;
    return [hx * Math.sin(angle), y, hz * Math.cos(angle)];
}

// Faces wind counter-clockwise seen from outside. `outline` marks faces whose
// own border is a visible edge (flat faces); curved sides only get a silhouette.
function partFaces(shape, width, height, depth, y0) {
    const hx = width / 2, hz = depth / 2, y1 = y0 + height;
    if (shape === "cylinder") {
        const faces = [];
        const top = [], bottom = [];
        for (let i = 0; i < SEGMENTS; i++) {
            faces.push({
                points: [ellipsePoint(hx, hz, i, y0), ellipsePoint(hx, hz, i + 1, y0),
                    ellipsePoint(hx, hz, i + 1, y1), ellipsePoint(hx, hz, i, y1)],
                outline: false,
            });
            top.push(ellipsePoint(hx, hz, i, y1));
            bottom.push(ellipsePoint(hx, hz, SEGMENTS - i, y0));
        }
        faces.push({ points: top, outline: true }, { points: bottom, outline: true });
        return faces;
    }
    const quad = (...points) => ({ points, outline: true });
    return [
        quad([-hx, y0, hz], [hx, y0, hz], [hx, y1, hz], [-hx, y1, hz]),
        quad([hx, y0, hz], [hx, y0, -hz], [hx, y1, -hz], [hx, y1, hz]),
        quad([hx, y0, -hz], [-hx, y0, -hz], [-hx, y1, -hz], [hx, y1, -hz]),
        quad([-hx, y0, -hz], [-hx, y0, hz], [-hx, y1, hz], [-hx, y1, -hz]),
        quad([-hx, y1, hz], [hx, y1, hz], [hx, y1, -hz], [-hx, y1, -hz]),
        quad([-hx, y0, -hz], [hx, y0, -hz], [hx, y0, hz], [-hx, y0, hz]),
    ];
}

// A patch on the front (+Z) of the body, standing in for the product label.
function labelFaces(shape, width, height, depth) {
    const hx = width / 2, hz = depth / 2;
    const y0 = 0.18 * height, y1 = 0.72 * height;
    if (shape === "cylinder") {
        const faces = [];
        for (let i = -LABEL_SEGMENTS; i < LABEL_SEGMENTS; i++) {
            faces.push({
                points: [ellipsePoint(hx, hz, i, y0), ellipsePoint(hx, hz, i + 1, y0),
                    ellipsePoint(hx, hz, i + 1, y1), ellipsePoint(hx, hz, i, y1)],
                outline: false,
            });
        }
        return faces;
    }
    const lx = 0.8 * hx;
    return [{ points: [[-lx, y0, hz], [lx, y0, hz], [lx, y1, hz], [-lx, y1, hz]], outline: false }];
}

function faceGeometry(cam, points) {
    const camPoints = points.map((point) => objectToCamera(cam, point));
    // Newell normal and centroid.
    const normal = [0, 0, 0], centre = [0, 0, 0];
    for (let i = 0; i < camPoints.length; i++) {
        const a = camPoints[i], b = camPoints[(i + 1) % camPoints.length];
        normal[0] += (a[1] - b[1]) * (a[2] + b[2]);
        normal[1] += (a[2] - b[2]) * (a[0] + b[0]);
        normal[2] += (a[0] - b[0]) * (a[1] + b[1]);
        centre[0] += a[0]; centre[1] += a[1]; centre[2] += a[2];
    }
    const unit = normalize3(normal);
    // The camera sits at the origin, so the centroid is also the view vector.
    if (dot(unit, centre) >= 0) return null;
    const clipped = clipPolygonNear(camPoints, cam.near);
    if (clipped.length < 3) return null;
    return { normal: unit, points: clipped.map((point) => project(cam, point)) };
}

function shade(rgb, normal) {
    const k = 0.45 + 0.55 * Math.max(0, dot(normal, LIGHT));
    return rgb.map((channel) => Math.floor(channel * k + 0.5));
}

function convexHull(points) {
    const sorted = [...points].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    if (sorted.length < 3) return sorted;
    const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
    const lower = [];
    for (const point of sorted) {
        while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], point) <= 0) lower.pop();
        lower.push(point);
    }
    const upper = [];
    for (let i = sorted.length - 1; i >= 0; i--) {
        const point = sorted[i];
        while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], point) <= 0) upper.pop();
        upper.push(point);
    }
    lower.pop();
    upper.pop();
    return lower.concat(upper);
}

// Border of the label patch: the visible quads form one strip, so walk along
// its bottom edge and back along its top.
function labelBorder(quads) {
    if (!quads.length || quads.some((points) => points.length !== 4)) return null;
    const last = quads[quads.length - 1];
    const bottom = quads.map((points) => points[0]).concat([last[1]]);
    const top = [last[2]].concat(quads.map((points) => points[3]).reverse());
    return bottom.concat(top);
}

// `look` carries the outline colour and width.
function partOps(cam, faces, rgb, look, labels, labelRgb) {
    const fills = [], strokes = [], outlinePoints = [], labelQuads = [];
    const stroke = (points) => strokes.push({ points, fill: null, stroke: look.rgb, width: look.width });
    for (const face of faces) {
        const geometry = faceGeometry(cam, face.points);
        if (!geometry) continue;
        fills.push({ points: geometry.points, fill: shade(rgb, geometry.normal), stroke: null, width: 0 });
        outlinePoints.push(...geometry.points);
        if (face.outline && look.width > 0) stroke(geometry.points);
    }
    for (const face of labels) {
        const geometry = faceGeometry(cam, face.points);
        if (!geometry) continue;
        fills.push({ points: geometry.points, fill: shade(labelRgb, geometry.normal), stroke: null, width: 0 });
        labelQuads.push(geometry.points);
    }
    // Each part is convex, so the hull of its visible faces is its silhouette.
    if (look.width > 0 && outlinePoints.length >= 3) stroke(convexHull(outlinePoints));
    const border = look.width > 0 ? labelBorder(labelQuads) : null;
    if (border) stroke(border);
    return fills.concat(strokes);
}

// Draw list for the bottle, back to front. Each op is a closed polygon with
// either a fill (also stroked hairline in its own colour to hide seams) or a
// stroke of `width` image pixels.
//
// Renderers draw it as two layers. Faces: the fills, painted, blended at
// fill_opacity (shaded style only). Edges: the strokes, with every fill erasing
// what is under it so lines behind a nearer face stay hidden; always solid.
export function buildBottle(p, cam) {
    const look = { rgb: p.outline_color, width: (p.outline_width * cam.height) / 1024 };
    const body = partOps(
        cam, partFaces(p.shape, p.body_width, p.body_height, p.body_depth, 0), p.body_color, look,
        p.front_label ? labelFaces(p.shape, p.body_width, p.body_height, p.body_depth) : [], p.label_color,
    );
    const neck = partOps(
        cam, partFaces(p.shape, p.neck_width, p.neck_height, p.neck_depth, p.body_height), p.neck_color, look,
        [], p.label_color,
    );
    // The plane y = body_height separates the two convex parts; whichever side
    // the camera is on is drawn last.
    const cameraY = -applyTransposed(cam.Ro, cam.T)[1];
    return cameraY > p.body_height ? body.concat(neck) : neck.concat(body);
}

// ---- perspective grid -----------------------------------------------------

function pushLine(lines, cam, a, b, rgba, width) {
    const clipped = clipSegmentNear(a, b, cam.near);
    if (!clipped) return;
    const margin = 8 + width;
    const segment = clipSegmentRect(
        project(cam, clipped[0]), project(cam, clipped[1]),
        -margin, -margin, cam.width + margin, cam.height + margin,
    );
    if (segment) lines.push({ a: segment[0], b: segment[1], rgba, width });
}

// Ground-plane grid through the base centre, the vertical axis and the horizon.
// Returned back to front: dark underlays first, so light lines stay readable on
// any background.
export function buildGrid(p, cam) {
    const unit = cam.height / 1024;
    const extent = GRID_CELLS * p.grid_spacing;
    const light = [], dark = [];
    const line = (a, b, rgba, width) => {
        pushLine(dark, cam, a, b, [0, 0, 0, 0.35], (width + 1.5) * unit);
        pushLine(light, cam, a, b, rgba, width * unit);
    };
    for (let i = -GRID_CELLS; i <= GRID_CELLS; i++) {
        if (i === 0) continue;
        const offset = i * p.grid_spacing;
        const rgba = [255, 255, 255, i % 4 === 0 ? 0.8 : 0.45];
        line(worldToCamera(cam, [offset, 0, -extent]), worldToCamera(cam, [offset, 0, extent]), rgba, 1);
        line(worldToCamera(cam, [-extent, 0, offset]), worldToCamera(cam, [extent, 0, offset]), rgba, 1);
    }
    line(worldToCamera(cam, [-extent, 0, 0]), worldToCamera(cam, [extent, 0, 0]), [255, 80, 80, 0.95], 1.5);
    line(worldToCamera(cam, [0, 0, -extent]), worldToCamera(cam, [0, 0, extent]), [80, 140, 255, 0.95], 1.5);
    line(worldToCamera(cam, [0, 0, 0]), worldToCamera(cam, [0, extent, 0]), [90, 220, 90, 0.95], 1.5);

    const horizon = horizonSegment(cam);
    if (horizon) {
        dark.push({ a: horizon[0], b: horizon[1], rgba: [0, 0, 0, 0.35], width: 3 * unit });
        light.push({ a: horizon[0], b: horizon[1], rgba: [255, 190, 60, 0.95], width: 1.5 * unit });
    }
    return dark.concat(light);
}

// Vanishing line of the ground plane, clipped to the image.
function horizonSegment(cam) {
    const n = [cam.Rw[0][1], cam.Rw[1][1], cam.Rw[2][1]];
    const reach = 4 * (cam.width + cam.height);
    let a, b;
    if (Math.abs(n[1]) >= Math.abs(n[0])) {
        if (Math.abs(n[1]) < 1e-6) return null;
        const y = (x) => (n[2] * cam.f - n[0] * x) / n[1];
        a = [-reach, y(-reach)];
        b = [reach, y(reach)];
    } else {
        const x = (y) => (n[2] * cam.f - n[1] * y) / n[0];
        a = [x(-reach), -reach];
        b = [x(reach), reach];
    }
    return clipSegmentRect(
        [cam.cx + a[0], cam.cy - a[1]], [cam.cx + b[0], cam.cy - b[1]],
        -8, -8, cam.width + 8, cam.height + 8,
    );
}

// ---- editor helpers (browser only; no Python counterpart) -------------------

// Where a point on the ground plane lands on screen, or null behind the camera.
export function projectGround(cam, x, z) {
    const point = worldToCamera(cam, [x, 0, z]);
    return point[2] <= -cam.near ? project(cam, point) : null;
}

// Drag handles: where they sit on screen and how one unit along their axis
// moves on screen, so a mouse delta can be turned into a dimension change.
// They sit on edges, which keeps the faces free for grabbing the bottle itself.
export function buildHandles(p, cam) {
    const bodyTop = p.body_height, neckMid = p.body_height + p.neck_height / 2;
    const specs = [
        ["body_width", [p.body_width / 2, bodyTop / 2, 0], [1, 0, 0], 2],
        ["body_depth", [0, 0, p.body_depth / 2], [0, 0, 1], 2],
        ["body_height", [-p.body_width / 2, bodyTop, 0], [0, 1, 0], 1],
        ["neck_width", [p.neck_width / 2, neckMid, 0], [1, 0, 0], 2],
        ["neck_depth", [0, bodyTop, p.neck_depth / 2], [0, 0, 1], 2],
        ["neck_height", [0, bodyTop + p.neck_height, 0], [0, 1, 0], 1],
    ];
    const handles = [];
    for (const [param, position, axis, factor] of specs) {
        const a = objectToCamera(cam, position);
        const b = objectToCamera(cam, add(position, axis));
        if (a[2] > -cam.near || b[2] > -cam.near) continue;
        const pa = project(cam, a), pb = project(cam, b);
        handles.push({ param, position: pa, axis: [pb[0] - pa[0], pb[1] - pa[1]], factor });
    }
    return handles;
}

export function rotateRingRadius(p) {
    return 0.75 * Math.max(p.body_width, p.body_depth) + 1;
}

// Ring on the ground around the base, with a knob marking the bottle's front.
export function buildRotateRing(p, cam) {
    const radius = rotateRingRadius(p);
    const points = [];
    for (let i = 0; i <= 64; i++) {
        const angle = (2 * Math.PI * i) / 64;
        const point = worldToCamera(cam, [radius * Math.sin(angle), 0, radius * Math.cos(angle)]);
        points.push(point[2] <= -cam.near ? project(cam, point) : null);
    }
    const yaw = p.rotate_y * DEG;
    const knob = worldToCamera(cam, [radius * Math.sin(yaw), 0, radius * Math.cos(yaw)]);
    return { points, knob: knob[2] <= -cam.near ? project(cam, knob) : null };
}

// Where a screen point lands on the ground plane, as world [x, z].
export function groundPoint(cam, x, y) {
    const ray = [(x - cam.cx) / cam.f, -(y - cam.cy) / cam.f, -1];
    const n = [cam.Rw[0][1], cam.Rw[1][1], cam.Rw[2][1]];
    const denominator = dot(n, ray);
    if (Math.abs(denominator) < 1e-9) return null;
    const t = dot(n, cam.T) / denominator;
    if (t <= 0) return null;
    const world = applyTransposed(cam.Rw, [ray[0] * t - cam.T[0], ray[1] * t - cam.T[1], ray[2] * t - cam.T[2]]);
    return [world[0], world[2]];
}

export function pointInPolygon(point, polygon) {
    let inside = false;
    for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
        const a = polygon[i], b = polygon[j];
        if ((a[1] > point[1]) !== (b[1] > point[1])
            && point[0] < ((b[0] - a[0]) * (point[1] - a[1])) / (b[1] - a[1]) + a[0]) {
            inside = !inside;
        }
    }
    return inside;
}

// ---- camera from traced lines ---------------------------------------------
//
// Lines drawn along two directions of the surface that are at right angles in
// reality (two table edges, say) each meet at a vanishing point. The two points
// fix the horizon (pitch and roll), the grid direction (orbit) and, because the
// directions are perpendicular, the focal length (fov). The optical centre is
// taken to be the image centre.

function cross(a, b) {
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
}

// Eigenvector of the smallest eigenvalue of a symmetric 3x3 matrix (Jacobi).
function smallestEigenvector(matrix) {
    const a = matrix.map((row) => [...row]);
    const v = [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
    for (let sweep = 0; sweep < 60; sweep++) {
        if (Math.abs(a[0][1]) + Math.abs(a[0][2]) + Math.abs(a[1][2]) < 1e-20) break;
        for (const [p, q] of [[0, 1], [0, 2], [1, 2]]) {
            if (a[p][q] === 0) continue;
            const theta = 0.5 * Math.atan2(2 * a[p][q], a[q][q] - a[p][p]);
            const c = Math.cos(theta), s = Math.sin(theta);
            for (const m of [a, v]) {
                for (let k = 0; k < 3; k++) {
                    const kp = m[k][p], kq = m[k][q];
                    m[k][p] = c * kp - s * kq;
                    m[k][q] = s * kp + c * kq;
                }
            }
            for (let k = 0; k < 3; k++) {
                const pk = a[p][k], qk = a[q][k];
                a[p][k] = c * pk - s * qk;
                a[q][k] = s * pk + c * qk;
            }
        }
    }
    let best = 0;
    for (let i = 1; i < 3; i++) if (a[i][i] < a[best][best]) best = i;
    return [v[0][best], v[1][best], v[2][best]];
}

// Homogeneous vanishing point [X, Y, w] of a group of segments [x1, y1, x2, y2]
// in image pixels. X is right and Y is up from the image centre, in units of
// the image height; w is 0 when the lines are parallel on screen. Null when the
// group does not hold two distinct lines.
export function vanishingPoint(segments, width, height) {
    const lines = [];
    for (const [x1, y1, x2, y2] of segments) {
        const a = [(x1 - width / 2) / height, -(y1 - height / 2) / height, 1];
        const b = [(x2 - width / 2) / height, -(y2 - height / 2) / height, 1];
        if (Math.hypot(a[0] - b[0], a[1] - b[1]) < 1e-4) continue;
        lines.push(normalize3(cross(a, b)));
    }
    let spread = 0;
    for (let i = 0; i < lines.length; i++) {
        for (let j = i + 1; j < lines.length; j++) {
            spread = Math.max(spread, Math.hypot(...cross(lines[i], lines[j])));
        }
    }
    if (spread < 1e-4) return null;
    const scatter = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
    for (const line of lines) {
        for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) scatter[i][j] += line[i] * line[j];
    }
    // The point closest to every line, in the least-squares sense.
    return smallestEigenvector(scatter);
}

// Camera settings from two groups of traced segments. `current` supplies the
// fov and orbit to fall back on. Returns { values, solvedFov, message }; values
// is null when there is not enough to go on.
export function solvePerspective(groupA, groupB, width, height, current) {
    const va = vanishingPoint(groupA, width, height), vb = vanishingPoint(groupB, width, height);
    if (!va && !vb) {
        return { values: null, solvedFov: false, message: "Draw two lines along one edge direction of the surface" };
    }
    let focal = 0.5 / Math.tan((current.fov * DEG) / 2);   // in image heights
    let solvedFov = false;
    if (va && vb) {
        const ww = va[2] * vb[2];
        const focal2 = Math.abs(ww) > 1e-9 ? -(va[0] * vb[0] + va[1] * vb[1]) / ww : NaN;
        const fov = focal2 > 0 ? (2 * Math.atan(0.5 / Math.sqrt(focal2))) / DEG : NaN;
        if (fov >= 5 && fov <= 120) {
            focal = Math.sqrt(focal2);
            solvedFov = true;
        }
    }
    const direction = (v) => normalize3([v[0], v[1], -focal * v[2]]);
    const lead = va ?? vb;
    let normal;
    if (va && vb) {
        normal = normalize3(cross(direction(va), direction(vb)));
    } else {
        // One direction only: assume a level horizon, so the surface normal has no sideways part.
        const d = direction(lead);
        normal = normalize3([0, d[2], -d[1]]);
    }
    if (Math.hypot(...normal) === 0) {
        return { values: null, solvedFov: false, message: "The two directions look the same; trace two different edges" };
    }
    if (normal[1] < 0) normal = normal.map((value) => -value);
    const pitch = Math.asin(Math.max(-1, Math.min(1, normal[2]))) / DEG;
    const roll = Math.atan2(-normal[0], normal[1]) / DEG;

    // The red group runs along the grid's X axis (the blue along Z). Either way
    // along a line is the same direction, so take the turn nearest the current one.
    const u = applyTransposed(mul(rotZ(roll), rotX(pitch)), direction(lead));
    let orbit = (va ? Math.atan2(-u[2], u[0]) : Math.atan2(u[0], u[2])) / DEG;
    const turn = (((orbit - current.camera_orbit + 90) % 180) + 180) % 180 - 90;
    orbit = ((((current.camera_orbit + turn + 180) % 360) + 360) % 360) - 180;

    const values = { camera_pitch: pitch, camera_roll: roll, camera_orbit: orbit };
    if (solvedFov) values.fov = (2 * Math.atan(0.5 / focal)) / DEG;
    const message = solvedFov
        ? `Solved: fov ${values.fov.toFixed(1)}°, pitch ${pitch.toFixed(1)}°, roll ${roll.toFixed(1)}°`
        : va && vb
            ? `Lens not measurable from these lines (one direction is near parallel on screen): kept fov ${current.fov.toFixed(1)}°`
            : `One direction traced: level horizon and fov ${current.fov.toFixed(1)}° assumed. Add two lines along the other direction`;
    return { values, solvedFov, message };
}
