import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const pending = new Set();
const supported = /\.(png|jpe?g|webp|bmp|tiff?)$/i;
const nodeNames = new Set(["WN_H3RefModCreate", "WN_H3RefModPreview"]);

async function uploadFiles(node, files) {
    const paths = node.widgets.find(w => w.name === "image_paths");
    const recursive = node.widgets.find(w => w.name === "recursive")?.value;
    const selected = [...files].filter(f => supported.test(f.name) &&
        (recursive || !f.webkitRelativePath || f.webkitRelativePath.split("/").length <= 2));
    if (selected[0]?.webkitRelativePath) {
        selected.sort((a, b) => a.webkitRelativePath.localeCompare(b.webkitRelativePath, "en", { numeric: true }));
    }
    if (!selected.length) throw new Error("No supported images selected. Enable recursive to include subfolders.");
    const subfolder = `wepenerd_refmod/${crypto.randomUUID()}`;
    for (const file of selected) {
        const body = new FormData();
        body.append("image", file);
        body.append("type", "input");
        body.append("subfolder", subfolder);
        const response = await api.fetchApi("/upload/image", { method: "POST", body });
        if (!response.ok) throw new Error(`Upload failed for ${file.name}: ${await response.text()}`);
        const saved = await response.json();
        const path = [saved.subfolder, saved.name].filter(Boolean).join("/");
        paths.value = [...new Set([...String(paths.value || "").split("\n").filter(Boolean), path])].join("\n");
        paths.callback?.(paths.value);
        node.graph?.setDirtyCanvas(true, true);
    }
}

app.registerExtension({
    name: "WepeNerd.H3RefMod",
    setup() {
        const queuePrompt = app.queuePrompt;
        app.queuePrompt = async function (...args) {
            await Promise.all([...pending]);
            return queuePrompt.apply(this, args);
        };
    },
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (!nodeNames.has(nodeData.name)) return;
        const onCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function (...args) {
            const result = onCreated?.apply(this, args);
            for (const [label, directory] of [["Add images", false], ["Upload folder", true]]) {
                const button = this.addWidget("button", label, null, () => {
                    const picker = document.createElement("input");
                    picker.type = "file";
                    picker.multiple = true;
                    picker.accept = ".png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff";
                    if (directory) picker.webkitdirectory = true;
                    picker.onchange = async () => {
                        if (!picker.files?.length) return;
                        button.name = "Uploading…";
                        const task = uploadFiles(this, picker.files);
                        pending.add(task);
                        try {
                            await task;
                        } catch (error) {
                            alert(error.message);
                        } finally {
                            pending.delete(task);
                            button.name = label;
                            this.graph?.setDirtyCanvas(true, true);
                        }
                    };
                    picker.click();
                }, { serialize: false });
            }
            return result;
        };
    },
});
