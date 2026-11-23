import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import {
    DEFAULTS, normalizeParams, makeCamera, buildBottle, buildGrid, buildHandles,
    buildRotateRing, groundPoint, pointInPolygon, solvePerspective,
} from "./wn_bottle_geometry.mjs";

// Interactive viewport for the Bottle Placeholder node. It only edits the
// node's ordinary widgets; the queued image is drawn by Python from those same
// values, so nothing here has to be captured or uploaded.

const NODE_NAME = "WN_BottlePlaceholder";
const BACKGROUND_KEY = "wn_bottle_background";
const TRACE_KEY = "wn_bottle_trace";
const GEOMETRY_WIDGETS = Object.keys(DEFAULTS);
const WATCHED_WIDGETS = [...GEOMETRY_WIDGETS, "show_grid"];
const TRACE_COLOURS = { a: "#ff5050", b: "#508cff" };
const HINT = "Drag bottle: move · Drag background: orbit camera · Click, then scroll: size · "
    + "Dots: dimensions · Ring: turn · Alt+drag: tilt · Shift+scroll: turn";
const TRACE_HINT = "Trace the surface's perspective: draw two or more lines along one edge direction (red), "
    + "then along the direction at right angles to it (blue). Drag an end to adjust.";
const HANDLE_RADIUS = 6;      // CSS pixels
const HANDLE_REACH = 11;      // CSS pixels
const TOOLBAR_HEIGHT = 30;
const HINT_HEIGHT = 42;
const MIN_STAGE = 200;
const MAX_STAGE = 640;

// Approximate proportions in centimetres, as starting points to adjust.
const PRESETS = {
    "Round, standard 70 cl": { shape: "cylinder", body: [8, 20, 8], neck: [3, 9, 3] },
    "Round, tall and slim": { shape: "cylinder", body: [7.5, 21, 7.5], neck: [3, 11, 3] },
    "Round, short and wide": { shape: "cylinder", body: [10, 14, 10], neck: [3.4, 6, 3.4] },
    "Square": { shape: "box", body: [8.5, 16, 8.5], neck: [3.2, 7, 3.2] },
    "Rectangular, flat": { shape: "box", body: [10, 17, 6], neck: [3, 6, 3] },
    "Miniature 5 cl": { shape: "cylinder", body: [3.2, 7.5, 3.2], neck: [1.6, 3, 1.6] },
};

const RESETS = {
    bottle: ["pos_x", "pos_y", "scale", "rotate_x", "rotate_y", "rotate_z"],
    camera: ["fov", "camera_pitch", "camera_orbit", "camera_roll"],
};

const LABELS = {
    body_width: "Body width", body_height: "Body height", body_depth: "Body depth",
    neck_width: "Neck width", neck_height: "Neck height", neck_depth: "Neck depth",
};

function getWidget(node, name) {
    return node.widgets?.find((widget) => widget.name === name);
}

function setWidget(node, name, value) {
    const widget = getWidget(node, name);
    if (!widget) return;
    let next = value;
    if (typeof value === "number") {
        const { min, max, step, round } = widget.options ?? {};
        if (Number.isFinite(min)) next = Math.max(min, next);
        if (Number.isFinite(max)) next = Math.min(max, next);
        const grain = Number.isFinite(round) && round > 0 ? round : step;
        if (Number.isFinite(grain) && grain > 0) {
            const decimals = String(grain).split(".")[1]?.length ?? 0;
            next = Number((Math.round(next / grain) * grain).toFixed(decimals));
        }
    }
    if (widget.value === next) return;
    widget.value = next;
    widget.callback?.(next);
}

function wrapDegrees(value) {
    return ((((value + 180) % 360) + 360) % 360) - 180;
}

function rgb(values, alpha = 1) {
    return `rgba(${values[0]},${values[1]},${values[2]},${alpha})`;
}

function viewUrl(descriptor) {
    const params = new URLSearchParams({ filename: descriptor.filename, type: descriptor.type ?? "input" });
    if (descriptor.subfolder) params.set("subfolder", descriptor.subfolder);
    return api.apiURL(`/view?${params}`);
}

// A Load Image style widget value: "sub/folder/name.png [input]".
function describeImageValue(value) {
    const text = String(value ?? "").trim();
    if (!text) return null;
    const annotated = text.match(/^(.*)\s+\[(input|output|temp)\]$/);
    const parts = (annotated ? annotated[1] : text).replaceAll("\\", "/").split("/");
    const filename = parts.pop();
    return { filename, subfolder: parts.join("/"), type: annotated ? annotated[2] : "input" };
}

function button(text, title, onClick) {
    const element = document.createElement("button");
    element.type = "button";
    element.textContent = text;
    element.title = title;
    element.style.cssText = CONTROL_STYLE + "cursor:pointer;";
    element.addEventListener("click", onClick);
    return element;
}

const CONTROL_STYLE = "font:11px sans-serif;height:22px;padding:0 8px;border-radius:4px;"
    + "border:1px solid var(--border-color,#4e4e4e);background:var(--comfy-input-bg,#222);"
    + "color:var(--input-text,#ddd);";

