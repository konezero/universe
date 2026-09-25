"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const app = fs.readFileSync(path.join(root, "tools", "universe_ui", "app.js"), "utf8");
const css = fs.readFileSync(path.join(root, "tools", "universe_ui", "styles.css"), "utf8");

assert.match(app, /function renderOpsStatusOverview\(automation\)/);
assert.match(app, /ops-status-overview/);
assert.match(app, /scheduler projection · read-only/);
assert.match(app, /처리량/);
assert.match(app, /최근 완료/);
assert.match(app, /function opsAutomationNextAction\(config, schedule, status\)/);
assert.match(app, /Deterministic · model not invoked/);
assert.match(app, /renderOpsStatusOverview\(automation\)/);

assert.match(css, /\.ops-status-overview/);
assert.match(css, /\.ops-status-metrics/);
assert.match(css, /\.ops-status-card\.tone-error/);
assert.match(css, /@media \(max-width: 720px\)/);

console.log("ops status UI contract ok");
