const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('tools/universe_ui/app.js', 'utf8');
const start = source.indexOf('const todoAutomationNextStepLabels');
const end = source.indexOf('async function selectTodoForGoalExecution(');
const ctx = { state: { goalAutomationSurfaces: {} } };
vm.createContext(ctx);
vm.runInContext(source.slice(start, end), ctx);

// 2026-09-14 finding: the server already computes exactly why a Goal cannot
// proceed (BLOCKED_GOAL_NOT_PLAN_ELIGIBLE -> diagnostic.reasons[] +
// next_action), but the UI's fallback branch collapsed it to a generic
// "Goal automation is X; this gate is not bypassed" string, discarding the
// reasons and the recommended next action. Both Fleet and the flat Todo list
// call this same projection, so fixing it here fixes both screens at once.
const todo = { todo_id: 't1', goal_id: 'g1', state: 'IN_PROGRESS' };
ctx.state.goalAutomationSurfaces.g1 = {
  goal: { goal_id: 'g1', revision: 3 },
  automation_state: 'BLOCKED_GOAL_NOT_PLAN_ELIGIBLE',
  next_operation: 'USER_RESOLUTION_REQUIRED',
  todo_execution: { eligible_todo_ids: [], selection: null },
  diagnostic: {
    code: 'GOAL_NOT_WORK_PLAN_ELIGIBLE',
    reasons: ['Goal state is ACTIVE, not DESIGNING', 'Goal has no adopted Feature Expected Path derivation'],
    next_action: 'Reshape the Goal to a DESIGNING, Feature-path-derived Goal (or adopt a Work Plan candidate) before automation.',
  },
};
const projection = ctx.todoAutomationControlProjection(todo);
assert.equal(projection.code, 'GOAL_NOT_WORK_PLAN_ELIGIBLE');
assert.match(projection.detail, /Goal state is ACTIVE, not DESIGNING/);
assert.match(projection.detail, /Goal has no adopted Feature Expected Path derivation/);
assert.match(projection.detail, /Reshape the Goal to a DESIGNING/);
assert.equal(projection.actionable, false);

// A surface with no diagnostic (the common WAIT / normal-progress case) must
// keep the original concise fallback, not regress into always demanding a
// diagnostic that may not exist.
ctx.state.goalAutomationSurfaces.g1 = {
  goal: { goal_id: 'g1', revision: 3 },
  automation_state: 'MASTER_HANDOFF_UPDATE_READY',
  next_operation: 'WAIT',
  todo_execution: { eligible_todo_ids: [], selection: null },
  diagnostic: null,
};
const waiting = ctx.todoAutomationControlProjection(todo);
assert.equal(waiting.detail, 'Goal automation is MASTER_HANDOFF_UPDATE_READY; this gate is not bypassed.');

console.log('PASS Fleet/Todo 목록 Goal 자동화 진단(BLOCKED_GOAL_NOT_PLAN_ELIGIBLE reasons/next_action) 표시, 무진단 상태 회귀 없음');