class BottleEditor {
    constructor(node) {
        this.node = node;
        this.background = null;
        this.backgroundSource = "";
        this.aspect = 1;
        this.drag = null;
        this.hover = null;
        this.pointerInside = false;
        this.frame = 0;
        this.signature = "";
        this.faces = document.createElement("canvas");
        this.edges = document.createElement("canvas");
        // Perspective tracing: null when off, else the group being drawn and any
        // line in progress. The lines themselves live in node.properties.
        this.trace = null;
        this.buildDom();
        this.observer = new ResizeObserver(() => this.requestDraw());
        this.observer.observe(this.stage);
        // Catches value changes that bypass widget callbacks: undo, paste, workflow load.
        this.poll = setInterval(() => this.checkForChanges(), 300);
    }

    // ---- DOM ----------------------------------------------------------------

    buildDom() {
        this.root = document.createElement("div");
        this.root.style.cssText = "display:flex;flex-direction:column;width:100%;height:100%;min-height:0;"
            + "box-sizing:border-box;gap:4px;overflow:hidden;";

        const row = () => {
            const element = document.createElement("div");
            element.style.cssText = `display:flex;gap:4px;align-items:center;flex:0 0 ${TOOLBAR_HEIGHT - 4}px;`;
            return element;
        };
        const toolbar = this.toolbar = row();

        const presets = document.createElement("select");
        presets.title = "Set the shape and dimensions to a common bottle proportion";
        presets.style.cssText = CONTROL_STYLE + "flex:1 1 auto;min-width:0;cursor:pointer;";
        presets.append(new Option("Bottle preset…", ""));
        for (const name of Object.keys(PRESETS)) presets.append(new Option(name, name));
        presets.addEventListener("change", () => {
            if (presets.value) this.applyPreset(PRESETS[presets.value]);
            presets.value = "";
        });

        this.gridButton = button("Grid", "Show or hide the perspective grid in this viewport", () => {
            setWidget(this.node, "show_grid", !getWidget(this.node, "show_grid")?.value);
            this.requestDraw();
        });
        toolbar.append(
            presets,
            this.gridButton,
            button("Trace", "Draw lines along the surface's edges to work out the camera and grid", () => this.setTracing(true)),
            button("Reset bottle", "Reset position, size and rotation", () => this.reset(RESETS.bottle)),
            button("Reset camera", "Reset field of view, pitch, orbit and roll", () => this.reset(RESETS.camera)),
        );

        // Shown instead of the toolbar while tracing.
        const traceBar = this.traceBar = row();
        traceBar.style.display = "none";
        this.groupButtons = {
            a: button("", "Draw lines along the first edge direction", () => this.setTraceGroup("a")),
            b: button("", "Draw lines along the direction at right angles to the first", () => this.setTraceGroup("b")),
        };
        const spacer = document.createElement("div");
        spacer.style.flex = "1 1 auto";
        traceBar.append(
            this.groupButtons.a,
            this.groupButtons.b,
            spacer,
            button("Undo", "Remove the last line drawn", () => this.undoTraceLine()),
            button("Clear", "Remove all traced lines", () => this.clearTrace()),
            button("Done", "Leave tracing; the camera settings stay", () => this.setTracing(false)),
        );

        this.stage = document.createElement("div");
        this.stage.style.cssText = "flex:1 1 auto;min-height:0;display:flex;align-items:center;"
            + "justify-content:center;background:#141414;border-radius:4px;overflow:hidden;";
        this.canvas = document.createElement("canvas");
        this.canvas.style.cssText = "display:block;touch-action:none;outline:none;";
        // The wheel resizes the bottle only while the viewport has focus, so
        // scrolling past the node still zooms the graph. The Vue node renderer
        // applies the same rule to elements marked data-capture-wheel.
        this.canvas.tabIndex = 0;
        this.canvas.dataset.captureWheel = "true";
        this.stage.append(this.canvas);

        const hint = document.createElement("div");
        hint.style.cssText = `flex:0 0 ${HINT_HEIGHT - 4}px;font:10px/1.3 sans-serif;opacity:0.7;`
            + "color:var(--input-text,#ddd);overflow:hidden;";
        hint.textContent = HINT;
        this.hint = hint;

        this.root.append(toolbar, traceBar, this.stage, hint);

        const canvas = this.canvas;
        canvas.addEventListener("pointerdown", (event) => this.onPointerDown(event));
        canvas.addEventListener("pointermove", (event) => this.onPointerMove(event));
        canvas.addEventListener("pointerup", (event) => this.onPointerUp(event));
        canvas.addEventListener("pointercancel", (event) => this.onPointerUp(event));
        canvas.addEventListener("pointerenter", () => { this.pointerInside = true; this.requestDraw(); });
        canvas.addEventListener("pointerleave", () => {
            this.pointerInside = false;
            this.hover = null;
            this.requestDraw();
        });
        canvas.addEventListener("wheel", (event) => this.onWheel(event), { passive: false });
        const showFocus = (focused) => {
            this.stage.style.boxShadow = focused ? "inset 0 0 0 1px var(--p-primary-color,#6aa9ff)" : "none";
        };
        canvas.addEventListener("keydown", (event) => {
            if (event.key !== "Escape" || !this.trace?.pending) return;
            event.preventDefault();
            event.stopPropagation();
            this.trace.pending = null;
            this.solveTrace();
        });
        canvas.addEventListener("focus", () => showFocus(true));
        canvas.addEventListener("blur", () => showFocus(false));
    }

