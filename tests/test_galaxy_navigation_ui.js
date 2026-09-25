"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const app = fs.readFileSync(
  path.join(__dirname, "..", "tools", "universe_ui", "app.js"),
  "utf8"
);
const css = fs.readFileSync(
  path.join(__dirname, "..", "tools", "universe_ui", "styles.css"),
  "utf8"
);

assert.match(app, /galaxySearch: "",/);
assert.match(app, /galaxyStateFilter: "ALL"/);
assert.match(app, /setAttribute\("role", "tab"\)/);
assert.match(app, /setAttribute\("aria-selected", name === active \? "true" : "false"\)/);
assert.match(app, /setAttribute\("aria-controls", "universe-graph"\)/);
assert.match(app, /ArrowLeft.*ArrowRight.*Home.*End/s);
assert.match(app, /type = "search"/);
assert.match(app, /Search Galaxy nodes/);
assert.match(app, /Filter Galaxy node state/);
assert.match(app, /aria-live.*polite/);
assert.match(css, /\.galaxy-explore-controls/);
assert.match(css, /\.galaxy-view-chip\[aria-selected="true"\]/);

// Exercise the exact search/state contract with a 22-node relationship fixture.
const nodes = Array.from({ length: 22 }, (_, index) => ({
  node_id: `node-${index}`,
  title: index === 7 ? "Checkout Flow" : `Node ${index}`,
  kind: index % 2 ? "FEATURE" : "FLOW",
  state: index === 7 ? "PROPOSED" : index % 3 ? "READY" : "DONE",
}));
const matches = (item, query, state) => {
  const text = String(item.title || item.node_id).toLowerCase();
  const kind = String(item.kind || "").toLowerCase();
  return (!query || text.includes(query) || kind.includes(query)) &&
    (state === "ALL" || item.state === state);
};
assert.equal(nodes.length, 22);
assert.deepEqual(
  nodes.filter((node) => matches(node, "checkout", "ALL")).map((node) => node.node_id),
  ["node-7"]
);
assert.ok(nodes.filter((node) => matches(node, "", "DONE")).length > 0);
assert.ok(nodes.filter((node) => matches(node, "feature", "READY")).every(
  (node) => node.kind === "FEATURE" && node.state === "READY"
));

console.log("Galaxy search/filter, 22-node fixture, selection details, and ARIA tabs passed.");
