import assert from "node:assert/strict";
import fs from "node:fs/promises";
import vm from "node:vm";
import test from "node:test";

const source = (await fs.readFile(new URL("../js/wn_refmod.js", import.meta.url), "utf8"))
    .replace(/^import .*;\r?\n/gm, "");

function harness(recursive = false, upload = async () => {}) {
    let extension, picker;
    const sent = [], alerts = [];
    let queued = 0;
    const app = {
        registerExtension(value) { extension = value; },
        async queuePrompt() { queued++; },
    };
    const api = { async fetchApi(url, request) {
        assert.equal(url, "/upload/image");
        const file = request.body.values.image;
        sent.push(file.name);
        await upload(file);
        return { ok: true, json: async () => ({ subfolder: "wepenerd_refmod/test", name: file.name }) };
    } };
    class Node {
        constructor() {
            this.widgets = [{ name: "image_paths", value: "existing.png" }, { name: "recursive", value: recursive }];
            this.graph = { setDirtyCanvas() {} };
        }
        addWidget(type, name, value, callback) {
            const widget = { type, name, value, callback };
            this.widgets.push(widget);
            return widget;
        }
    }
    vm.runInNewContext(source, { app, api, crypto: { randomUUID: () => "test" },
        alert: value => alerts.push(value),
        FormData: class { values = {}; append(key, value) { this.values[key] = value; } },
        document: { createElement() { picker = { click() {} }; return picker; } },
    });
    extension.setup();
    extension.beforeRegisterNodeDef(Node, { name: "WN_H3RefModCreate" });
    const node = new Node();
    node.onNodeCreated();
    return { node, sent, alerts, app, queued: () => queued,
        async select(files, label = "Add images") {
            node.widgets.find(w => w.name === label).callback();
            picker.files = files;
            await picker.onchange();
        } };
}

test("multi-upload appends server paths and preserves existing selections", async () => {
    const h = harness();
    await h.select([{ name: "a.png" }, { name: "b.jpg" }, { name: "ignore.txt" }]);
    assert.deepEqual(h.sent, ["a.png", "b.jpg"]);
    assert.equal(h.node.widgets[0].value, "existing.png\nwepenerd_refmod/test/a.png\nwepenerd_refmod/test/b.jpg");
});

test("folder upload is naturally sorted and recursion is explicit", async () => {
    const files = [
        { name: "10.png", webkitRelativePath: "folder/10.png" },
        { name: "2.png", webkitRelativePath: "folder/2.png" },
        { name: "deep.png", webkitRelativePath: "folder/sub/deep.png" },
    ];
    const flat = harness();
    await flat.select(files, "Upload folder");
    assert.deepEqual(flat.sent, ["2.png", "10.png"]);
    const recursive = harness(true);
    await recursive.select(files, "Upload folder");
    assert.deepEqual(recursive.sent, ["2.png", "10.png", "deep.png"]);
});

test("queue waits until uploads have finished", async () => {
    let finish;
    const h = harness(false, () => new Promise(resolve => { finish = resolve; }));
    const uploading = h.select([{ name: "a.png" }]);
    const queueing = h.app.queuePrompt();
    assert.equal(h.queued(), 0);
    finish();
    await Promise.all([uploading, queueing]);
    assert.equal(h.queued(), 1);
});

test("failed uploads retain successful files and surface the error", async () => {
    const h = harness(false, async file => { if (file.name === "bad.png") throw new Error("Upload failed"); });
    await h.select([{ name: "a.png" }, { name: "bad.png" }]);
    assert.equal(h.alerts[0], "Upload failed");
    assert.equal(h.node.widgets[0].value, "existing.png\nwepenerd_refmod/test/a.png");
});
