const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('tools/universe_ui/app.js','utf8');
const ctx = {};vm.createContext(ctx);
vm.runInContext(source.slice(source.indexOf('function ragChangedInWindow('),source.indexOf('function renderMemory()')),ctx);
const now = new Date(2026,8,12,12,0,0);
const start = new Date(2026,8,12).getTime();
const stamp = t => new Date(t).toISOString();
const records = {candidates:[
 {created_at:stamp(start),updated_at:stamp(start+1000)},
 {created_at:stamp(start-1000),updated_at:stamp(start+2000)},
 {created_at:stamp(start-2000),updated_at:stamp(start-1000)},
 {created_at:'invalid'},
 {created_at:stamp(now.getTime()+1000)},
],memories:[{created_at:stamp(start+3000)},{created_at:stamp(start-1),updated_at:stamp(start+4000)}]};
const before = JSON.stringify(records);
const r=ctx.summarizeRagDay(records,now);
assert.equal(r.newCandidates,1);assert.equal(r.updatedCandidates,1);
assert.equal(r.newMemories,1);assert.equal(r.updatedMemories,1);
assert.equal(r.unknownDates,1);assert.equal(r.changedCandidates.length,2);
assert.equal(JSON.stringify(records),before);
assert.equal(ctx.ragChangedInWindow({created_at:stamp(start-1)},start,now.getTime()),false);
assert.equal(ctx.ragChangedInWindow({updated_at:stamp(now.getTime())},start,now.getTime()),true);
assert.equal(ctx.summarizeRagDay({candidates:[],memories:[]},now).newCandidates,0);
console.log('PASS local midnight, update versus create, invalid/future timestamps, inclusive now, empty input and no mutation');
