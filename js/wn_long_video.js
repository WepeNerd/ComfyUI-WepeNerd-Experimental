import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const style = document.createElement("style");
style.textContent = `
.wn-long-video{display:flex;flex-direction:column;gap:7px;height:100%;padding:6px;box-sizing:border-box;font:12px Arial,sans-serif;color:var(--input-text,#ddd)}
.wn-long-video *{box-sizing:border-box}.wn-long-video .row{display:flex;align-items:center;gap:5px;flex-wrap:wrap}
.wn-long-video button{font:inherit;color:inherit;background:var(--comfy-input-bg,#282a30);border:1px solid var(--border-color,#555);border-radius:4px;padding:5px 8px;cursor:pointer}
.wn-long-video button:hover{border-color:#73c9bb}.wn-long-video button:disabled{opacity:.4;cursor:default}
.wn-long-video .stage{position:relative;flex:1;min-height:180px;background:#101216;border-radius:5px;overflow:hidden}
.wn-long-video video,.wn-long-video img{position:absolute;width:100%;height:100%;object-fit:contain}
.wn-long-video [hidden]{display:none!important}.wn-long-video .clock{position:absolute;bottom:7px;right:7px;padding:3px 6px;background:#000b;color:#fff;font:12px monospace;pointer-events:none}
.wn-long-video canvas{width:100%;height:72px;flex-shrink:0;background:#191d24;border:1px solid #3b424f;border-radius:4px;touch-action:none;cursor:crosshair}
.wn-long-video input[type=range]{width:100%;margin:0;accent-color:#73c9bb}.wn-long-video .info,.wn-long-video .hint{font-size:11px;line-height:15px;color:#aeb8c8}
.wn-long-video .strip{height:44px;cursor:pointer}.wn-long-video .note{color:#ffd17d}
.wn-long-video .status{min-height:15px;font-size:11px;line-height:15px}.wn-long-video .error{color:#ffaaa0}.wn-long-video .spacer{flex:1}
`;
document.head.append(style);

const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
function timecode(seconds) {
    const ms = Math.round(Math.max(0, seconds) * 1000);
    return `${String(Math.floor(ms / 3600000)).padStart(2, "0")}:${String(Math.floor(ms / 60000) % 60).padStart(2, "0")}:${String(Math.floor(ms / 1000) % 60).padStart(2, "0")}.${String(ms % 1000).padStart(3, "0")}`;
}
function el(tag, text = "", className = "") {
    const item = document.createElement(tag); item.textContent = text; item.className = className; return item;
}

