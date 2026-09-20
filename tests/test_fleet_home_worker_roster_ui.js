const fs = require('node:fs');
const assert = require('node:assert/strict');

const source = fs.readFileSync('tools/universe_ui/app.js', 'utf8');
assert.match(source, /function fleetHomeWorkerSummary\(nodes\)/);
assert.match(source, /Running Worker \/ Reviewer/);
assert.match(source, /fleet-home-worker-count/);
assert.match(source, /fleet-home-worker-peek/);
assert.match(source, /function peekFleetWorkerTerminal\(terminal\)/);
assert.match(source, /state\.terminals = \[/);
assert.match(source, /peekFleetWorkerTerminal\(terminal\)/);
// Regression: the authoritative Worker projection is intentionally excluded
// from the normal dock, so Peek must explicitly attach that excluded session.
assert.match(source, /current\.some\(\(item\) => String\(item\?\.terminal_id/);
assert.match(source, /selectTerminalTab\(terminalId\)/);
assert.match(source, /void ensureFleetWorkerAssignments\(featureId\)/);
assert.match(source, /listEl\.append\(fleetHomeWorkerSummary\(nodes\)\)/);

const css = fs.readFileSync('tools/universe_ui/styles.css', 'utf8');
assert.match(css, /\.fleet-home-worker-summary/);
assert.match(css, /\.fleet-home-worker-row/);
console.log('PASS Fleet home shows authoritative running Worker/Reviewer count and Peek actions.');
