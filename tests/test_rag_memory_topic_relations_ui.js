const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('tools/universe_ui/app.js', 'utf8');
const start = source.indexOf('const RAG_RELATION_LABELS');
const end = source.indexOf('function groupRagKnowledgeTopics(');
const ctx = {};
vm.createContext(ctx);
vm.runInContext(source.slice(start, end), ctx);

// 2026-09-14: topic/relation classification is server-owned data
// (rag.memory-relate), not a UI-hardcoded id table. These tests use made-up
// memory ids that exist nowhere in the real project, to prove the behavior
// is generic — any project's memories get it with zero source changes —
// rather than asserting a fixed constant list back at itself.

// A memory whose knowledge.topic is set (server data) always wins over the
// keyword-inference hints, for ANY id — nothing here is memory-id-specific.
const explicit1 = ctx.ragTopicFor({memory_id: 'memory_zzz001', title: '', body: '', knowledge: {topic: '임의 주제 A', kind: 'USER_DECISION', applicability: 'x'}});
assert.equal(explicit1.name, '임의 주제 A');
assert.equal(explicit1.inferred, false);
const explicit2 = ctx.ragTopicFor({memory_id: 'memory_zzz002', title: '', body: 'Fleet kanban node stuff', knowledge: {topic: '임의 주제 A', kind: 'USER_DECISION', applicability: 'x'}});
assert.equal(explicit2.name, '임의 주제 A', 'explicit knowledge.topic overrides the keyword hint that would otherwise fire on this body text');

// A memory with no knowledge.topic still falls back to keyword inference,
// unaffected — this is a targeted addition, not a classifier replacement.
const fallback = ctx.ragTopicFor({memory_id: 'memory_zzz003', title: '', body: '완전히 상관없는 아무 내용'});
assert.equal(fallback.inferred, true);

// memoryRelationNotes reads item.relations (a plain array on the memory
// object, exactly as the server returns it) and resolves the other side of
// each edge against a supplied lookup map — no memory-id table anywhere.
const memoryById = new Map([
  ['memory_rep', {memory_id: 'memory_rep', title: '대표 후보'}],
  ['memory_child', {memory_id: 'memory_child', title: '보완 후보'}],
]);
const childNotes = ctx.memoryRelationNotes(
  {memory_id: 'memory_child', relations: [
    {memory_id: 'memory_child', target_memory_id: 'memory_rep', relation: 'COMPLEMENTS', note: '테스트 근거 문장'},
  ]},
  memoryById
);
assert.equal(childNotes.length, 1);
assert.match(childNotes[0], /보완/);
assert.match(childNotes[0], /대표 후보/);
assert.match(childNotes[0], /테스트 근거 문장/);

// The representative side sees the same edge from the other direction
// (memory_id !== item.memory_id) and is labeled as the target of the
// relation, not merged into the child's claim text.
const repNotes = ctx.memoryRelationNotes(
  {memory_id: 'memory_rep', relations: [
    {memory_id: 'memory_child', target_memory_id: 'memory_rep', relation: 'COMPLEMENTS', note: '테스트 근거 문장'},
  ]},
  memoryById
);
assert.equal(repNotes.length, 1);
assert.match(repNotes[0], /대표/);
assert.match(repNotes[0], /보완 후보/);

// A memory with no relations at all (the overwhelmingly common case) gets no
// notes — this must never fabricate a relationship.
assert.equal(ctx.memoryRelationNotes({memory_id: 'memory_lonely', relations: []}, memoryById).length, 0);
assert.equal(ctx.memoryRelationNotes({memory_id: 'memory_lonely'}, memoryById).length, 0, 'relations field may be entirely absent');

// Unknown relation kinds still render (fail open with the raw code) instead
// of silently dropping the edge.
const unknownKind = ctx.memoryRelationNotes(
  {memory_id: 'memory_child', relations: [
    {memory_id: 'memory_child', target_memory_id: 'memory_rep', relation: 'SOMETHING_NEW', note: 'n'},
  ]},
  memoryById
);
assert.match(unknownKind[0], /SOMETHING_NEW/);

console.log('PASS RAG 메모 주제/관계: 서버 데이터(knowledge.topic, relations[]) 기반 렌더, 임의 ID로 일반성 검증, 무관계 항목 비생성');