    // Height the viewport widget asks the node for, from the current width.
    preferredHeight() {
        const width = Math.max(120, (this.node.size?.[0] ?? 440) - 20);
        const stage = Math.min(MAX_STAGE, Math.max(MIN_STAGE, width / this.aspect));
        return Math.ceil(stage + TOOLBAR_HEIGHT + HINT_HEIGHT + 4);
    }

    // Snap the node height to the viewport once the image aspect is known.
    fitNode() {
        const node = this.node;
        if (!node.graph || !node.computeSize || !node.setSize) return;
        const minimum = node.computeSize();
        node.setSize([Math.max(node.size[0], minimum[0]), minimum[1]]);
        node.setDirtyCanvas?.(true, true);
    }

    dispose() {
        clearInterval(this.poll);
        cancelAnimationFrame(this.frame);
        this.observer.disconnect();
        this.background = null;
    }

    // ---- state ----------------------------------------------------------------

    params() {
        const raw = {};
        for (const name of GEOMETRY_WIDGETS) raw[name] = getWidget(this.node, name)?.value;
        return normalizeParams(raw);
    }

    imageSize() {
        if (this.background) return [this.background.naturalWidth, this.background.naturalHeight];
        return [Math.round(1024 * this.aspect), 1024];
    }

    scene() {
        const [width, height] = this.imageSize();
        const p = this.params();
        const cam = makeCamera(p, width, height);
        return { p, cam, width, height };
    }

    checkForChanges() {
        if (!this.node.graph) return;
        this.refreshBackground();
        const signature = WATCHED_WIDGETS.map((name) => getWidget(this.node, name)?.value).join("|");
        if (signature !== this.signature) this.requestDraw();
    }

    applyPreset(preset) {
        setWidget(this.node, "shape", preset.shape);
        ["width", "height", "depth"].forEach((axis, index) => {
            setWidget(this.node, `body_${axis}`, preset.body[index]);
            setWidget(this.node, `neck_${axis}`, preset.neck[index]);
        });
        this.requestDraw();
    }

    reset(names) {
        for (const name of names) setWidget(this.node, name, DEFAULTS[name]);
        this.requestDraw();
    }

    // ---- perspective tracing ------------------------------------------------

    // Traced lines, as [x1, y1, x2, y2] in 0..1 of the image, per direction.
    traceLines() {
        const stored = this.node.properties?.[TRACE_KEY];
        const clean = (lines) => (Array.isArray(lines) ? lines : [])
            .filter((line) => Array.isArray(line) && line.length === 4 && line.every(Number.isFinite));
        return { a: clean(stored?.a), b: clean(stored?.b), order: Array.isArray(stored?.order) ? stored.order : [] };
    }

    storeTrace(lines) {
        this.node.properties ??= {};
        this.node.properties[TRACE_KEY] = lines;
    }

    setTracing(on) {
        if (on) {
            const lines = this.traceLines();
            this.trace = { group: lines.a.length >= 2 && lines.b.length < 2 ? "b" : "a", pending: null, message: "" };
            this.solveTrace();
        } else {
            this.trace = null;
            this.node.graph?.afterChange?.();
        }
        this.toolbar.style.display = on ? "none" : "flex";
        this.traceBar.style.display = on ? "flex" : "none";
        this.hint.textContent = on ? TRACE_HINT : HINT;
        this.drag = null;
        this.hover = null;
        this.updateTraceBar();
        this.updateCursor();
        this.requestDraw();
    }

    setTraceGroup(group) {
        if (!this.trace) return;
        this.trace.group = group;
        this.trace.pending = null;
        this.updateTraceBar();
        this.requestDraw();
    }

    updateTraceBar() {
        const lines = this.traceLines();
        for (const [group, label] of [["a", "Red"], ["b", "Blue"]]) {
            const element = this.groupButtons[group];
            const active = this.trace?.group === group;
            element.textContent = `${label} lines (${lines[group].length})`;
            element.style.borderColor = active ? TRACE_COLOURS[group] : "var(--border-color,#4e4e4e)";
            element.style.color = TRACE_COLOURS[group];
            element.style.fontWeight = active ? "bold" : "normal";
        }
    }

    addTraceLine(group, line) {
        const lines = this.traceLines();
        lines[group].push(line);
        lines.order.push(group);
        this.storeTrace(lines);
        // Two lines settle a direction, so move on to the other one.
        const other = group === "a" ? "b" : "a";
        if (lines[group].length >= 2 && lines[other].length < 2) this.trace.group = other;
    }

    undoTraceLine() {
        if (!this.trace) return;
        if (this.trace.pending) {
            this.trace.pending = null;
        } else {
            const lines = this.traceLines();
            const group = lines.order.pop() ?? (lines.b.length ? "b" : "a");
            lines[group].pop();
            this.storeTrace(lines);
        }
        this.solveTrace();
    }

