const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('tools/universe_ui/app.js', 'utf8');
const start = source.indexOf('async function submitHomeNode(');
const end = source.indexOf('// + Todo', start);
assert.ok(start >= 0 && end > start);

async function run(workstream, predecessorGoalId = null, expectedError = false) {
  const calls = [];
  const form = {elements: {intent_text: {value: 'A finite Goal with Todos'}},
    dataset: predecessorGoalId ? {predecessorGoalId, pendingProjectId:'universe'} : {}};
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
      return action === 'goal.create-follow-up'
        ? {goal: {goal_id:'goal_abcdef', project_id:projectId}}
        : {feature: {feature_id: 'feature_1'}};
    },
    api: async (path, options) => {
      calls.push({path, options});
      return {goal: {goal_id: goalId, project_id: projectId, title: 'A finite Goal with Todos'}};
    },
  };
  vm.createContext(context);
  vm.runInContext(source.slice(start, end), context);
  await context.submitHomeNode({preventDefault() {}, target: form});
  if (expectedError) {
    assert.match(error.textContent, /context changed/);
    assert.equal(dialog.closed, false);
  } else {
    assert.equal(error.textContent, '');
    assert.equal(dialog.closed, true);
  }
  assert.equal(submit.disabled, false);
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
  const followup = await run('DEVELOPMENT', 'goal_prior');
  assert.equal(followup.calls.length, 1);
  assert.equal(followup.calls[0].action, 'goal.create-follow-up');
  assert.equal(followup.calls[0].body.predecessor_goal_id, 'goal_prior');
  assert.equal(followup.calls[0].body.goal_id, 'goal_abcdef');
  assert.equal(followup.state.homeNodeId, 'goal:goal_abcdef');
  const switched = await run('OPERATIONS', 'goal_prior', true);
  assert.equal(switched.calls.length, 0, 'switched workstream cannot create a Feature instead');
  console.log('Fleet Goal registration, follow-up lineage, and separate Operations registration passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
