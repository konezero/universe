// Run: node tests/test_hover_memory_ui.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('tools/universe_ui/app.js', 'utf8');
class Element {
  constructor(tag, css, text) { this.tag = tag; this.children = []; this.textContent = text || ''; this.isConnected = true; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  setAttribute() {}
  addEventListener() {}
  showModal() {}
  close() { this.isConnected = false; }
  remove() { this.isConnected = false; }
  querySelectorAll(tag) { return this.children.flatMap(child => [...(child.tag === tag ? [child] : []), ...child.querySelectorAll(tag)]); }
}
let candidate = { candidate_id: 'c1', project_id: 'project-a', candidate_digest: 'digest1', kind: 'MEMORY', state: 'REVIEW_REQUIRED', summary: 'Test memory', decision_contract: { allowed_actions: ['KEEP','IGNORE'], next_action: {kind:'NONE'} } };
let memories = [], calls = [], fail = false;
const body = new Element('body');
const context = {
  Map, Set, Date, console, document: {body},
  node: (...args) => new Element(...args), markdownBody: text => new Element('p','',text),
  closeDocumentHover() {}, toast() {},
  memoryCandidateActionSpecs: c => c.decision_contract.allowed_actions.map(id => ({id})),
  memoryCandidateNextAction: c => c.decision_contract.next_action,
  memoryCandidateReviewPayload: (c,a) => ({candidate_id:c.candidate_id, decision:a.id}),
  async api(path, options) {
    calls.push([path, options]);
    if (options) {
      assert.equal(path, '/v1/projects/project-a/memory-candidates/review');
      if (fail) throw new Error('Review conflict');
      assert.deepEqual(JSON.parse(JSON.stringify(options.body)), {candidate_id:'c1',decision:'KEEP'});
      candidate = {...candidate, state:'KEEP', decision_contract:{allowed_actions:[], next_action:{kind:'RAG_ADOPT_AVAILABLE'}}};
      return {candidate, status:'REVIEWED'};
    }
    return path.endsWith('/memories') ? {memories} : {candidates:[candidate]};
  },
  async invokeServerAction(id, request) {
    assert.equal(id, 'rag.adopt');
    assert.equal(request.expected_candidate_digest, 'digest1');
    assert.equal(request.candidate_id, 'c1');
    memories = [{memory_id:'m1',project_id:'project-a',title:'Test memory',body:'Test memory',link_state:'UNLINKED',origin_ref:'universe://memory-candidates/digest1/c1'}];
    return {candidate, memory:memories[0], status:'RAG_MEMORY_ADOPTED'};
  },
};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('const hoverKnowledgeCache ='),source.indexOf('let documentHoverTimer =')),context);
(async () => {
  const knowledge = {memories:[{memory_id:'linked',node_ref:'n1',link_state:'LINKED'},{memory_id:'proposed',node_ref:'n2',link_state:'PROPOSED'},{memory_id:'free',node_ref:null}],candidates:[candidate]};
  const related = context.relatedKnowledgeForNode({kind:'system',data:{node_id:'n1'}}, knowledge);
  assert.equal(related.memories.length,1); assert.equal(related.memories[0].memory_id,'linked'); assert.equal(related.candidates.length,0);
  assert.equal(context.relatedKnowledgeForNode({kind:'project'},knowledge).candidates.length,1);
  assert.equal(context.knowledgeStatus(candidate,true,[]).tone,'pending');
  await context.openHoverMemory('project-a',candidate,true);
  const dialog = body.children.at(-1);
  const buttons = () => dialog.querySelectorAll('button');
  fail = true;
  await buttons().find(b=>b.textContent==='KEEP').onclick();
  assert.equal(buttons().find(b=>b.textContent==='Adopt to RAG'),undefined);
  fail = false;
  await buttons().find(b=>b.textContent==='KEEP').onclick();
  assert.equal(candidate.state,'KEEP');
  assert.equal(memories.length,0);
  await buttons().find(b=>b.textContent==='Adopt to RAG').onclick();
  assert.equal(memories.length,1);
  assert.equal(buttons().find(b=>b.textContent==='Adopt to RAG'),undefined);
  assert.equal(context.relatedKnowledgeForNode({kind:'project'},{memories,candidates:[candidate]}).candidates.length,0);
  console.log('PASS node scoping, proposed/unlinked separation, failed review, KEEP, governed adoption, adopted deduplication');
})().catch(error => {console.error(error);process.exitCode=1;});
