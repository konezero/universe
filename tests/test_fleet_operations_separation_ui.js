const assert = require('assert/strict');
const fs = require('fs');

const html = fs.readFileSync('tools/universe_ui/index.html', 'utf8');
const app = fs.readFileSync('tools/universe_ui/app.js', 'utf8');

const utilityRail = html.match(/<nav class="utility-rail"[\s\S]*?<\/nav>/)?.[0] || '';
const hiddenTopNav = html.match(/<nav id="primary-nav"[\s\S]*?<\/nav>/)?.[0] || '';
assert.match(utilityRail, /data-primary-view="memory-ops"/, 'Memory Ops must be reachable from the visible left rail');
assert.doesNotMatch(hiddenTopNav, /data-primary-view="memory-ops"/, 'Memory Ops must not be placed in the hidden top navigation');

const memoryStart = html.indexOf('<section id="memory-ops-view"');
const memoryEnd = html.indexOf('</section>', memoryStart);
const goalStart = html.indexOf('<section id="goal-plan-workspace"');
const goalEnd = html.lastIndexOf('</section>', memoryStart);
assert.ok(memoryStart >= 0, 'Memory Ops view must exist');
assert.ok(goalStart >= 0 && goalEnd >= 0, 'Goal Plan workspace must exist');
assert.ok(
  memoryStart > goalEnd,
  'Memory Ops must be a sibling after Goal Plan, not a child of the hidden workspace'
);
assert.ok(memoryEnd > memoryStart, 'Memory Ops view must have a closing tag');

const showMemoryStart = app.indexOf('function showMemoryOpsView()');
const showMemoryEnd = app.indexOf('\n}', showMemoryStart) + 2;
const showMemory = app.slice(showMemoryStart, showMemoryEnd);
assert.match(showMemory, /goal-plan-workspace.*setAttribute\("hidden"/s);
assert.match(showMemory, /memory-ops-view.*hidden = false/s);
assert.match(showMemory, /restoreBenchPanel\(\)/);
assert.match(showMemory, /restoreProjectPanels\(\)/);

const hideMemoryStart = app.indexOf('function hideMemoryOpsView()');
const hideMemoryEnd = app.indexOf('\n}', hideMemoryStart) + 2;
const hideMemory = app.slice(hideMemoryStart, hideMemoryEnd);
assert.match(hideMemory, /memory-ops-view/);

const showGoalStart = app.indexOf('function showGoalPlanView()');
const showGoalEnd = app.indexOf('\n}', showGoalStart) + 2;
const showGoal = app.slice(showGoalStart, showGoalEnd);
assert.match(showGoal, /goal-plan-workspace.*hidden = false/s);
assert.match(showGoal, /hideMemoryOpsView\(\)/);

const graphStart = app.indexOf('function showGraphView(view)');
const graphEnd = app.indexOf('\n}', graphStart) + 2;
const showGraph = app.slice(graphStart, graphEnd);
assert.match(showGraph, /hideMemoryOpsView\(\)/);
for (const route of ['map', 'timeline', 'documents']) {
  assert.ok(showGraph.includes('allowed = new Set'), `Graph ${route} exit must use the graph route`);
}

const projectStart = app.indexOf('function showProjectScreen(which)');
const projectEnd = app.indexOf('\n}', projectStart) + 2;
const showProject = app.slice(projectStart, projectEnd);
assert.match(showProject, /goal-plan-workspace.*hidden = true/s);
assert.match(showProject, /hideMemoryOpsView\(\)/);

const benchStart = app.indexOf('function showBenchScreen()');
const benchEnd = app.indexOf('\n}', benchStart) + 2;
const showBench = app.slice(benchStart, benchEnd);
assert.match(showBench, /goal-plan-workspace.*hidden = true/s);
assert.match(showBench, /hideMemoryOpsView\(\)/);
assert.ok(
  showProject.indexOf('hideMemoryOpsView()') < showProject.indexOf('screen.hidden = false'),
  'Project navigation must hide Memory Ops before showing the target screen'
);
assert.ok(
  showBench.indexOf('hideMemoryOpsView()') < showBench.indexOf('screen.hidden = false'),
  'Bench navigation must hide Memory Ops before showing the target screen'
);

const inspectorStart = app.indexOf('function openInspectorSurface(tab)');
const inspectorEnd = app.indexOf('\n}', inspectorStart) + 2;
const openInspector = app.slice(inspectorStart, inspectorEnd);
assert.match(openInspector, /hideMemoryOpsView\(\)/);
const tabStart = app.indexOf('function showInspectorTab(name)');
const tabEnd = app.indexOf('\n}', tabStart) + 2;
const inspectorTab = app.slice(tabStart, tabEnd);
assert.match(inspectorTab, /hideMemoryOpsView\(\)/);
assert.match(inspectorTab, /name !== "details"/);
assert.match(inspectorTab, /name !== "future"/);

console.log('Memory Ops is structurally independent from Goal Plan and view routing toggles both surfaces.');
