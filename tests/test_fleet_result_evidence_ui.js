const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
const source = fs.readFileSync('tools/universe_ui/app.js', 'utf8');
const start = source.indexOf('function homeTodoResultCacheKey(');
const end = source.indexOf('function renderHomeKanban(', start);

function fakeNode(tag, className, text) {
  const el = { tag, className, text, children: [], type: '', clickHandlers: [] };
  el.append = (...kids) => { el.children.push(...kids); };
  el.addEventListener = (evt, fn) => { if (evt === 'click') el.clickHandlers.push(fn); };
  el.click = () => el.clickHandlers.forEach((fn) => fn());
  return el;
}
function flatText(el) {
  const own = el.text || '';
  return [own, ...el.children.map(flatText)].join('|');
}
function findButton(el, textPattern) {
  if (el.tag === 'button' && textPattern.test(el.text || '')) return el;
  for (const child of el.children) {
    const hit = findButton(child, textPattern);
    if (hit) return hit;
  }
  return null;
}

const apiCalls = [];
let apiResult = null;
let apiShouldThrow = false;
const context = {
  state: { homeTodoResultCache: {}, homeTodoResultPending: new Set(), homeTodoId: 'todo-1' },
  node: fakeNode,
  api: async (path) => {
    apiCalls.push(path);
    if (apiShouldThrow) throw new Error('network down');
    return apiResult;
  },
  renderIntegratedHome: () => { context.rerendered = true; },
  rerendered: false,
};
vm.createContext(context);
vm.runInContext(source.slice(start, end), context);

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

(async () => {
  // Non-terminal states never fetch or render a result section.
  assert.equal(context.renderHomeTodoResult({ todo_id: 'todo-1', revision: 1, state: 'IN_PROGRESS' }), null);
  assert.equal(apiCalls.length, 0);

  // First render of a DONE todo: cache miss triggers a background fetch and
  // shows a loading placeholder; it must not fetch twice while pending.
  apiResult = { history: { actions: [
    { action: { outcome: 'COMPLETED', state: 'DONE', validation: { status: 'PASSED', evidence_ref: 'test://evidence-1' } } },
  ] } };
  const loading = context.renderHomeTodoResult({ todo_id: 'todo-1', revision: 1, state: 'DONE' });
  assert.match(flatText(loading), /불러오는 중/);
  await settle();
  assert.equal(apiCalls.length, 1);
  assert.equal(apiCalls[0], '/v1/todos/todo-1/actions');
  assert.ok(context.rerendered, 're-renders once the fetch resolves');

  // Second render (same revision) reads the cache — no repeat fetch, evidence text present.
  const filled = context.renderHomeTodoResult({ todo_id: 'todo-1', revision: 1, state: 'DONE' });
  assert.equal(apiCalls.length, 1);
  assert.match(flatText(filled), /test:\/\/evidence-1/);
  assert.match(flatText(filled), /COMPLETED/);
  // provider성공/검증통과/제품완료: three distinct rows, each reusing an
  // existing signal — Provider 실행 has none available, so it must read
  // UNKNOWN rather than be inferred from the other two.
  assert.match(flatText(filled), /제품 완료/);
  assert.match(flatText(filled), /검증 통과/);
  assert.match(flatText(filled), /통과/);
  assert.match(flatText(filled), /Provider 실행/);
  assert.match(flatText(filled), /UNKNOWN/);

  // A todo with no recorded validation renders an explicit empty state, not
  // a silent blank — and still caches to avoid refetching every render.
  apiResult = { history: { actions: [] } };
  context.rerendered = false;
  const empty = context.renderHomeTodoResult({ todo_id: 'todo-2', revision: 1, state: 'BLOCKED' });
  await settle();
  assert.equal(apiCalls.length, 2);
  const emptyFilled = context.renderHomeTodoResult({ todo_id: 'todo-2', revision: 1, state: 'BLOCKED' });
  assert.equal(apiCalls.length, 2);
  assert.match(flatText(emptyFilled), /기록된 완료 근거가 없습니다/);

  // A network failure must render as a distinct error state — never as
  // "no evidence recorded" — and must not silently retry on every render.
  apiCalls.length = 0;
  apiShouldThrow = true;
  const failing = context.renderHomeTodoResult({ todo_id: 'todo-3', revision: 1, state: 'DONE' });
  await settle();
  assert.equal(apiCalls.length, 1);
  const failed = context.renderHomeTodoResult({ todo_id: 'todo-3', revision: 1, state: 'DONE' });
  assert.equal(apiCalls.length, 1, 'a failed fetch must not retry on every render');
  assert.match(flatText(failed), /결과를 불러오지 못했습니다/);
  assert.doesNotMatch(flatText(failed), /기록된 완료 근거가 없습니다/, 'failure must never be shown as "no evidence"');

  // Retry recovers once the transport succeeds again.
  apiShouldThrow = false;
  apiResult = { history: { actions: [
    { action: { outcome: 'FAILED', state: 'BLOCKED', validation: { status: 'PASSED', evidence_ref: 'test://evidence-recovered' } } },
  ] } };
  const retryButton = findButton(failed, /다시 시도/);
  assert.ok(retryButton, 'a failed fetch offers an explicit retry control');
  retryButton.click();
  await settle();
  assert.equal(apiCalls.length, 2, 'retry re-issues the fetch');
  const recovered = context.renderHomeTodoResult({ todo_id: 'todo-3', revision: 1, state: 'DONE' });
  assert.match(flatText(recovered), /test:\/\/evidence-recovered/);

  // Same todo_id, new revision (e.g. after a state change elsewhere bumped
  // it): must not reuse the stale revision's cached result.
  apiCalls.length = 0;
  apiResult = { history: { actions: [
    { action: { outcome: 'COMPLETED', state: 'DONE', validation: { status: 'PASSED', evidence_ref: 'test://evidence-revision-2' } } },
  ] } };
  const staleCheck = context.renderHomeTodoResult({ todo_id: 'todo-1', revision: 2, state: 'DONE' });
  assert.match(flatText(staleCheck), /불러오는 중/, 'a new revision is a cache miss, not the old revision\'s result');
  await settle();
  assert.equal(apiCalls.length, 1);
  const revised = context.renderHomeTodoResult({ todo_id: 'todo-1', revision: 2, state: 'DONE' });
  assert.match(flatText(revised), /test:\/\/evidence-revision-2/);
  // The old revision's cache entry is untouched by the new revision's fetch.
  const stillOldRevision = context.renderHomeTodoResult({ todo_id: 'todo-1', revision: 1, state: 'DONE' });
  assert.match(flatText(stillOldRevision), /test:\/\/evidence-1/);

  console.log('Fleet Todo 상세 결과 연결: 실패≠빈근거, 재시도 복구, 동일 TODO revision 갱신 회귀 방지 passed');
})().catch((e) => { console.error(e); process.exitCode = 1; });