    clearTrace() {
        if (!this.trace) return;
        this.storeTrace({ a: [], b: [], order: [] });
        this.trace.pending = null;
        this.trace.group = "a";
        this.solveTrace();
    }

    // Work the camera out from the lines, including the one being drawn, and
    // write it to the camera widgets.
    solveTrace() {
        if (!this.trace) return;
        const [width, height] = this.imageSize();
        const lines = this.traceLines();
        const pending = this.trace.pending;
        const groups = { a: [...lines.a], b: [...lines.b] };
        if (pending?.end) groups[pending.group].push([...pending.start, ...pending.end]);
        const pixels = (group) => group.map(([x1, y1, x2, y2]) => [x1 * width, y1 * height, x2 * width, y2 * height]);
        const result = solvePerspective(pixels(groups.a), pixels(groups.b), width, height, this.params());
        this.trace.message = result.message;
        if (result.values) {
            for (const [name, value] of Object.entries(result.values)) setWidget(this.node, name, value);
        }
        this.updateTraceBar();
        this.requestDraw();
    }

    // Nearest traced line end within `reach` image pixels of a point given in
    // 0..1 image coordinates.
    pickTraceEnd(point, reach) {
        const lines = this.traceLines();
        const [width, height] = this.imageSize();
        let best = null;
        for (const group of ["a", "b"]) {
            lines[group].forEach((line, index) => {
                for (const end of [0, 2]) {
                    const distance = Math.hypot((line[end] - point[0]) * width, (line[end + 1] - point[1]) * height);
                    if (distance <= reach && (!best || distance < best.distance)) best = { group, index, end, distance };
                }
            });
        }
        return best;
    }

    cssToUnit(css) {
        return [css[0] / (this.canvas.clientWidth || 1), css[1] / (this.canvas.clientHeight || 1)];
    }

    traceReach() {
        return HANDLE_REACH * (this.imageSize()[0] / (this.canvas.clientWidth || 1));
    }

    tracePointerDown(event) {
        const css = this.pointerCss(event);
        const point = this.cssToUnit(css);
        const trace = this.trace;
        if (trace.pending?.clicks) {
            // Second click of a click-click line.
            this.finishPending(point, css);
            return;
        }
        const end = this.pickTraceEnd(point, this.traceReach());
        if (end) {
            this.drag = { kind: "trace-end", end };
        } else {
            trace.pending = { group: trace.group, start: point, end: null, css, clicks: false };
            this.drag = { kind: "trace-new" };
        }
        this.canvas.setPointerCapture?.(event.pointerId);
        this.updateCursor();
        this.requestDraw();
    }

    tracePointerMove(event) {
        const css = this.pointerCss(event);
        const point = this.cssToUnit(css);
        const trace = this.trace;
        if (this.drag?.kind === "trace-end") {
            const { group, index, end } = this.drag.end;
            const lines = this.traceLines();
            lines[group][index][end] = point[0];
            lines[group][index][end + 1] = point[1];
            this.storeTrace(lines);
            this.solveTrace();
        } else if (trace.pending) {
            const moved = Math.hypot(css[0] - trace.pending.css[0], css[1] - trace.pending.css[1]);
            trace.pending.end = moved > 6 ? point : null;
            this.solveTrace();
        } else {
            const over = this.pickTraceEnd(point, this.traceReach()) ? "trace-end" : null;
            if (over !== this.hover) {
                this.hover = over;
                this.updateCursor();
            }
        }
    }

    tracePointerUp(event) {
        const css = this.pointerCss(event);
        const trace = this.trace;
        const kind = this.drag?.kind;
        this.drag = null;
        this.canvas.releasePointerCapture?.(event.pointerId);
        if (kind === "trace-new" && trace.pending) {
            if (trace.pending.end) {
                // Press, drag, release drew the whole line.
                this.finishPending(this.cssToUnit(css), css);
            } else {
                // A plain click set the first point; the next click sets the second.
                trace.pending.clicks = true;
            }
        }
        this.updateCursor();
        this.solveTrace();
    }

    finishPending(point, css) {
        const pending = this.trace.pending;
        if (Math.hypot(css[0] - pending.css[0], css[1] - pending.css[1]) <= 6) return;
        this.trace.pending = null;
        this.addTraceLine(pending.group, [...pending.start, ...point]);
        this.solveTrace();
    }

