const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('tools/universe_ui/app.js', 'utf8');
const start = source.indexOf('async function submitHomeNode(');
const end = source.indexOf('// + Todo', start);
assert.ok(start >= 0 && end > start);

async function run(workstream) {
  const calls = [];
  const form = {elements: {intent_text: {value: 'A finite Goal with Todos'}}, dataset: {}};
  const error = {textContent: ''};
  const submit = {disabled: false};
  const dialog = {closed: false, close() { this.closed = true; }};
  const projectId = 'universe';
  const goalId = 'goal_abcdef';
  const context = {
    state: {selectedProject: {project_id: projectId}, homeWorkstreamKind: workstream,
      goals: [], projectionsByProject: {}},
    document: {querySelector: id => ({'#home-node-error': error,
      '#home-node-submit': submit, '#home-node-dialog': dialog})[id]},
    crypto: {randomUUID: () => 'abcdef'},
    toast: () => {},
    renderIntegratedHome: () => {},
    selectProject: async () => {},
    invokeServerAction: async (action, body) => {
      calls.push({action, body});
      return {feature: {feature_id: 'feature_1'}};
    },
    api: async (path, options) => {
      calls.push({path, options});
      return {goal: {goal_id: goalId, project_id: projectId, title: 'A finite Goal with Todos'}};
    },
  };
  vm.createContext(context);
  vm.runInContext(source.slice(start, end), context);
  await context.submitHomeNode({preventDefault() {}, target: form});
  assert.equal(error.textContent, '');
  assert.equal(submit.disabled, false);
  assert.equal(dialog.closed, true);
  return {calls, state: context.state};
}

(async () => {
  const development = await run('DEVELOPMENT');
  assert.equal(development.calls.length, 1);
  assert.equal(development.calls[0].path, '/v1/projects/universe/goals');
  assert.equal(development.calls[0].options.body.scope_kind, 'PROJECT');
  assert.equal(development.state.homeNodeId, 'goal:goal_abcdef');
  const operations = await run('OPERATIONS');
  assert.equal(operations.calls.length, 1);
  assert.equal(operations.calls[0].action, 'feature.create');
  assert.equal(operations.calls[0].body.feature.workstream_kind, 'OPERATIONS');
  assert.equal(operations.state.homeNodeId, 'feat:feature_1');
  console.log('Fleet Goal registration and separate Operations Feature registration passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
