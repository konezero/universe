const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
const source = fs.readFileSync('tools/universe_ui/app.js', 'utf8');
const start = source.indexOf('function homeNodeFullyDone(');
const end = source.indexOf('// A todo belongs to a node', start);
const context = { state: {projection: {unified_graph: {nodes: [
  {node_id:'app',kind:'APP',state:'ADOPTED'},
  {node_id:'clinic',kind:'CARE_PATHWAY',state:'ADOPTED'},
  {node_id:'prediction',kind:'FEATURE',state:'PROPOSED'},
  {node_id:'memory',kind:'MEMORY',state:'ADOPTED'},
  {node_id:'document',kind:'DOCUMENT',state:'ADOPTED'},
  {node_id:'feat:work',kind:'FEATURE',state:'ADOPTED'},
  {node_id:'feat:ops',kind:'FEATURE',state:'ADOPTED',data:{workstream_kind:'OPERATIONS'}},
  {node_id:'app',kind:'APP',state:'ADOPTED'},
]}}, fleetFilters: {showDone:false, showDiscarded:false}, projectFeatures: []}, homeAllTodos:()=>[{node_ref:'work'}], homeNodeRefKey:id=>id.replace(/^feat:/,''), homeNodeTodos:n=>n.node_id==='feat:work'?[{state:'IN_PROGRESS'}]:[], fleetShowDone:()=>false, fleetShowDiscarded:()=>false };
vm.createContext(context);
vm.runInContext(source.slice(start,end),context);
assert.deepEqual(
  Array.from(context.homeNodes(), n => n.node_id),
  ['feat:work'],
  'Fleet must expose only explicit Feature Node registrations'
);
context.state.homeWorkstreamKind = 'OPERATIONS';
assert.deepEqual(Array.from(context.homeNodes(),n=>n.node_id),['feat:ops'], 'Ops board must own the complete Operations node');
context.state.homeWorkstreamKind = 'DEVELOPMENT';
assert.match(source, /function isRegisteredFeatureNode\(graphNode\)/);
assert.match(source, /let nodes = unified.nodes.filter\(\(n\) => !nodeAllow \|\| nodeAllow.has\(n.node_id\)\)/, 'Galaxy must retain its full topology projection');
assert.match(source.slice(source.indexOf('async function submitHomeNode('),source.indexOf('// + Todo',source.indexOf('async function submitHomeNode('))),/invokeServerAction\("feature.create"/);

// Fleet 완료/폐기 필터: DONE-only nodes and ARCHIVED Feature Nodes hide by
// default and reappear only when their toggle is on; the toggle never
// mutates Todo/node state (homeNodeTodos/projectFeatures stay untouched).
const filters = { showDone: false, showDiscarded: false };
const filterContext = {
  state: {
    projection: { unified_graph: { nodes: [
      {node_id:'app',kind:'APP',state:'ADOPTED'},
      {node_id:'feat:work',kind:'FEATURE',state:'ADOPTED'},
      {node_id:'feat:done',kind:'FEATURE',state:'ADOPTED'},
    ]}},
    fleetFilters: filters,
    projectFeatures: [{feature_id:'gone', state:'ARCHIVED', intent_text:'Retired feature'}],
  },
  homeAllTodos: () => [{node_ref:'work'},{node_ref:'done'}],
  homeNodeRefKey: id => id.replace(/^feat:/, ''),
  homeNodeTodos: n => n.node_id === 'feat:work' ? [{state:'IN_PROGRESS'}]
    : n.node_id === 'feat:done' ? [{state:'DONE'}]
    : [],
  fleetShowDone: () => Boolean(filters.showDone),
  fleetShowDiscarded: () => Boolean(filters.showDiscarded),
};
vm.createContext(filterContext);
vm.runInContext(source.slice(start, end), filterContext);
assert.deepEqual(
  Array.from(filterContext.homeNodes(), n => n.node_id).sort(),
  ['feat:work'],
  'unregistered structural nodes, fully-DONE nodes, and archived features hide by default'
);
filters.showDone = true;
assert.ok(
  Array.from(filterContext.homeNodes(), n => n.node_id).includes('feat:done'),
  'showDone reveals the fully-DONE node'
);
filters.showDone = false;
filters.showDiscarded = true;
const withDiscarded = filterContext.homeNodes();
const archivedNode = withDiscarded.find(n => n.node_id === 'feat:gone');
assert.ok(archivedNode, 'showDiscarded reveals the ARCHIVED feature node');
assert.equal(archivedNode.state, 'ARCHIVED');
assert.deepEqual(
  filterContext.state.projectFeatures,
  [{feature_id:'gone', state:'ARCHIVED', intent_text:'Retired feature'}],
  'revealing a discarded node must not mutate the underlying Feature Node record'
);

// Goal work nodes are separate from Galaxy/Feature nodes. A DONE Todo does not
// complete a Goal; only the Goal's own terminal state hides it by default.
const goalFilters = {showDone: false, showDiscarded: false};
const goalContext = {
  state: {
    projection: {unified_graph: {nodes: [
      {node_id:'feat:source', kind:'FEATURE', state:'ADOPTED'},
    ]}},
    goals: [
      {goal_id:'goal_active', title:'Follow-up work', state:'ACTIVE', node_ref:'source', description:'Finite work'},
      {goal_id:'goal_done', title:'Previous work', state:'DONE', node_ref:'source'},
    ],
    fleetFilters: goalFilters,
    projectFeatures: [],
  },
  homeAllTodos: () => [
    {todo_id:'t1', goal_id:'goal_active', node_ref:'source', state:'DONE'},
    {todo_id:'t2', goal_id:'goal_done', node_ref:'source', state:'DONE'},
  ],
  homeNodeRefKey: id => id.replace(/^(feat:|goal:)/, ''),
  homeNodeTodos: n => n.goal_id === 'goal_active' ? [{state:'DONE'}]
    : n.goal_id === 'goal_done' ? [{state:'DONE'}] : [],
  fleetShowDone: () => goalFilters.showDone,
  fleetShowDiscarded: () => goalFilters.showDiscarded,
};
vm.createContext(goalContext);
vm.runInContext(source.slice(start, end), goalContext);
assert.deepEqual(Array.from(goalContext.homeNodes(), n => n.node_id), ['goal:goal_active']);
assert.equal(goalContext.homeNodeFullyDone(goalContext.homeNodes()[0]), false);
goalFilters.showDone = true;
assert.deepEqual(Array.from(goalContext.homeNodes(), n => n.node_id).sort(),
  ['goal:goal_active', 'goal:goal_done']);
const ownershipStart = source.indexOf('function homeNodeOwnsTodo(');
const ownershipEnd = source.indexOf('function homeSelectedNode()', ownershipStart);
vm.runInContext(source.slice(ownershipStart, ownershipEnd), goalContext);
assert.equal(goalContext.homeNodeOwnsTodo(
  {node_id:'goal:goal_active',kind:'FLEET_GOAL',goal_id:'goal_active'},
  {goal_id:'goal_active',node_ref:'source'}), true);
assert.equal(goalContext.homeNodeOwnsTodo(
  {node_id:'feat:source',kind:'FEATURE'},
  {goal_id:'goal_active',node_ref:'source'}), false);
assert.equal(goalContext.homeNodeOwnsTodo(
  {node_id:'feat:source',kind:'FEATURE'},
  {node_ref:'source'}), true);
console.log('Fleet structural/domain visibility, prediction exclusion, work ranking, duplicate suppression and manual Action routing passed.');
console.log('Fleet 완료/폐기 필터: default-hidden, toggle-revealed, no data mutation passed.');
