/* Shared project authoring. Source/API registration is separate from a draft. */
const projectDraftEditor = { current: null, cache: new Map(), busy: false, polling: false, timer: null, opening: 0 };
const projectDraftFields = {
  title: "프로젝트 이름", domain: "분야", description: "프로젝트 설명", goal: "최종 목표",
  target_users: "사용자·대상", scenarios: "사례·시나리오", structure: "구조·프로세스",
  capabilities: "기능·수행할 일", validation: "검증·완료 기준", constraints: "제약", project_root: "프로젝트 폴더 (선택)",
};

function projectDraftNotice(text) {
  document.querySelector("#project-draft-status").textContent = text;
}

function renderProjectDraft() {
  const current = projectDraftEditor.current;
  if (!current) return;
  const form = document.querySelector("#project-draft-form");
  form.replaceChildren();
  for (const [key, label] of Object.entries(projectDraftFields)) {
    const wrapper = node("label", "project-draft-field", label);
    const input = document.createElement(["title", "domain", "project_root"].includes(key) ? "input" : "textarea");
    input.name = key;
    input.maxLength = ["title", "domain"].includes(key) ? 160 : 12000;
    if (input.tagName === "TEXTAREA") input.rows = 3;
    input.value = current.fields[key] || "";
    input.addEventListener("input", () => {
      current.fields[key] = input.value;
      current.dirty = true;
      current.generation++;
      projectDraftNotice("저장하지 않은 변경이 있습니다. 저장하면 대화에서도 같은 내용을 읽습니다.");
    });
    wrapper.append(input);
    form.append(wrapper);
  }
  projectDraftNotice(current.dirty ? "저장하지 않은 변경이 있습니다." : `저장 버전 ${current.revision} · ${current.project_id || "새 프로젝트 초안"}`);
  document.querySelector("#project-draft-reload").hidden = true;
  document.querySelector("#project-draft-comparison").hidden = true;
}

async function openProjectDraft(draftId = null, initial = null, projectId = null) {
  const ticket = ++projectDraftEditor.opening;
  const id = draftId || `draft_${crypto.randomUUID()}`;
  const result = await invokeServerAction("project.draft.read", { draft_id: id });
  if (ticket !== projectDraftEditor.opening) return;
  let current = projectDraftEditor.cache.get(id);
  if (!current?.dirty) {
    current = { ...result.draft, fields: { ...result.draft.fields }, dirty: false, generation: 0 };
    if (!current.revision && projectId) current.project_id = projectId;
    if (initial && !current.revision) {
      Object.assign(current.fields, initial);
      current.dirty = true;
      current.generation++;
    }
    projectDraftEditor.cache.set(id, current);
  }
  projectDraftEditor.current = current;
  showGoalPlanView();
  const panel = document.querySelector("#project-draft-panel");
  panel.hidden = false;
  renderProjectDraft();
  panel.scrollIntoView({ block: "start", behavior: "smooth" });
  if (!projectDraftEditor.timer) projectDraftEditor.timer = setInterval(pollProjectDraft, 3000);
}

async function saveProjectDraft() {
  const current = projectDraftEditor.current;
  if (!current || projectDraftEditor.busy) return;
  const generation = current.generation;
  // Preserve the same key on a transport retry; edited content gets a new key.
  const body = { draft_id: current.draft_id, project_id: current.project_id, expected_revision: current.revision, fields: { ...current.fields } };
  const signature = JSON.stringify(body);
  if (current.pending?.signature !== signature) current.pending = { signature, request_id: crypto.randomUUID() };
  projectDraftEditor.busy = true;
  document.querySelector("#project-draft-save").disabled = true;
  try {
    const result = await invokeServerAction("project.draft.save", { ...body, request_id: current.pending.request_id });
    current.revision = result.draft.revision;
    current.pending = null;
    if (current.generation === generation) current.dirty = false;
    if (projectDraftEditor.current === current) projectDraftNotice(current.dirty ? "이전 입력은 저장됐습니다. 이후 변경은 아직 저장되지 않았습니다." : `버전 ${current.revision} 저장 완료`);
  } catch (error) {
    if (projectDraftEditor.current === current) {
      projectDraftNotice(error.errorCode === "PROJECT_DRAFT_REVISION_CONFLICT" ? "다른 편집자가 먼저 저장했습니다. 입력은 보존되어 있습니다. 서버 버전을 확인한 뒤 병합하세요." : error.message);
      document.querySelector("#project-draft-reload").hidden = error.errorCode !== "PROJECT_DRAFT_REVISION_CONFLICT";
    }
  } finally {
    projectDraftEditor.busy = false;
    document.querySelector("#project-draft-save").disabled = false;
  }
}

