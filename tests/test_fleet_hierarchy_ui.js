"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const app = fs.readFileSync(path.join(root, "tools", "universe_ui", "app.js"), "utf8");
const css = fs.readFileSync(path.join(root, "tools", "universe_ui", "styles.css"), "utf8");

assert.match(app, /renderFleetHierarchyTrail\(selNode, selTodo\)/);
assert.match(app, /fleet-hierarchy-trail/);
assert.match(app, /renderFleetAutomationGuidance\(projection\.error\)/);
assert.match(app, /사용량 한도 또는 제공자 쿼터에 도달했습니다/);
assert.match(app, /Goal 소유 Master\/Conductor 세션으로 전환/);
assert.match(css, /\.fleet-hierarchy-trail/);
assert.match(css, /\.fleet-automation-guidance/);
assert.match(css, /text-overflow: ellipsis/);

console.log("fleet hierarchy UI checks passed");
