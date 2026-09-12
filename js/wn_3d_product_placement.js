import { app } from "../../scripts/app.js";

let implementation;
let promptInstalled = false;
const loading = new Map();

app.registerExtension({
    name: "WepeNerd.3DProductPlacement",
    setup() {
        if (promptInstalled) return;
        promptInstalled = true;
        const original = app.graphToPrompt;
        const wrapped = async function(...args) {
            const graph = args[0] || app.rootGraph || app.graph;
            await Promise.all([...loading].filter(([node]) => node.graph === graph).map(([, promise]) => promise));
            return original.apply(this, args);
        };
        app.graphToPrompt = wrapped;
    },
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (!["WN_LoadOBJ", "WN_3DProductPlacement"].includes(nodeData.name) || nodeType.prototype._wn3dLazy) return;
        nodeType.prototype._wn3dLazy = true;
        const created = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function(...args) {
            const result = created?.apply(this,args);
            const node = this;
            let removed = false;
            const onRemoved = node.onRemoved;
            node.onRemoved = function(...args) { removed = true; loading.delete(node); return onRemoved?.apply(this,args); };
            implementation ||= import("./wn_3d_editor.mjs").catch(error => { implementation = null; throw error; });
            const ready = implementation.then(async ({extension}) => {
                if (removed) return;
                // Install the existing hooks on this instance once its renderer is available.
                const prototype = Object.create(node);
                prototype.onNodeCreated = function() {};
                await extension.beforeRegisterNodeDef({prototype},nodeData);
                if (removed) return;
                Object.defineProperties(node,Object.getOwnPropertyDescriptors(prototype));
                node.onNodeCreated();
            }).finally(() => loading.delete(node));
            loading.set(node,ready);
            ready.catch(error => { if (!removed) { console.error("WepeNerd 3D editor:",error); node.title = "3D editor failed to load — see console"; } });
            return result;
        };
    },
});
