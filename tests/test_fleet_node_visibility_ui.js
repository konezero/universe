const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
const source = fs.readFileSync('tools/universe_ui/app.js', 'utf8');
const start = source.indexOf('function homeNodes() {');
const end = source.indexOf('// A todo belongs to a node', start);
const context = { state: {projection: {unified_graph: {nodes: [
  {node_id:'app',kind:'APP',state:'ADOPTED'},
  {node_id:'clinic',kind:'CARE_PATHWAY',state:'ADOPTED'},
  {node_id:'prediction',kind:'FEATURE',state:'PROPOSED'},
  {node_id:'memory',kind:'MEMORY',state:'ADOPTED'},
  {node_id:'document',kind:'DOCUMENT',state:'ADOPTED'},
  {node_id:'feat:work',kind:'FEATURE',state:'ADOPTED'},
  {node_id:'app',kind:'APP',state:'ADOPTED'},
]}}}, homeAllTodos:()=>[{node_ref:'work'}], homeNodeRefKey:id=>id.replace(/^feat:/,''), homeNodeTodos:n=>n.node_id==='feat:work'?[{state:'IN_PROGRESS'}]:[] };
vm.createContext(context);
vm.runInContext(source.slice(start,end),context);
assert.deepEqual(Array.from(context.homeNodes(),n=>n.node_id),['feat:work','app','clinic']);
assert.match(source.slice(source.indexOf('async function submitHomeNode('),source.indexOf('// + Todo',source.indexOf('async function submitHomeNode('))),/invokeServerAction\("feature.create"/);
console.log('Fleet structural/domain visibility, prediction exclusion, work ranking, duplicate suppression and manual Action routing passed.');
