import assert from "node:assert/strict";
import test from "node:test";
import {
    DEFAULTS, normalizeParams, makeCamera, buildBottle, buildGrid, buildHandles,
    buildRotateRing, groundPoint, pointInPolygon, rotateRingRadius,
    projectGround, solvePerspective, vanishingPoint,
} from "../js/wn_bottle_geometry.mjs";

const scene = (overrides = {}, width = 1200, height = 900) => {
    const p = normalizeParams({ ...DEFAULTS, ...overrides });
    return { p, cam: makeCamera(p, width, height) };
};

test("the base centre lands on pos_x, pos_y", () => {
    const { p, cam } = scene({ pos_x: 0.3, pos_y: 0.7, camera_pitch: 30, camera_orbit: 40, fov: 70 });
    const ring = buildRotateRing(p, cam);
    const xs = ring.points.map((point) => point[0]), ys = ring.points.map((point) => point[1]);
    // The ring is centred on the base, so the base sits inside its bounds.
    assert.ok(Math.min(...xs) < 360 && 360 < Math.max(...xs));
    assert.ok(Math.min(...ys) < 630 && 630 < Math.max(...ys));
    assert.deepEqual(groundPoint(cam, 360, 630).map((value) => Math.abs(Math.round(value * 1e6))), [0, 0]);
});

test("scale is the unit size as a percentage of image height", () => {
    // Scale is measured at the depth of the base centre, so use a bottle with
    // almost no depth: seen level, 29 units at 2% of 900 px each.
    const { p, cam } = scene({ camera_pitch: 0, scale: 2, outline_width: 0, body_depth: 0.1, neck_depth: 0.1 });
    const ys = buildBottle(p, cam).flatMap((op) => op.points.map((point) => point[1]));
    assert.ok(Math.abs((Math.max(...ys) - Math.min(...ys)) - 29 * 18) < 1);
});

test("the turn knob follows rotate_y on the ground plane", () => {
    const { p, cam } = scene({ rotate_y: 65, camera_pitch: 35, camera_orbit: -20 });
    const ground = groundPoint(cam, ...buildRotateRing(p, cam).knob);
    assert.ok(Math.abs(Math.atan2(ground[0], ground[1]) * (180 / Math.PI) - 65) < 1e-6);
    assert.ok(Math.abs(Math.hypot(...ground) - rotateRingRadius(p)) < 1e-6);
});

test("handles report a usable screen axis for each dimension", () => {
    const { p, cam } = scene({ camera_pitch: 25, rotate_y: 30 });
    const handles = buildHandles(p, cam);
    assert.deepEqual(handles.map((handle) => handle.param).sort(), [
        "body_depth", "body_height", "body_width", "neck_depth", "neck_height", "neck_width",
    ]);
    // Height handles move straight up the screen for an upright bottle at image centre.
    const height = handles.find((handle) => handle.param === "body_height");
    assert.ok(height.axis[1] < 0 && Math.abs(height.axis[1]) > 5 * Math.abs(height.axis[0]));
});

test("box and cylinder draw a body, a label and a neck", () => {
    for (const shape of ["box", "cylinder"]) {
        const { p, cam } = scene({ shape, rotate_y: 20 });
        const ops = buildBottle(p, cam);
        const fills = new Set(ops.filter((op) => op.fill).map((op) => op.fill.join()));
        assert.ok(fills.size >= 4, `${shape} should show several shaded faces`);
        assert.ok(ops.some((op) => op.stroke), `${shape} should have outlines`);
        const bodyFront = ops.find((op) => op.fill && pointInPolygon([600, 600], op.points));
        assert.ok(bodyFront, `${shape} should cover the image below its centre`);
    }
    const { p, cam } = scene({ outline_width: 0 });
    assert.ok(buildBottle(p, cam).every((op) => !op.stroke));
});

test("grid lines stay inside the image and survive a wide lens", () => {
    const { p, cam } = scene({ fov: 120, camera_pitch: 5, scale: 12 });
    const lines = buildGrid(p, cam);
    assert.ok(lines.length > 20);
    for (const line of lines) {
        for (const [x, y] of [line.a, line.b]) {
            assert.ok(Number.isFinite(x) && Number.isFinite(y));
            assert.ok(x >= -20 && x <= 1220 && y >= -20 && y <= 920);
        }
    }
});