async function pollProjectDraft() {
  const current = projectDraftEditor.current;
  if (!current || projectDraftEditor.busy || projectDraftEditor.polling || document.hidden || document.querySelector("#project-draft-panel").hidden || state.view !== "work") return;
  projectDraftEditor.polling = true;
  try {
    const result = await invokeServerAction("project.draft.read", { draft_id: current.draft_id });
    if (projectDraftEditor.current !== current || result.draft.revision <= current.revision) return;
    if (current.dirty) {
      projectDraftNotice(`서버에 새 버전 ${result.draft.revision}이 있습니다. 현재 입력은 보존했습니다.`);
      document.querySelector("#project-draft-reload").hidden = false;
    } else {
      Object.assign(current, result.draft, { fields: { ...result.draft.fields } });
      renderProjectDraft();
    }
  } catch (error) { if (projectDraftEditor.current === current) projectDraftNotice(error.message); }
  finally { projectDraftEditor.polling = false; }
}

async function inspectProjectDraftVersion() {
  const current = projectDraftEditor.current;
  if (!current) return;
  const result = await invokeServerAction("project.draft.read", { draft_id: current.draft_id });
  if (current !== projectDraftEditor.current) return;
  const comparison = document.querySelector("#project-draft-comparison");
  comparison.replaceChildren(node("strong", "", `서버 버전 ${result.draft.revision}`));
  for (const [key, label] of Object.entries(projectDraftFields)) {
    if (current.fields[key] !== result.draft.fields[key]) {
      comparison.append(node("h4", "", label), node("pre", "", result.draft.fields[key] || "(비어 있음)"));
    }
  }
  const merge = node("button", "secondary-button", "현재 입력으로 병합 저장 준비");
  merge.type = "button";
  merge.addEventListener("click", () => {
    current.revision = result.draft.revision;
    current.pending = null;
    current.dirty = true;
    current.generation++;
    comparison.hidden = true;
    projectDraftNotice("입력을 검토한 뒤 저장하세요. 서버 버전이 다시 바뀌면 충돌을 확인합니다.");
  });
  comparison.append(merge);
  comparison.hidden = false;
}

async function listProjectDrafts() {
  const result = await invokeServerAction("project.draft.list", {});
  const list = document.querySelector("#project-draft-list");
  list.replaceChildren();
  for (const draft of result.drafts) {
    const button = node("button", "secondary-button", `${draft.fields.title || "이름 없는 초안"} · v${draft.revision}`);
    button.type = "button";
    button.addEventListener("click", () => openProjectDraft(draft.draft_id).catch(error => toast(error.message, true)));
    list.append(button);
  }
  if (!result.drafts.length) list.append(node("span", "", "저장된 초안이 없습니다."));
}

function projectDraftConversationContext() {
  const current = projectDraftEditor.current;
  return current?.revision && state.view === "work" && !document.querySelector("#project-draft-panel").hidden ? { project_draft_id: current.draft_id } : {};
}

document.addEventListener("DOMContentLoaded", () => {
  api("/v1/actions").then(catalog => {
    document.querySelector(".project-authoring-controls").hidden = !catalog.registry?.registered_action_ids?.includes("project.draft.save");
  }).catch(() => {});
  const bind = (selector, callback) => document.querySelector(selector)?.addEventListener("click", () => Promise.resolve().then(callback).catch(error => toast(error.message, true)));
  bind("#project-draft-save", saveProjectDraft);
  bind("#project-draft-reload", inspectProjectDraftVersion);
  bind("#project-draft-show-list", listProjectDrafts);
  bind("#project-draft-edit-selected", async () => {
    const projectId = state.selectedProject?.project_id;
    if (!projectId) throw new Error("프로젝트를 먼저 선택하세요.");
    const result = await invokeServerAction("project.draft.list", { project_id: projectId });
    await openProjectDraft(result.drafts[0]?.draft_id, { title: projectId }, projectId);
  });
  bind("#project-draft-close", () => { document.querySelector("#project-draft-panel").hidden = true; });
});