function setup(node) {
    const file = node.widgets.find(w => w.name === "video_file");
    const start = node.widgets.find(w => w.name === "start_seconds");
    const end = node.widgets.find(w => w.name === "end_seconds");
    const rateWidget = node.widgets.find(w => w.name === "frame_rate");
    const formatWidget = node.widgets.find(w => w.name === "format");
    let presets = {}, thumbs = null, thumbsKey = "", proxy = null, playOffset = 0, loadQueued = false;
    let metadata = null, source = "", version = 0, removed = false;
    let cursor = 0, viewStart = 0, viewEnd = 1, drag = null;
    let frameBusy = false, pendingFrame = null, timer = null, objectURL = null, playbackFrame = null;
    let loadAbort = null, movieFailed = false;
    const abort = new AbortController();
    const root = el("div", "", "wn-long-video"), stage = el("div", "", "stage");
    const image = el("img"), movie = el("video"), clock = el("div", "00:00:00.000", "clock");
    image.alt = "Source frame at the playhead"; image.hidden = true;
    movie.preload = "metadata"; movie.playsInline = true; movie.hidden = true;
    stage.append(image, movie, clock);
    const sourceTools = el("div", "", "row"), transport = el("div", "", "row");
    const zoomTools = el("div", "", "row");
    const info = el("div", "Choose a video from ComfyUI/input or a subfolder.", "info");
    const status = el("div", "", "status"); status.setAttribute("role", "status");
    const canvas = el("canvas"), ctx = canvas.getContext("2d"); canvas.tabIndex = 0;
    const strip = el("canvas", "", "strip"), stripCtx = strip.getContext("2d");
    strip.setAttribute("aria-label", "Thumbnails of the full source. Click or drag to scrub.");
    const formatTools = el("div", "", "row");
    canvas.setAttribute("aria-label", "Segment timeline. Drag in/out handles or the selected band. Click to scrub. Arrow keys step the playhead; I and O set in and out.");
    const overview = el("input"); overview.type = "range"; overview.min = 0; overview.max = 1; overview.step = "any"; overview.value = 0;
    overview.setAttribute("aria-label", "Position in the full source video");
    const controls = [];
    function button(text, row, action, title = text, needsSource = true) {
        const b = el("button", text); b.type = "button"; b.title = title; b.onclick = action; row.append(b);
        if (needsSource) controls.push(b);
        return b;
    }
    const refresh = button("Refresh files", sourceTools, refreshFiles, "Find videos copied into ComfyUI/input", false);
    button("Reload preview", sourceTools, load, "Read source metadata and refresh the preview", false);
    sourceTools.append(el("span", "", "spacer"), el("span", "Only the in/out range is imported", "hint"));
    const play = button("▶ Play selection", transport, togglePlayback);
    button("−1 frame", transport, () => seek(cursor - frameStep()));
    button("+1 frame", transport, () => seek(cursor + frameStep()));
    button("Set in", transport, markIn, "Set in point at playhead · I");
    button("Set out", transport, markOut, "Set out point at playhead · O");
    button("Go to in", zoomTools, () => seek(Number(start.value)));
    button("Go to out", zoomTools, () => seek(Math.max(Number(start.value), Number(end.value) - frameStep())));
    button("Fit selection", zoomTools, fitSelection);
    button("Full source", zoomTools, () => { viewStart = 0; viewEnd = metadata.duration; draw(); });
    button("+", zoomTools, () => zoom(.5, cursor), "Zoom in around playhead");
    button("−", zoomTools, () => zoom(2, cursor), "Zoom out around playhead");
    button("Out: fewer", formatTools, () => stepFormat(-1), "Move the out point to the next shorter frame count the format allows");
    button("Snap out to format", formatTools, () => stepFormat(0), "Move the out point so the selection is exactly the trimmed frame count");
    button("Out: more", formatTools, () => stepFormat(1), "Move the out point to the next longer frame count the format allows");
    root.append(sourceTools, stage, transport, info, canvas, strip, overview, zoomTools, formatTools, status,
        el("div", "Drag handles = trim · Drag band = move segment · Click = scrub · Wheel = zoom · Thumbnails / slider = full source", "hint"));
    // Fixed, not measured: rows measured before layout wrap word by word, and nothing here may depend on
    // node.size (deriving the height from it made the node grow every frame). The extra row is the format tools.
    const minHeight = () => formatTools.hidden ? 580 : 620;
    node.addDOMWidget("long_video_preview", "wn_long_video", root, { serialize: false, hideOnZoom: false, getMinHeight: minHeight });
    // No computeSize override: deriving the widget's height from node.size made the node grow every frame.
    // The frontend gives the panel at least its minimum height and any extra room when the node is resized.
    requestAnimationFrame(() => node.setSize([Math.max(560, node.size[0]), Math.max(node.size[1], node.computeSize()[1])]));

    function message(text, error = false) { status.textContent = text; status.classList.toggle("error", error); }
    function fps() { return metadata?.fps || 24; }
    function frameStep() { return 1 / fps(); }
    // Times are stored as frame starts (k / fps) rounded to ms; frameOf recovers k (as the node does).
    function frameOf(time) { return Math.max(0, Math.floor(time * fps() + .5)); }
    // Frame on screen at a time; the small bias absorbs ms rounding of stored frame starts.
    function frameAt(time) { return Math.max(0, Math.floor(time * fps() + .2)); }
    function lastFrame() { return Math.max(0, (metadata?.frames || 1) - 1); }
    function stored(frame) { return Math.round(frame / fps() * 1000) / 1000; }
    function preset() { const name = formatWidget?.value; return name && name !== "None" ? presets[name] || null : null; }
    function largestValid(count, p) {
        if (!p || count <= 0) return Math.max(0, count);
        if (p.rule) { const [step, offset] = p.rule; const valid = count - ((((count - offset) % step) + step) % step); return valid >= 1 ? valid : 0; }
        return Math.max(0, ...p.frames.filter(f => f <= count));
    }
    function plan() {
        // Mirrors plan_segment in long_video_node.py.
        const rate = Number(rateWidget?.value) || 0, p = preset();
        const first = Math.min(frameOf(Number(start.value)), lastFrame());
        const last = Math.min(Math.max(first + 1, frameOf(Number(end.value))), Math.max(first + 1, metadata.frames));
        const sourceFrames = last - first;
        const selected = rate ? Math.max(1, Math.floor(sourceFrames / fps() * rate + .5)) : sourceFrames;
        const count = largestValid(selected, p), outRate = rate || fps();
        return { first, last, sourceFrames, selected, count, rate: outRate, preset: p,
            usedLast: first + (rate ? Math.round(count / rate * fps()) : count) };
    }
    function sourceFramesFor(count) {
        const rate = Number(rateWidget?.value) || 0;
        if (!rate) return count;
        let frames = Math.max(1, Math.round(count / rate * fps()));
        while (Math.floor(frames / fps() * rate + .5) < count) frames++;
        while (frames > 1 && Math.floor((frames - 1) / fps() * rate + .5) >= count) frames--;
        return frames;
    }
    function stepFormat(direction) {
        if (!metadata) return;
        const current = plan(), p = current.preset;
        if (!p) { message("Choose a format first.", true); return; }
        const allowed = p.rule ? null : p.frames;
        let target;
        if (direction === 0) target = current.count || (allowed ? allowed[0] : p.rule[1] + p.rule[0]);
        else if (allowed) target = direction > 0 ? allowed.find(f => f > current.count) : [...allowed].reverse().find(f => f < current.count);
        else target = current.count + direction * p.rule[0];
        if (!target || target < 1) { message("No shorter count allowed by this format.", true); return; }
        const last = current.first + sourceFramesFor(target);
        if (last > metadata.frames) { message(`${target} frames don't fit before the end of the video.`, true); return; }
        setFrames(current.first, last);
    }
    function url(action, extra = {}) {
        return `/wepenerd/long_video/${action}?${new URLSearchParams({ file: source, v: metadata?.version || "", ...extra })}`;
    }
    async function response(action, extra = {}, signal = abort.signal) {
        const result = await api.fetchApi(url(action, extra), { signal });
        if (!result.ok) {
            const data = await result.json(); throw new Error(data.error || `Preview request failed (${result.status}).`);
        }
        return result;
    }
    function changed() { node.graph?.setDirtyCanvas(true, true); }
    function markIn() {
        const a = frameOf(Number(start.value)), b = frameOf(Number(end.value)), k = frameAt(cursor);
        setFrames(k, k >= b ? k + Math.max(1, b - a) : b);
    }
    function markOut() {
        // The out point is exclusive: "Set out" keeps the frame under the playhead.
        const a = frameOf(Number(start.value)), b = frameOf(Number(end.value)), k = frameAt(cursor) + 1;
        setFrames(k <= a ? Math.max(0, k - Math.max(1, b - a)) : a, k);
    }
    function setFrames(a, b) {
        if (!metadata) return;
        a = clamp(Math.round(a), 0, lastFrame());
        b = clamp(Math.round(b), a + 1, Math.max(a + 1, metadata.frames));
        start.value = stored(a); end.value = stored(b);
        stop(); changed(); draw();
    }
    function setRange(a, b) {
        if (!metadata) return;
        if (b <= a) { message("Set out later than in.", true); return; }
        setFrames(frameOf(a), frameOf(b));
    }
    function panTo(time) {
        const span = viewEnd - viewStart;
        viewStart = clamp(time - span / 2, 0, Math.max(0, metadata.duration - span));
        viewEnd = viewStart + span;
    }
    function seek(time) {
        if (!metadata) return;
        stop(); cursor = clamp(frameAt(time), 0, lastFrame()) / fps();
        if (cursor < viewStart || cursor > viewEnd) panTo(cursor);
        movie.hidden = true; image.hidden = !objectURL;
        draw(); requestFrame();
    }
    function requestFrame() {
        pendingFrame = { time: cursor, version };
        clearTimeout(timer); timer = setTimeout(fetchFrame, 100);
    }
    async function fetchFrame() {
        if (frameBusy || !pendingFrame || removed) return;
        const request = pendingFrame; pendingFrame = null; frameBusy = true;
        try {
            const result = await response("frame", { time: request.time.toFixed(6), fps: String(fps()) });
            const blob = await result.blob();
            if (removed || request.version !== version || Math.abs(cursor - request.time) > .00001 || !movie.paused) return;
            if (objectURL) URL.revokeObjectURL(objectURL);
            objectURL = URL.createObjectURL(blob); image.src = objectURL; image.hidden = false;
            message(movieFailed ? "This codec can't play in the browser; Play selection uses a small proxy instead." : "Ready. Queue the workflow to import the selected range.");
        } catch (error) {
            if (!removed && request.version === version && error.name !== "AbortError") message(error.message, true);
        } finally {
            frameBusy = false;
            if (pendingFrame && !removed) { clearTimeout(timer); timer = setTimeout(fetchFrame, 100); }
        }
    }
    function stop() {
        movie.pause(); if (playbackFrame !== null) cancelAnimationFrame(playbackFrame); playbackFrame = null;
        play.textContent = "▶ Play selection";
    }
    function updatePlayback() {
        if (movie.paused || removed) return;
        cursor = movie.currentTime + playOffset;
        if (cursor >= Math.min(Number(end.value), metadata.duration) || movie.ended) { stop(); seek(Math.max(Number(start.value), Number(end.value) - frameStep())); return; }
        if (cursor < viewStart || cursor > viewEnd) panTo(cursor);
        draw(); playbackFrame = requestAnimationFrame(updatePlayback);
    }
    function attach(src) {
        return new Promise((resolve, reject) => {
            movie.onloadedmetadata = () => movie.videoWidth ? resolve() : reject(new Error("no picture"));
            movie.onerror = () => reject(new Error("unsupported"));
            movie.src = src;
        });
    }
    async function useProxy(a, b, currentVersion) {
        const key = `${a}|${b}`;
        if (proxy?.key === key) return;
        message("Making a small playback proxy of the selection…");
        const data = await (await response("proxy", { start: String(a), end: String(b) })).json();
        if (removed || currentVersion !== version) return;
        await attach(api.apiURL(`/wepenerd/long_video/proxy_file?${new URLSearchParams({ name: data.name })}`));
        proxy = { key }; playOffset = data.offset;
    }
    async function togglePlayback() {
        if (!metadata) return;
        if (!movie.paused) { stop(); return; }
        const a = Number(start.value), b = Math.min(Number(end.value), metadata.duration);
        if (a >= b) { message("Set a valid in/out range before playing.", true); return; }
        if (cursor < a || cursor >= b - frameStep()) cursor = stored(frameOf(a));
        const currentVersion = version;
        pendingFrame = null; clearTimeout(timer);
        try {
            if (!movieFailed && !movie.getAttribute("src")) {
                try { await attach(api.apiURL(url("source"))); playOffset = 0; proxy = null; }
                catch { movieFailed = true; movie.removeAttribute("src"); movie.load(); }
            }
            if (movieFailed) await useProxy(a, b, currentVersion);
            if (removed || currentVersion !== version) return;
            movie.currentTime = Math.max(0, cursor - playOffset); movie.hidden = false; image.hidden = true;
            await movie.play();
            if (removed || currentVersion !== version) { stop(); return; }
            play.textContent = "❚❚ Pause";
            message(proxy ? "Playing a proxy of the selection (the import still uses the original)." : "Playing the selected range from the source.");
            updatePlayback();
        } catch (error) {
            if (removed || currentVersion !== version) return;
            stop(); movie.hidden = true; image.hidden = !objectURL;
            movie.removeAttribute("src"); movie.load(); proxy = null;
            message(error.message === "unsupported" || error.message === "no picture" ? "Browser cannot play this video." : error.message, true);
        }
    }
    movie.onended = stop;
    function fitSelection() {
        const a = clamp(Number(start.value), 0, metadata.duration), b = clamp(Number(end.value), a, metadata.duration);
        const span = Math.max(frameStep() * 4, (b - a) * 1.4);
        viewStart = clamp(a - span * .15, 0, Math.max(0, metadata.duration - span));
        viewEnd = Math.min(metadata.duration, viewStart + span); draw();
    }
    function zoom(factor, center) {
        if (!metadata) return;
        const span = clamp((viewEnd - viewStart) * factor, Math.min(1, metadata.duration), metadata.duration);
        const fraction = clamp((center - viewStart) / (viewEnd - viewStart), 0, 1);
        viewStart = clamp(center - fraction * span, 0, metadata.duration - span); viewEnd = viewStart + span; draw();
    }
    function draw() {
        const width = Math.max(1, canvas.clientWidth), height = 72, dpr = window.devicePixelRatio || 1;
        canvas.width = Math.round(width * dpr); canvas.height = height * dpr; ctx.scale(dpr, dpr);
        ctx.clearRect(0, 0, width, height);
        for (const control of controls) control.disabled = !metadata;
        overview.disabled = !metadata;
        if (!metadata) return;
        const x = time => (time - viewStart) / (viewEnd - viewStart) * width;
        const a = Number(start.value), b = Number(end.value);
        ctx.fillStyle = "#23645c"; ctx.fillRect(x(a), 26, x(b) - x(a), 27);
        ctx.font = "10px monospace";
        for (let i = 0; i <= 4; i++) {
            const px = i * width / 4;
            ctx.strokeStyle = "#596473"; ctx.beginPath(); ctx.moveTo(px, 21); ctx.lineTo(px, 58); ctx.stroke();
            ctx.fillStyle = "#b5c0ce"; ctx.textAlign = i === 0 ? "left" : i === 4 ? "right" : "center";
            ctx.fillText(timecode(viewStart + i * (viewEnd - viewStart) / 4), px, 14);
        }
        for (const [time, label] of [[a, "IN"], [b, "OUT"]]) {
            if (time < viewStart || time > viewEnd) continue;
            const px = clamp(x(time), 4, width - 4);
            ctx.fillStyle = "#8fe3cd"; ctx.fillRect(px - 4, 24, 8, 32);
            ctx.textAlign = time === a ? "left" : "right"; ctx.fillText(label, px, 68);
        }
        ctx.strokeStyle = "#ffd17d"; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(x(cursor), 18); ctx.lineTo(x(cursor), 58); ctx.stroke(); ctx.lineWidth = 1;
        clock.textContent = timecode(cursor); overview.max = metadata.duration; overview.value = cursor;
        const p = plan(), name = formatWidget?.value;
        if (p.preset && p.count < p.selected) {
            const px = x(p.usedLast / fps());
            if (px >= 0 && px <= width) {
                ctx.strokeStyle = "#ffd17d"; ctx.setLineDash([3, 3]); ctx.beginPath(); ctx.moveTo(px, 22); ctx.lineTo(px, 58); ctx.stroke(); ctx.setLineDash([]);
            }
        }
        const gib = p.count * metadata.width * metadata.height * 12 / 2 ** 30;
        const outLine = p.preset
            ? `${p.selected} frames → ${p.count} for ${name}` + (p.count && (p.count < p.preset.min || p.count > p.preset.max) ? ` (outside its ${p.preset.min}-${p.preset.max} range)` : "")
            : `${p.count} frames`;
        info.textContent = `${timecode(a)} → ${timecode(b)} · frames ${p.first}-${p.last - 1} · ${outLine} at ${p.rate.toFixed(3)} fps = ${(p.count / p.rate).toFixed(3)}s · ~${gib.toFixed(2)} GiB as images\n`
            + `Source ${timecode(metadata.duration)} · ${metadata.width}×${metadata.height} · ${metadata.fps.toFixed(3)} fps · ${metadata.frames} frames`;
        info.style.whiteSpace = "pre-line";
        info.classList.toggle("note", Boolean(p.preset && (p.count === 0 || p.count < p.preset.min || p.count > p.preset.max)));
        if (formatTools.hidden !== !p.preset) {
            formatTools.hidden = !p.preset;
            requestAnimationFrame(() => { const needed = node.computeSize()[1]; if (node.size[1] < needed) node.setSize([node.size[0], needed]); changed(); });
        }
        drawStrip();
    }
    function drawStrip() {
        const width = Math.max(1, strip.clientWidth), height = 44, dpr = window.devicePixelRatio || 1;
        strip.width = Math.round(width * dpr); strip.height = height * dpr; stripCtx.scale(dpr, dpr);
        stripCtx.fillStyle = "#101216"; stripCtx.fillRect(0, 0, width, height);
        if (!metadata) return;
        const count = clamp(Math.round(width / (44 * metadata.width / metadata.height)), 4, 48);
        const key = `${source}|${metadata.version}|${count}`;
        if (key !== thumbsKey) {
            thumbsKey = key; thumbs = null;
            const picture = new Image(), wanted = key;
            picture.onload = () => { if (thumbsKey === wanted) { thumbs = picture; drawStrip(); } };
            picture.src = api.apiURL(url("thumbs", { count: String(count) }));
        }
        if (thumbs) stripCtx.drawImage(thumbs, 0, 0, width, height);
        const x = time => time / metadata.duration * width;
        const a = Number(start.value), b = Number(end.value);
        stripCtx.fillStyle = "#000a"; stripCtx.fillRect(0, 0, x(a), height); stripCtx.fillRect(x(b), 0, width - x(b), height);
        stripCtx.strokeStyle = "#8fe3cd"; stripCtx.lineWidth = 2; stripCtx.strokeRect(x(a), 1, Math.max(2, x(b) - x(a)), height - 2);
        stripCtx.strokeStyle = "#ffffff66"; stripCtx.lineWidth = 1; stripCtx.strokeRect(x(viewStart) + .5, .5, Math.max(1, x(viewEnd) - x(viewStart) - 1), height - 1);
        stripCtx.strokeStyle = "#ffd17d"; stripCtx.lineWidth = 2; stripCtx.beginPath(); stripCtx.moveTo(x(cursor), 0); stripCtx.lineTo(x(cursor), height); stripCtx.stroke(); stripCtx.lineWidth = 1;
    }
    function stripTime(event) {
        const rect = strip.getBoundingClientRect();
        return clamp((event.clientX - rect.left) / rect.width, 0, 1) * metadata.duration;
    }
    strip.onpointerdown = event => {
        if (!metadata || event.button !== 0) return;
        event.preventDefault(); event.stopPropagation(); strip.setPointerCapture(event.pointerId); seek(stripTime(event));
    };
    strip.onpointermove = event => { if (metadata && strip.hasPointerCapture(event.pointerId)) seek(stripTime(event)); };
    strip.onpointerup = strip.onpointercancel = event => { if (strip.hasPointerCapture(event.pointerId)) strip.releasePointerCapture(event.pointerId); };
    function pointerTime(event) {
        const rect = canvas.getBoundingClientRect();
        return clamp(viewStart + (event.clientX - rect.left) / rect.width * (viewEnd - viewStart), 0, metadata.duration);
    }
    canvas.onpointerdown = event => {
        if (!metadata || event.button !== 0) return;
        event.preventDefault(); event.stopPropagation(); canvas.focus(); stop();
        const time = pointerTime(event), rect = canvas.getBoundingClientRect();
        const tolerance = 11 / rect.width * (viewEnd - viewStart);
        const a = Number(start.value), b = Number(end.value), y = event.clientY - rect.top;
        const mode = Math.abs(time - a) <= tolerance ? "in" : Math.abs(time - b) <= tolerance ? "out" : y >= 26 && y <= 53 && time > a && time < b ? "move" : "seek";
        drag = { mode, time, a, b }; canvas.setPointerCapture(event.pointerId);
        if (mode === "seek") seek(time);
    };
    canvas.onpointermove = event => {
        if (!drag || !metadata) return;
        const time = pointerTime(event);
        const a = frameOf(Number(start.value)), b = frameOf(Number(end.value)), k = Math.round(time * fps());
        if (drag.mode === "in") { setFrames(Math.min(k, b - 1), b); cursor = stored(frameOf(Number(start.value))); requestFrame(); }
        else if (drag.mode === "out") { setFrames(a, Math.max(k, a + 1)); cursor = stored(Math.max(a, frameOf(Number(end.value)) - 1)); requestFrame(); }
        else if (drag.mode === "move") {
            const da = frameOf(drag.a), db = frameOf(drag.b);
            const delta = clamp(Math.round((time - drag.time) * fps()), -da, metadata.frames - db);
            setFrames(da + delta, db + delta); cursor = stored(frameOf(Number(start.value))); requestFrame();
        } else seek(time);
        movie.hidden = true; image.hidden = !objectURL; draw();
    };
    canvas.onpointerup = canvas.onpointercancel = event => { drag = null; if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId); };
    canvas.onwheel = event => { if (metadata) { event.preventDefault(); event.stopPropagation(); zoom(event.deltaY > 0 ? 1.25 : .8, pointerTime(event)); } };
    canvas.ondblclick = () => { if (metadata) fitSelection(); };
    canvas.onkeydown = event => {
        if (!metadata) return;
        if (event.key === "ArrowLeft" || event.key === "ArrowRight") seek(cursor + (event.key === "ArrowLeft" ? -1 : 1) * frameStep() * (event.shiftKey ? 10 : 1));
        else if (event.key.toLowerCase() === "i") markIn();
        else if (event.key.toLowerCase() === "o") markOut();
        else if (event.key === " ") togglePlayback();
        else return;
        event.preventDefault(); event.stopPropagation();
    };
    overview.oninput = () => { if (metadata) seek(Number(overview.value)); };
    root.addEventListener("pointerdown", event => event.stopPropagation());
    root.addEventListener("keydown", event => event.stopPropagation());

    async function refreshFiles() {
        refresh.disabled = true;
        try {
            const result = await api.fetchApi("/wepenerd/long_video/files", { signal: abort.signal });
            if (!result.ok) throw new Error("Cannot refresh files. Restart ComfyUI to load the new node routes.");
            file.options.values = await result.json();
            if (!file.options.values.includes(file.value)) file.value = file.options.values[0];
            changed(); await load();
        } catch (error) { if (!removed && error.name !== "AbortError") message(error.message, true); }
        finally { refresh.disabled = false; }
    }
    async function load() {
        if (removed) return;
        const currentVersion = ++version; stop(); clearTimeout(timer); pendingFrame = null;
        loadAbort?.abort(); loadAbort = new AbortController();
        metadata = null; source = String(file.value || ""); movieFailed = false; proxy = null; playOffset = 0;
        movie.removeAttribute("src"); movie.load(); movie.hidden = true; image.hidden = true;
        if (objectURL) { URL.revokeObjectURL(objectURL); objectURL = null; image.removeAttribute("src"); }
        draw();
        if (!source) { message("Copy a video into ComfyUI/input, then click Refresh files."); return; }
        message("Reading video metadata…");
        try {
            const result = await response("metadata", {}, loadAbort.signal); const data = await result.json();
            if (removed || currentVersion !== version) return;
            metadata = data;
            cursor = stored(Math.min(frameOf(Number(start.value)), lastFrame()));
            viewStart = 0; viewEnd = data.duration; draw(); requestFrame();
        } catch (error) { if (!removed && currentVersion === version && error.name !== "AbortError") message(error.message, true); }
    }
    function scheduleLoad() {
        if (loadQueued) return;
        loadQueued = true; queueMicrotask(() => { loadQueued = false; load(); });
    }
    async function loadPresets() {
        try {
            const result = await api.fetchApi("/wepenerd/long_video/presets", { signal: abort.signal });
            if (result.ok) { presets = await result.json(); draw(); }
        } catch { /* Without presets the readout simply shows untrimmed counts. */ }
    }
    if (formatWidget) {
        const callback = formatWidget.callback;
        formatWidget.callback = function(...args) {
            callback?.apply(this, args);
            const p = preset();
            if (p && rateWidget) rateWidget.value = p.fps;
            stop(); changed(); draw();
        };
    }
    if (rateWidget) {
        const callback = rateWidget.callback;
        rateWidget.callback = function(...args) { callback?.apply(this, args); stop(); draw(); };
    }
    const fileChanged = file.callback;
    file.callback = function(...args) { fileChanged?.apply(this, args); load(); };
    for (const widget of [start, end]) {
        const callback = widget.callback;
        widget.callback = function(...args) { callback?.apply(this, args); stop(); draw(); };
    }
    const configured = node.onConfigure;
    node.onConfigure = function(...args) { configured?.apply(this, args); scheduleLoad(); };
    const observer = new ResizeObserver(draw); observer.observe(canvas); observer.observe(strip);
    const onRemoved = node.onRemoved;
    node.onRemoved = function(...args) {
        removed = true; version++; stop(); abort.abort(); loadAbort?.abort(); clearTimeout(timer); observer.disconnect();
        movie.removeAttribute("src"); movie.load(); if (objectURL) URL.revokeObjectURL(objectURL); root.remove();
        onRemoved?.apply(this, args);
    };
    scheduleLoad(); loadPresets();
}

app.registerExtension({
    name: "WepeNerd.LoadLongVideo",
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "WN_LoadLongVideo") return;
        const created = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function(...args) { const result = created?.apply(this, args); setup(this); return result; };
    },
});
