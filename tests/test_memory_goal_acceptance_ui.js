const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('tools/universe_ui/app.js', 'utf8');
const start = source.indexOf('function memoryCandidateCanBecomeGoal(');
const end = source.indexOf('function memoryCandidateActionSpecs(', start);
assert.ok(start >= 0 && end > start);
const calls = [];
const loads = [];
const state = {selectedProject: {project_id: 'universe'}};
const context = {
  state,
  invokeServerAction: async (action, request) => {
    calls.push({action, request});
    return {goal: {goal_id: 'goal_created', project_id: 'universe'}};
  },
  refreshGoalPlan: async () => {},
  loadGalaxyGoalProposals: async (projectId) => { loads.push(projectId); },
};
vm.createContext(context);
vm.runInContext(source.slice(start, end), context);
const idea = {candidate_id:'candidate_1',project_id:'universe',kind:'MEMORY',state:'REVIEW_REQUIRED',
  candidate_digest:'a'.repeat(64),knowledge:{kind:'USER_IDEA'}};
assert.equal(context.memoryCandidateCanBecomeGoal(idea), true);
assert.equal(context.memoryCandidateCanBecomeGoal({...idea, knowledge:{kind:'REUSABLE_PROCEDURE'}}), false);
assert.equal(context.memoryCandidateCanBecomeGoal({...idea, state:'IGNORE'}), false);
assert.equal(context.memoryCandidateCanBecomeGoal({...idea,kind:'PRODUCT',knowledge:null}), true);
(async () => {
  const result = await context.acceptGoalCandidate(idea);
  assert.equal(result.goal.goal_id,'goal_created');
  assert.equal(calls.length,1);
  assert.equal(calls[0].action,'goal.accept-proposal');
  assert.equal(calls[0].request.project_id,'universe');
  assert.equal(calls[0].request.candidate_id,'candidate_1');
  assert.equal(calls[0].request.expected_candidate_digest,'a'.repeat(64));
  assert.deepEqual(loads, ['universe'], 'acceptance reloads a potentially in-flight proposal page');
  state.selectedProject = {project_id: 'other'};
  await assert.rejects(context.acceptGoalCandidate(idea), /different selected project/);
  assert.equal(calls.length, 1, 'a stale card cannot accept into a background project');
  state.selectedProject = {project_id: 'universe'};
  context.invokeServerAction = async () => {
    state.selectedProject = {project_id: 'other'};
    return {goal: {goal_id: 'goal_late', project_id: 'universe'}};
  };
  await context.acceptGoalCandidate(idea);
  assert.deepEqual(loads, ['universe'], 'a late acceptance does not reload another project');
  console.log('Memory/Galaxy proposal Goal acceptance rejects stale cards and scopes late reloads.');
})().catch(error => {console.error(error);process.exitCode=1;});