    drawTrace(ctx, width, height, ui) {
        const lines = this.traceLines();
        const pending = this.trace.pending;
        const all = [
            ...lines.a.map((line) => ["a", line]),
            ...lines.b.map((line) => ["b", line]),
            ...(pending?.end ? [[pending.group, [...pending.start, ...pending.end]]] : []),
        ];
        const reach = 4 * (width + height);
        for (const [group, [x1, y1, x2, y2]] of all) {
            const a = [x1 * width, y1 * height], b = [x2 * width, y2 * height];
            const length = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
            const step = [((b[0] - a[0]) / length) * reach, ((b[1] - a[1]) / length) * reach];
            // A thin extension shows where the lines of a direction converge.
            ctx.strokeStyle = TRACE_COLOURS[group];
            ctx.globalAlpha = 0.45;
            ctx.lineWidth = 1 * ui;
            ctx.setLineDash([6 * ui, 5 * ui]);
            ctx.beginPath();
            ctx.moveTo(a[0] - step[0], a[1] - step[1]);
            ctx.lineTo(b[0] + step[0], b[1] + step[1]);
            ctx.stroke();
            ctx.setLineDash([]);
            ctx.globalAlpha = 1;
            for (const [colour, lineWidth] of [["#000", 4.5 * ui], [TRACE_COLOURS[group], 2.5 * ui]]) {
                ctx.strokeStyle = colour;
                ctx.lineWidth = lineWidth;
                ctx.beginPath();
                ctx.moveTo(a[0], a[1]);
                ctx.lineTo(b[0], b[1]);
                ctx.stroke();
            }
            for (const end of [a, b]) {
                ctx.beginPath();
                ctx.arc(end[0], end[1], 5 * ui, 0, Math.PI * 2);
                ctx.fillStyle = "#fff";
                ctx.fill();
                ctx.lineWidth = 1.5 * ui;
                ctx.strokeStyle = TRACE_COLOURS[group];
                ctx.stroke();
            }
        }
        if (pending && !pending.end) {
            ctx.beginPath();
            ctx.arc(pending.start[0] * width, pending.start[1] * height, 5 * ui, 0, Math.PI * 2);
            ctx.fillStyle = TRACE_COLOURS[pending.group];
            ctx.fill();
        }
        this.drawNote(ctx, this.trace.message, ui);
    }

    drawNote(ctx, text, ui) {
        if (!text) return;
        ctx.font = `${11 * ui}px sans-serif`;
        ctx.textAlign = "left";
        ctx.textBaseline = "middle";
        const boxWidth = ctx.measureText(text).width + 12 * ui;
        ctx.fillStyle = "rgba(0,0,0,0.7)";
        ctx.fillRect(6 * ui, 6 * ui, boxWidth, 20 * ui);
        ctx.fillStyle = "#fff";
        ctx.fillText(text, 12 * ui, 16 * ui);
    }

    // ---- background -----------------------------------------------------------

    // A directly connected Load Image is read live; anything else shows the image
    // the node last received when the workflow ran.
    backgroundDescriptor() {
        const index = this.node.inputs?.findIndex((input) => input.name === "image") ?? -1;
        const upstream = index >= 0 ? this.node.getInputNode?.(index) : null;
        const value = upstream?.widgets?.find((widget) => widget.name === "image")?.value;
        if (typeof value === "string" && value) return describeImageValue(value);
        if (!this.node.isInputConnected?.(index)) return null;
        return this.node.properties?.[BACKGROUND_KEY] ?? null;
    }

    refreshBackground() {
        const descriptor = this.backgroundDescriptor();
        const source = descriptor ? `${viewUrl(descriptor)}&v=${descriptor.version ?? 0}` : "";
        if (source === this.backgroundSource) return;
        this.backgroundSource = source;
        if (!source) {
            this.background = null;
            this.requestDraw();
            return;
        }
        const image = new Image();
        image.onload = () => {
            if (this.backgroundSource !== source) return;
            this.background = image;
            const aspect = image.naturalWidth / image.naturalHeight;
            if (Math.abs(aspect - this.aspect) > 1e-3) {
                this.aspect = aspect;
                this.fitNode();
            }
            this.requestDraw();
        };
        image.onerror = () => {
            if (this.backgroundSource !== source) return;
            // Temp previews do not survive a ComfyUI restart.
            this.background = null;
            this.requestDraw();
        };
        image.src = source;
    }

    onExecuted(message) {
        const descriptor = message?.[BACKGROUND_KEY]?.[0];
        if (!descriptor) return;
        this.node.properties ??= {};
        this.node.properties[BACKGROUND_KEY] = { ...descriptor, version: Date.now() };
        this.refreshBackground();
    }

    // ---- drawing --------------------------------------------------------------

    requestDraw() {
        if (this.frame) return;
        this.frame = requestAnimationFrame(() => {
            this.frame = 0;
            this.draw();
        });
    }

