const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('tools/universe_ui/app.js', 'utf8');
const start = source.indexOf('function featureProposalEvidenceItems(');
const end = source.indexOf('function appendFeatureProposalEvidence(', start);
assert.ok(start >= 0 && end > start);
const state = {
  memories: [], memoryCandidates: [], memoryCandidatesStatus: 'ERROR',
  goalProposals: [{candidate_id:'idea_1', kind:'IDEA', stage:'SYNTHESIZE',
    state:'REVIEW_REQUIRED', summary:'A source-grounded proposal'}],
  workLoop: null,
};
const context = {state, truncate: text => text};
vm.createContext(context);
vm.runInContext(source.slice(start, end), context);
const evidence = context.featureProposalEvidenceItems({evidence_refs:[
  'universe://memory-candidates/digest/idea_1',
  'universe://memory-candidates/digest/missing_1',
]});
assert.equal(evidence[0].title, 'A source-grounded proposal');
assert.match(evidence[1].meta, /UNKNOWN.*lookup failed/);
assert.equal(evidence[1].hasSourceBody, false);
state.memoryCandidatesStatus = 'LOADING';
assert.match(context.featureProposalEvidenceItems({evidence_refs:[
  'universe://memory-candidates/digest/missing_1',
]})[0].meta, /lookup pending/);
state.memoryCandidatesStatus = 'READY';
assert.match(context.featureProposalEvidenceItems({evidence_refs:[
  'universe://memory-candidates/digest/missing_1',
]})[0].meta, /outside loaded candidate page/);
console.log('Feature proposal evidence uses the project proposal source and reports failed candidate lookup.');
