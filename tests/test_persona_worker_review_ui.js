const fs = require("fs");
const path = require("path");
const assert = require("assert");

const app = fs.readFileSync(path.join(__dirname, "..", "tools", "universe_ui", "app.js"), "utf8");

assert(app.includes('invokeServerAction("persona.automation.status"'), "Fleet automation must read the typed status Action");
assert(app.includes("run?.current_worker"), "Fleet automation projection must expose the Worker phase");
assert(app.includes("run?.current_reviewer"), "Fleet automation projection must expose the Reviewer phase");
assert(app.includes('invokeServerAction("fleet.worker-session-start"'), "Fleet UI must create Worker sessions through the typed Fleet Action");
assert(app.includes('invokeServerAction("fleet.worker-assignments-list"'), "Fleet UI must read authoritative assignment history");
console.log("persona worker/reviewer UI contract: PASS");