    draw() {
        const node = this.node;
        if (!node.graph) return;
        this.signature = WATCHED_WIDGETS.map((name) => getWidget(node, name)?.value).join("|");
        const showGrid = Boolean(getWidget(node, "show_grid")?.value) || Boolean(this.trace);
        this.gridButton.style.borderColor = showGrid ? "var(--p-primary-color,#6aa9ff)" : "var(--border-color,#4e4e4e)";

        // The Vue node renderer sizes the widget from its content, not from getMinHeight.
        this.root.style.minHeight = `${this.preferredHeight()}px`;
        const stageWidth = this.stage.clientWidth, stageHeight = this.stage.clientHeight;
        if (stageWidth < 2 || stageHeight < 2) return;
        const { p, cam, width, height } = this.scene();
        const cssWidth = Math.max(1, Math.min(stageWidth, stageHeight * (width / height)));
        const cssHeight = cssWidth * (height / width);
        const canvas = this.canvas;
        canvas.style.width = `${cssWidth}px`;
        canvas.style.height = `${cssHeight}px`;

        // Size the backing store from the on-screen size so it stays sharp when
        // the graph is zoomed.
        const onScreen = canvas.getBoundingClientRect().width || cssWidth;
        const backingWidth = Math.round(Math.min(2048, Math.max(64, onScreen * (window.devicePixelRatio || 1))));
        const backingHeight = Math.max(1, Math.round(backingWidth * (height / width)));
        for (const target of [canvas, this.faces, this.edges]) {
            if (target.width !== backingWidth || target.height !== backingHeight) {
                target.width = backingWidth;
                target.height = backingHeight;
            }
        }
        const toBacking = backingWidth / width;   // image px -> backing px
        const ui = backingWidth / cssWidth;       // CSS px -> backing px

        const ctx = canvas.getContext("2d");
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.clearRect(0, 0, backingWidth, backingHeight);
        if (this.background) {
            ctx.drawImage(this.background, 0, 0, backingWidth, backingHeight);
        } else {
            this.drawEmptyBackground(ctx, backingWidth, backingHeight, ui);
        }

        ctx.setTransform(toBacking, 0, 0, toBacking, 0, 0);
        ctx.lineCap = "butt";
        if (showGrid) {
            for (const line of buildGrid(p, cam)) {
                ctx.strokeStyle = rgb(line.rgba, line.rgba[3]);
                ctx.lineWidth = Math.max(line.width, 1 / toBacking);
                ctx.beginPath();
                ctx.moveTo(line.a[0], line.a[1]);
                ctx.lineTo(line.b[0], line.b[1]);
                ctx.stroke();
            }
        }

        // Two layers, as in the Python renderer. Faces: fills only, blended once
        // at fill_opacity so overlapping faces do not show through each other.
        // Edges: strokes, with each face erasing the lines behind it; always solid.
        const bottle = buildBottle(p, cam);
        const [faces, edges] = [this.faces, this.edges].map((target) => {
            const layer = target.getContext("2d");
            layer.setTransform(1, 0, 0, 1, 0, 0);
            layer.clearRect(0, 0, backingWidth, backingHeight);
            layer.setTransform(toBacking, 0, 0, toBacking, 0, 0);
            layer.lineJoin = "round";
            return layer;
        });
        const path = (layer, points) => {
            layer.beginPath();
            points.forEach((point, index) => (index ? layer.lineTo(point[0], point[1]) : layer.moveTo(point[0], point[1])));
            layer.closePath();
        };
        for (const op of bottle) {
            if (op.fill) {
                for (const layer of [faces, edges]) {
                    path(layer, op.points);
                    layer.globalCompositeOperation = layer === edges ? "destination-out" : "source-over";
                    layer.fillStyle = layer.strokeStyle = rgb(op.fill);
                    layer.lineWidth = 1 / toBacking;
                    layer.fill();
                    layer.stroke();
                }
                edges.globalCompositeOperation = "source-over";
            }
            if (op.stroke) {
                path(edges, op.points);
                edges.strokeStyle = rgb(op.stroke);
                edges.lineWidth = Math.max(op.width, 1 / toBacking);
                edges.stroke();
            }
        }
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        if (p.style === "shaded") {
            ctx.globalAlpha = p.fill_opacity;
            ctx.drawImage(this.faces, 0, 0);
            ctx.globalAlpha = 1;
        }
        ctx.drawImage(this.edges, 0, 0);

        this.bottlePolygons = bottle.filter((op) => op.fill).map((op) => op.points);
        if (this.trace) this.drawTrace(ctx, backingWidth, backingHeight, ui);
        else if (this.pointerInside || this.drag) this.drawControls(ctx, p, cam, toBacking, ui);
    }

    drawEmptyBackground(ctx, width, height, ui) {
        const cell = 16 * ui;
        for (let y = 0; y * cell < height; y++) {
            for (let x = 0; x * cell < width; x++) {
                ctx.fillStyle = (x + y) % 2 ? "#2a2a2a" : "#333";
                ctx.fillRect(x * cell, y * cell, cell, cell);
            }
        }
        const index = this.node.inputs?.findIndex((input) => input.name === "image") ?? -1;
        const connected = Boolean(this.node.isInputConnected?.(index));
        ctx.fillStyle = "rgba(255,255,255,0.75)";
        ctx.font = `${12 * ui}px sans-serif`;
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        ctx.fillText(connected ? "Run the workflow once to show the image here" : "Connect an image", width / 2, 10 * ui);
    }

