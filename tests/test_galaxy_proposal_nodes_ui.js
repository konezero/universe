const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('tools/universe_ui/app.js', 'utf8');
const start = source.indexOf('function galaxyProposalGraphNodes(');
const end = source.indexOf('function buildUnifiedGalaxyGraph(', start);
assert.ok(start >= 0 && end > start);
const context = {};
vm.createContext(context);
vm.runInContext(source.slice(start, end), context);
const make = (id, kind, project = 'universe', state = 'REVIEW_REQUIRED') => ({
  candidate_id: id, project_id: project, kind, state,
  summary: `Proposal ${id}`,
});
const projected = context.galaxyProposalGraphNodes('universe', [
  make('idea_1', 'IDEA'), make('prediction_1', 'HYPOTHESIS'),
  make('idea_2', 'MEMORY', 'universe', 'KEEP'),
  {...make('idea_3', 'MEMORY'), knowledge: {kind: 'USER_IDEA', topic: 'Future route'}},
  make('rag_1', 'MEMORY'), make('other_1', 'IDEA', 'other'),
]);
assert.deepEqual(Array.from(projected, (item) => item.node_id), [
  'proposal:idea_1', 'proposal:prediction_1', 'proposal:idea_3',
]);
assert.equal(projected[2].title, 'Future route');
assert.equal(projected[2].state, 'REVIEW_REQUIRED');
console.log('Galaxy projects only project-local proposal candidates, not operational RAG.');
