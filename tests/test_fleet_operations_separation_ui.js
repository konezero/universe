const assert = require('assert/strict');
const fs = require('fs');
const vm = require('vm');

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
assert.match(showMemory, /homeWorkstreamKind = "OPERATIONS"/);
assert.match(showMemory, /renderMemoryOpsView\(\)/);
const renderOps = app.slice(app.indexOf('function renderMemoryOpsView()'), app.indexOf('function showMemoryOpsView()'));
assert.match(renderOps, /view.append\(board\)/, 'Ops must host the existing full node board');
assert.match(renderOps, /renderIntegratedHome\(\)/);
assert.doesNotMatch(renderOps, /memoryBatchConfigs|memoryBatchRuns|memoryCandidates/, 'Ops is not a copied batch summary');

assert.match(app, /function activateCentralPage\(page\)/, 'central routes must select one page surface');
assert.match(app, /\"memory-ops\": \"ops\"/, 'Ops rail route must activate its own surface');
assert.match(app, /timeline: \"graph\", implementation: \"graph\"/, 'all graph routes must share the graph surface');
assert.match(app, /if \(centralPage\) activateCentralPage\(centralPage\)/, 'primary navigation must activate the selected surface');

const hideMemoryStart = app.indexOf('function hideMemoryOpsView()');
const hideMemoryEnd = app.indexOf('\n}', hideMemoryStart) + 2;
const hideMemory = app.slice(hideMemoryStart, hideMemoryEnd);
assert.match(hideMemory, /memory-ops-view/);
assert.match(hideMemory, /workspace.insertBefore\(board/);
assert.match(hideMemory, /homeWorkstreamKind = "DEVELOPMENT"/);
assert.match(utilityRail, /<small>Ops<\/small>/);

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
assert.match(showProject, /restoreProjectPanels\(\)/, 'switching project screens must park the prior screen panel');

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
assert.match(inspectorTab, /restoreBenchPanel\(\)/, 'inspector routes must hide the dedicated Bench screen');
assert.match(inspectorTab, /restoreProjectPanels\(\)/, 'inspector routes must hide project screens');
assert.match(inspectorTab, /name !== "details"/);
assert.match(inspectorTab, /name !== "future"/);

const workerStart = app.indexOf('function fleetHomeWorkerRows(nodes)');
const workerEnd = app.indexOf('function renderHomeNodes(selNode)', workerStart);
const workerContext = {
  state: {
    homeWorkstreamKind: 'DEVELOPMENT',
    fleetWorkerAssignmentsProject: null,
    fleetTaskFrameHosts: {status: 'READY', rows: [
      {task_frame_id: 'dev-host', node_ref: 'dev', alive: true},
      {task_frame_id: 'ops-host', node_ref: 'ops', alive: true},
      {task_frame_id: 'project-host', node_ref: null, alive: true},
    ]},
  },
  fleetFeatureIsOperations: ref => ref === 'ops',
};
vm.createContext(workerContext);
vm.runInContext(app.slice(workerStart, workerEnd), workerContext);
assert.deepEqual(
  Array.from(workerContext.fleetHomeWorkerRows([]), row => row.task_frame_id).sort(),
  ['dev-host', 'project-host'],
  'Fleet must not show Operations hosts as unassigned development workers'
);
workerContext.state.homeWorkstreamKind = 'OPERATIONS';
assert.deepEqual(
  Array.from(workerContext.fleetHomeWorkerRows([]), row => row.task_frame_id),
  ['ops-host'],
  'Ops must show only hosts owned by Operations nodes'
);
const newNodeRoute = app.slice(app.indexOf('async function submitHomeNode('), app.indexOf('// + Todo', app.indexOf('async function submitHomeNode(')));
assert.match(newNodeRoute, /state.homeWorkstreamKind === "OPERATIONS"/, 'Ops uses the Operations branch');
assert.match(newNodeRoute, /workstream_kind: "OPERATIONS"/, 'Ops retains its Operations Feature registration');
assert.match(newNodeRoute, /scope_kind: "PROJECT"/, 'Fleet registers a project Goal instead of a Feature');
const bindingRoute = app.slice(app.indexOf('function goToNodeMasterBinding('), app.indexOf('function openFleetNodeFromTerminal('));
assert.match(bindingRoute, /fleetFeatureIsOperations\(featureId\).*showMemoryOpsView\(\)/s);
const activityTodoRoute = app.slice(app.indexOf('function openActivityTodo('), app.indexOf('function openActivityTaskFrame('));
assert.match(activityTodoRoute, /fleetTodoIsOperations\(todo\).*showMemoryOpsView\(\)/s);

console.log('Ops owns whole Operations nodes, their workers, and routes; Fleet excludes them.');