    drawControls(ctx, p, cam, toBacking, ui) {
        const at = (point) => [point[0] * toBacking, point[1] * toBacking];
        const active = this.drag?.target ?? this.hover;

        const ring = buildRotateRing(p, cam);
        ctx.lineWidth = 1.5 * ui;
        ctx.setLineDash([4 * ui, 4 * ui]);
        ctx.strokeStyle = "rgba(255,255,255,0.85)";
        ctx.beginPath();
        let pen = false;
        for (const point of ring.points) {
            if (!point) { pen = false; continue; }
            const [x, y] = at(point);
            if (pen) ctx.lineTo(x, y); else ctx.moveTo(x, y);
            pen = true;
        }
        ctx.stroke();
        ctx.setLineDash([]);

        const dot = (point, highlighted, colour) => {
            const [x, y] = at(point);
            ctx.beginPath();
            ctx.arc(x, y, (highlighted ? HANDLE_RADIUS + 2 : HANDLE_RADIUS) * ui, 0, Math.PI * 2);
            ctx.fillStyle = highlighted ? "#ffd24a" : colour;
            ctx.fill();
            ctx.lineWidth = 1.5 * ui;
            ctx.strokeStyle = "#111";
            ctx.stroke();
        };
        if (ring.knob) dot(ring.knob, active === "rotate_y", "#7fd4ff");
        for (const handle of buildHandles(p, cam)) dot(handle.position, active === handle.param, "#ffffff");

        const text = active === "rotate_y" ? `Turn ${p.rotate_y.toFixed(1)}°`
            : LABELS[active] ? `${LABELS[active]} ${p[active].toFixed(1)}`
            : this.drag?.kind === "move" ? `Position ${p.pos_x.toFixed(3)}, ${p.pos_y.toFixed(3)}`
            : this.drag?.kind === "orbit" ? `Pitch ${p.camera_pitch.toFixed(1)}° · Orbit ${p.camera_orbit.toFixed(1)}°`
            : this.drag?.kind === "tilt" ? `Tip ${p.rotate_x.toFixed(1)}° · Lean ${p.rotate_z.toFixed(1)}°`
            : "";
        this.drawNote(ctx, text, ui);
    }

    // ---- pointer --------------------------------------------------------------

    // Pointer position in CSS pixels of the canvas, whatever the graph zoom.
    pointerCss(event) {
        const rect = this.canvas.getBoundingClientRect();
        const cssWidth = this.canvas.clientWidth || 1, cssHeight = this.canvas.clientHeight || 1;
        return [
            ((event.clientX - rect.left) / (rect.width || 1)) * cssWidth,
            ((event.clientY - rect.top) / (rect.height || 1)) * cssHeight,
        ];
    }

    // What sits under the pointer: a handle's parameter name, "bottle" or null.
    pick(css, scene) {
        const perCss = scene.width / (this.canvas.clientWidth || 1);   // image px per CSS px
        const point = [css[0] * perCss, css[1] * perCss];
        const reach = HANDLE_REACH * perCss;
        const near = (target) => target && Math.hypot(target[0] - point[0], target[1] - point[1]) <= reach;
        for (const handle of buildHandles(scene.p, scene.cam)) {
            if (near(handle.position)) return { target: handle.param, handle, point };
        }
        if (near(buildRotateRing(scene.p, scene.cam).knob)) return { target: "rotate_y", point };
        const onBottle = (this.bottlePolygons ?? []).some((polygon) => pointInPolygon(point, polygon));
        return { target: onBottle ? "bottle" : null, point };
    }

