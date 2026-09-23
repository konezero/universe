const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('tools/universe_ui/app.js', 'utf8');
const start = source.indexOf('async function loadGalaxyGoalProposals(');
const end = source.indexOf('function galaxyProposalGraphNodes(', start);
assert.ok(start >= 0 && end > start);

async function run(pages) {
  const state = {selectedProject: {project_id: 'universe'}, view: 'semantic', goalProposalsError: 'prior failure'};
  const calls = [];
  let renders = 0;
  const context = {
    state, encodeURIComponent, elements: {memoryPanel: null},
    api: async (path) => { calls.push(path); const next = pages.shift();
      if (next instanceof Error) throw next; return next; },
    buildGraph: () => { renders += 1; },
  };
  vm.createContext(context);
  vm.runInContext(source.slice(start, end), context);
  await context.loadGalaxyGoalProposals('universe');
  return {state, calls, renders};
}

(async () => {
  const first = {project_id:'universe', offset:0, has_more:true, next_offset:1,
    proposals:[{candidate_id:'idea_1', project_id:'universe'}]};
  const second = {project_id:'universe', offset:1, has_more:false, next_offset:2,
    proposals:[{candidate_id:'idea_2', project_id:'universe'}]};
  const ok = await run([first, second]);
  assert.equal(ok.state.goalProposalsStatus, 'READY');
  assert.equal(ok.state.goalProposalsError, null);
  assert.deepEqual(Array.from(ok.state.goalProposals, p => p.candidate_id), ['idea_1','idea_2']);
  assert.equal(ok.calls.length, 2);
  assert.equal(ok.renders, 1);
  const bad = await run([first, new Error('transport down')]);
  assert.equal(bad.state.goalProposalsStatus, 'ERROR');
  assert.equal(bad.state.goalProposals, null);
  assert.match(bad.state.goalProposalsError, /transport down/);
  assert.equal(bad.renders, 1);
  const pending = [];
  const raceState = {selectedProject:{project_id:'universe'}, view:'semantic'};
  const raceContext = {state:raceState, encodeURIComponent, elements:{memoryPanel:null},
    api: () => new Promise(resolve => pending.push(resolve)), buildGraph: () => {}};
  vm.createContext(raceContext);
  vm.runInContext(source.slice(start, end), raceContext);
  const staleLoad = raceContext.loadGalaxyGoalProposals('universe');
  const acceptedLoad = raceContext.loadGalaxyGoalProposals('universe');
  pending[1]({project_id:'universe', offset:0, has_more:false, next_offset:1,
    proposals:[{candidate_id:'idea_1', project_id:'universe', goal_acceptance:{goal_id:'goal_1'}}]});
  await acceptedLoad;
  pending[0]({project_id:'universe', offset:0, has_more:false, next_offset:1,
    proposals:[{candidate_id:'idea_1', project_id:'universe', goal_acceptance:null}]});
  await staleLoad;
  assert.equal(raceState.goalProposals[0].goal_acceptance.goal_id, 'goal_1');
  assert.equal(raceState.goalProposalsStatus, 'READY');
  console.log('Galaxy proposal pagination, error state, and stale-response isolation passed.');
})().catch(error => { console.error(error); process.exitCode=1; });