test("unusable widget values fall back instead of breaking the preview", () => {
    const p = normalizeParams({ body_width: "abc", fov: 500, body_color: "#12", shape: "sphere" });
    assert.equal(p.body_width, DEFAULTS.body_width);
    assert.equal(p.fov, 120);
    assert.deepEqual(p.body_color, [208, 208, 208]);
    assert.equal(p.shape, "box");
    assert.deepEqual(normalizeParams({ body_color: "0af" }).body_color, [0, 170, 255]);
});

// Segments along the ground's X and Z directions, as a user would trace them.
function tracedEdges(cam) {
    const segment = (x1, z1, x2, z2) => [...projectGround(cam, x1, z1), ...projectGround(cam, x2, z2)];
    return {
        a: [segment(-6, -4, 9, -4), segment(-3, 7, 8, 7)],
        b: [segment(-5, -6, -5, 8), segment(6, -3, 6, 10)],
    };
}

test("traced edges recover the camera that produced them", () => {
    for (const camera of [
        { fov: 55, camera_pitch: 28, camera_roll: 4, camera_orbit: 33 },
        { fov: 28, camera_pitch: 12, camera_roll: -7, camera_orbit: -52 },
        { fov: 85, camera_pitch: 50, camera_roll: 0, camera_orbit: 20, pos_x: 0.3, pos_y: 0.6 },
    ]) {
        const { cam } = scene(camera);
        const { a, b } = tracedEdges(cam);
        const start = { fov: 40, camera_orbit: camera.camera_orbit + 20 };
        const result = solvePerspective(a, b, 1200, 900, start);
        assert.ok(result.solvedFov, JSON.stringify(camera));
        for (const name of ["fov", "camera_pitch", "camera_roll", "camera_orbit"]) {
            assert.ok(Math.abs(result.values[name] - camera[name]) < 1e-6, `${name}: ${result.values[name]} vs ${camera[name]}`);
        }
        // An extra line that is slightly off still gives a close answer.
        const noisy = a.concat([a[0].map((value, index) => value + (index === 1 ? 1.5 : 0))]);
        const fitted = solvePerspective(noisy, b, 1200, 900, start);
        assert.ok(Math.abs(fitted.values.camera_pitch - camera.camera_pitch) < 3);
    }
});

test("the grid turn keeps to the side nearest the current orbit", () => {
    const { cam } = scene({ fov: 55, camera_pitch: 28, camera_orbit: 33 });
    const { a, b } = tracedEdges(cam);
    const flipped = solvePerspective(a, b, 1200, 900, { fov: 40, camera_orbit: -150 });
    assert.ok(Math.abs(flipped.values.camera_orbit - (33 - 180)) < 1e-6);
});

test("one traced direction solves pitch and orbit with a level horizon", () => {
    const { cam } = scene({ fov: 40, camera_pitch: 22, camera_roll: 0, camera_orbit: 35 });
    const { a, b } = tracedEdges(cam);
    for (const [first, second] of [[a, []], [[], b], [a, [b[0]]]]) {
        const result = solvePerspective(first, second, 1200, 900, { fov: 40, camera_orbit: 30 });
        assert.equal(result.solvedFov, false);
        assert.equal(result.values.fov, undefined);
        assert.ok(Math.abs(result.values.camera_pitch - 22) < 1e-6);
        assert.ok(Math.abs(result.values.camera_roll) < 1e-6);
        assert.ok(Math.abs(result.values.camera_orbit - 35) < 1e-6);
    }
});

test("lines that are parallel on screen keep the current lens", () => {
    // Facing the surface square on: one direction does not converge on screen.
    const { cam } = scene({ fov: 60, camera_pitch: 30, camera_orbit: 0 });
    const { a, b } = tracedEdges(cam);
    const result = solvePerspective(a, b, 1200, 900, { fov: 60, camera_orbit: 0 });
    assert.equal(result.solvedFov, false);
    assert.ok(Math.abs(result.values.camera_pitch - 30) < 1e-6);
    assert.ok(Math.abs(result.values.camera_orbit) < 1e-6);
    assert.match(result.message, /kept fov 60\.0/);
});

test("too little to go on changes nothing", () => {
    assert.equal(solvePerspective([], [], 1200, 900, { fov: 40, camera_orbit: 0 }).values, null);
    const one = [[100, 100, 500, 300]];
    assert.equal(solvePerspective(one, one, 1200, 900, { fov: 40, camera_orbit: 0 }).values, null);
    // The same line twice is still one line.
    assert.equal(vanishingPoint([one[0], [300, 200, 700, 400]], 1200, 900), null);
});
