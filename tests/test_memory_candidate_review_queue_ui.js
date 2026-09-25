const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("tools/universe_ui/app.js", "utf8");
const ctx = {};
vm.createContext(ctx);
vm.runInContext(
  source.slice(source.indexOf("function candidateDisplayTitle("), source.indexOf("function renderMemoryRelateEditor(")),
  ctx
);

const base = (id, extra = {}) => ({
  project_id: "p",
  candidate_id: id,
  kind: "MEMORY",
  stage: "FAST_EXTRACT",
  state: "REVIEW_REQUIRED",
  summary: "같은 내용",
  created_at: "2026-09-23T09:00:00Z",
  provenance: {
    source_session: "session-alpha",
    source_range: { start: 10, end: 20 },
    ref_digests: ["digest-1"],
  },
  relations: [],
  ...extra,
});

const duplicateRows = [
  base("a"),
  base("b", {
    stage: "CONSOLIDATE",
    relations: [{ relation: "DUPLICATE_OF", target_candidate_id: "a" }],
  }),
  base("ignored", { state: "IGNORE" }),
  base("accepted", { state: "KEEP" }),
];
const groups = ctx.groupRagReviewCandidates(duplicateRows);
assert.equal(groups.length, 2);
const pendingGroup = groups.find((group) => group.items.some((item) => item.candidate_id === "a"));
assert.equal(pendingGroup.items.length, 2);
assert.deepStrictEqual(Array.from(pendingGroup.candidateIds), ["a", "b"]);
assert.ok(pendingGroup.reasons.includes("동일 내용 묶음"));
assert.equal(pendingGroup.evidence[1].duplicateCount, 1);
assert.equal(groups.find((group) => group.items[0].candidate_id === "accepted").items[0].state, "KEEP");

const empty = base("raw-42", { summary: "   ", title: "" });
assert.equal(ctx.candidateDisplayTitle(empty), "제목 없음 · 원시 ID raw-42");
assert.ok(ctx.candidateReviewEvidence(empty).reason.includes("검토 대기"));

const before = JSON.stringify(duplicateRows);
ctx.groupRagReviewCandidates(duplicateRows);
assert.equal(JSON.stringify(duplicateRows), before);

const acceptedOnly = ctx.groupRagReviewCandidates([
  base("keep", { state: "KEEP" }),
  base("ignore", { state: "IGNORE" }),
]);
assert.equal(acceptedOnly.length, 1);
assert.equal(acceptedOnly[0].items[0].state, "KEEP");

const ignoredHistory = ctx.groupRagReviewCandidates(
  [base("ignore", { state: "IGNORE" })],
  { includeIgnored: true }
);
assert.equal(ignoredHistory.length, 1);
assert.equal(ignoredHistory[0].items[0].state, "IGNORE");

console.log("PASS candidate queue grouping, duplicate/IGNORE separation, evidence, empty-title fallback, no mutation");