    onPointerDown(event) {
        if (event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        this.canvas.focus({ preventScroll: true });
        if (this.trace) {
            this.tracePointerDown(event);
            return;
        }
        const scene = this.scene();
        const css = this.pointerCss(event);
        const picked = this.pick(css, scene);
        const value = (name) => scene.p[name];
        const drag = { css, point: picked.point, target: picked.target, scene };

        if (picked.handle) {
            Object.assign(drag, { kind: "dimension", handle: picked.handle, start: value(picked.target) });
        } else if (picked.target === "rotate_y") {
            const ground = groundPoint(scene.cam, picked.point[0], picked.point[1]);
            Object.assign(drag, {
                kind: "turn", start: value("rotate_y"),
                startAngle: ground ? Math.atan2(ground[0], ground[1]) : null,
            });
        } else if (event.altKey) {
            Object.assign(drag, { kind: "tilt", start: [value("rotate_x"), value("rotate_z")] });
        } else if (picked.target === "bottle" || event.shiftKey) {
            Object.assign(drag, { kind: "move", target: "bottle", start: [value("pos_x"), value("pos_y")] });
        } else {
            Object.assign(drag, { kind: "orbit", start: [value("camera_orbit"), value("camera_pitch")] });
        }
        this.drag = drag;
        this.canvas.setPointerCapture?.(event.pointerId);
        this.updateCursor();
        this.requestDraw();
    }

    onPointerMove(event) {
        if (this.trace) {
            this.tracePointerMove(event);
            return;
        }
        const css = this.pointerCss(event);
        const drag = this.drag;
        if (!drag) {
            const target = this.pick(css, this.scene()).target;
            if (target !== this.hover) {
                this.hover = target;
                this.updateCursor();
                this.requestDraw();
            }
            return;
        }
        event.preventDefault();
        event.stopPropagation();
        const node = this.node, { scene } = drag;
        const dx = css[0] - drag.css[0], dy = css[1] - drag.css[1];
        const perCss = scene.width / (this.canvas.clientWidth || 1);

        if (drag.kind === "dimension") {
            const [ax, ay] = drag.handle.axis;
            // An axis pointing at the camera barely moves on screen; cap how
            // sensitive that can make the handle.
            const length2 = Math.max(ax * ax + ay * ay, (0.25 * scene.cam.ppu) ** 2);
            const units = ((dx * ax + dy * ay) * perCss) / length2;
            setWidget(node, drag.target, drag.start + drag.handle.factor * units);
        } else if (drag.kind === "turn") {
            const ground = groundPoint(scene.cam, css[0] * perCss, css[1] * perCss);
            if (ground && drag.startAngle !== null) {
                const delta = (Math.atan2(ground[0], ground[1]) - drag.startAngle) * (180 / Math.PI);
                setWidget(node, "rotate_y", wrapDegrees(drag.start + delta));
            }
        } else if (drag.kind === "move") {
            setWidget(node, "pos_x", drag.start[0] + (dx * perCss) / scene.width);
            setWidget(node, "pos_y", drag.start[1] + (dy * perCss) / scene.height);
        } else if (drag.kind === "tilt") {
            setWidget(node, "rotate_x", wrapDegrees(drag.start[0] + dy * 0.5));
            setWidget(node, "rotate_z", wrapDegrees(drag.start[1] - dx * 0.5));
        } else {
            setWidget(node, "camera_orbit", wrapDegrees(drag.start[0] + dx * 0.4));
            setWidget(node, "camera_pitch", drag.start[1] + dy * 0.3);
        }
        this.requestDraw();
    }

    onPointerUp(event) {
        if (this.trace) {
            this.tracePointerUp(event);
            return;
        }
        if (!this.drag) return;
        this.drag = null;
        this.canvas.releasePointerCapture?.(event.pointerId);
        this.hover = this.pick(this.pointerCss(event), this.scene()).target;
        this.updateCursor();
        this.requestDraw();
        this.node.graph?.afterChange?.();
    }

    onWheel(event) {
        event.preventDefault();
        event.stopPropagation();
        if (document.activeElement !== this.canvas) {
            // Not ours: hand it to the graph, as if the node were not in the way.
            app.canvas?.canvas?.dispatchEvent(new WheelEvent(event.type, event));
            return;
        }
        const delta = event.deltaY || event.deltaX;
        if (!delta) return;
        const p = this.params();
        if (event.shiftKey) {
            setWidget(this.node, "rotate_y", wrapDegrees(p.rotate_y + (delta > 0 ? 5 : -5)));
        } else {
            const clamped = Math.max(-120, Math.min(120, delta));
            setWidget(this.node, "scale", p.scale * Math.exp(-clamped * 0.0012));
        }
        this.requestDraw();
    }

    updateCursor() {
        if (this.trace) {
            this.canvas.style.cursor = this.hover === "trace-end" || this.drag?.kind === "trace-end" ? "pointer" : "crosshair";
            return;
        }
        const target = this.drag?.target ?? this.hover;
        this.canvas.style.cursor = this.drag
            ? "grabbing"
            : target === "bottle" ? "move" : target ? "pointer" : "grab";
    }
}

function attachEditor(node) {
    if (node._wnBottleEditor) return node._wnBottleEditor;
    const editor = new BottleEditor(node);
    node._wnBottleEditor = editor;

    const widget = node.addDOMWidget("viewport", "wn_bottle_viewport", editor.root, {
        serialize: false,
        hideOnZoom: false,
        getMinHeight: () => editor.preferredHeight(),
    });
    widget.serialize = false;

    for (const name of WATCHED_WIDGETS) {
        const target = getWidget(node, name);
        if (!target) continue;
        const original = target.callback;
        target.callback = function (...args) {
            const result = original?.apply(this, args);
            editor.requestDraw();
            return result;
        };
    }

    if (node.size[0] < 440) node.size[0] = 440;
    requestAnimationFrame(() => {
        editor.refreshBackground();
        editor.fitNode();
        editor.requestDraw();
    });
    return editor;
}

app.registerExtension({
    name: "WepeNerd.BottlePlaceholder",
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function (...args) {
            const result = onNodeCreated?.apply(this, args);
            attachEditor(this);
            return result;
        };

        const onConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function (...args) {
            const result = onConfigure?.apply(this, args);
            const editor = this._wnBottleEditor;
            if (editor) {
                editor.refreshBackground();
                editor.requestDraw();
            }
            return result;
        };

        const onConnectionsChange = nodeType.prototype.onConnectionsChange;
        nodeType.prototype.onConnectionsChange = function (...args) {
            const result = onConnectionsChange?.apply(this, args);
            this._wnBottleEditor?.refreshBackground();
            return result;
        };

        const onExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message, ...rest) {
            const result = onExecuted?.call(this, message, ...rest);
            this._wnBottleEditor?.onExecuted(message);
            return result;
        };

        const onRemoved = nodeType.prototype.onRemoved;
        nodeType.prototype.onRemoved = function (...args) {
            this._wnBottleEditor?.dispose();
            this._wnBottleEditor = null;
            return onRemoved?.apply(this, args);
        };
    },
});
