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
  Map, Set, Date, console, state: {view:"documents"}, document: {body, createElement: tag => new Element(tag)}, Option: function(text,value) {return new Element("option", "", text);},
  node: (...args) => new Element(...args), markdownBody: text => new Element('p','',text),
  closeDocumentHover() {}, toast() {},
  memoryCandidateCanBecomeGoal: c => c.knowledge?.kind === 'USER_IDEA' || ['IDEA','HYPOTHESIS','PRODUCT'].includes(c.kind),
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
    if (path.endsWith("/projection")) return {projection:{nodes:[]}};
    return path.includes('/memories') ? {memories} : {candidates:[candidate]};
  },
  async invokeServerAction(id, request) {
    if (id === 'rag.memory-retention') {
      assert.equal(request.memory_id, 'm1');
      memories = memories.map(m => ({...m, retention:{decision:request.decision, revision:request.expected_revision+1, note:request.note}}));
      return {memory:memories[0],status:'MEMORY_RETENTION_RECORDED'};
    }
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
  candidate.knowledge = {kind:'USER_DECISION',topic:'Collection policy',applicability:'During extraction'};
  memories = [{memory_id:'saved',project_id:'project-a',origin_ref:'universe://memory-candidates/digest1/c1'}];
  const enriched = await context.loadHoverKnowledge('project-a',true);
  assert.equal(enriched.memories[0].knowledge.topic,'Collection policy');
  assert.equal(memories[0].knowledge,undefined);
  memories = [{memory_id:'other',project_id:'project-a',origin_ref:'universe://memory-candidates/changed/c1'}];
  assert.equal((await context.loadHoverKnowledge('project-a',true)).memories[0].knowledge,undefined);
  memories = [];
  const knowledge = {memories:[{memory_id:'linked',node_ref:'n1',link_state:'LINKED'},{memory_id:'proposed',node_ref:'n2',link_state:'PROPOSED'},{memory_id:'free',node_ref:null}],candidates:[candidate]};
  const related = context.relatedKnowledgeForNode({kind:'system',data:{node_id:'n1'}}, knowledge);
  assert.equal(related.memories.length,1); assert.equal(related.memories[0].memory_id,'linked'); assert.equal(related.candidates.length,0);
  assert.equal(context.relatedKnowledgeForNode({kind:'project'},knowledge).candidates.length,1);
  assert.equal(context.knowledgeStatus(candidate,true,[]).tone,'pending');
  await context.openHoverMemory('project-a',candidate,true);
  const dialog = body.children.at(-1);
  const buttons = () => dialog.querySelectorAll('button');
  fail = true;
  await buttons().find(b=>b.textContent==='채택 대상으로 확인').onclick();
  assert.equal(buttons().find(b=>b.textContent==='RAG에 채택'),undefined);
  fail = false;
  await buttons().find(b=>b.textContent==='채택 대상으로 확인').onclick();
  assert.equal(candidate.state,'KEEP');
  assert.equal(memories.length,0);
  await buttons().find(b=>b.textContent==='RAG에 채택').onclick();
  assert.equal(memories.length,1);
  assert.equal(buttons().find(b=>b.textContent==='RAG에 채택'),undefined);
  assert.equal(context.relatedKnowledgeForNode({kind:'project'},{memories,candidates:[candidate]}).candidates.length,0);
  await buttons().find(b=>b.textContent==='무시 — RAG에서 제외').onclick();
  assert.equal(memories[0].retention.decision,'IGNORE');
  assert.equal(context.relatedKnowledgeForNode({kind:'project'},{memories,candidates:[candidate]}).memories.length,0);
  assert.equal(buttons().find(b=>b.textContent==='노드 연결 확정'),undefined);
  await buttons().find(b=>b.textContent==='메모 복원').onclick();
  assert.equal(memories[0].retention.decision,'ACTIVE');
  console.log('PASS node scoping, candidate adoption, saved memory exclusion/restoration through governed action, hidden links');
})().catch(error => {console.error(error);process.exitCode=1;});
