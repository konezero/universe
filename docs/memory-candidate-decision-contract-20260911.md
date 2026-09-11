# Memory Candidate 결정 계약 — 2026-09-11

상태: 초안 v2 (Codex B 검토 반영, 두 차단 이견 확정). 새 taxonomy 저장소나 Collector를
만들지 않는다 — `docs/memory-candidate-type-ui-audit-20260911.md`가 확인한 기존 세 축
(후보 종류 / 메모 성격 / 연결 상태)을 그대로 쓴다. 담당 A(백엔드/공통 계약, 이 문서
소유)와 Codex Master(UI, `tools/universe_ui/app.js`)가 이 계약만으로 통합한다.

## v2 변경 — Codex B의 두 차단 이견에 대한 확정 답변

1. **MEMORY+EXPLORE**: 원 지시("무효 MEMORY EXPLORE는 차단")와 감사 문서 개선 순서
   1번("후속 작업 없는 MEMORY EXPLORE는 노출·수락하지 않는다")을 그대로 따른다.
   v1에서 EXPLORE를 "노출하되 결과만 KNOWLEDGE_RETAINED_NO_AUTOMATION으로 표시"한
   건 원 지시보다 약했다 — 정정: **MEMORY kind의 `allowed_actions`에서 EXPLORE를
   제외**한다. MEMORY는 REVIEW_REQUIRED에서 `IGNORE`, `KEEP`, `START_PRODUCT_DESIGN`
   세 개만 허용. `disabled_actions.EXPLORE = "MEMORY_EXPLORE_NO_AUTOMATION"`는
   API/테스트 관측용으로 계속 내려주되, UI가 그 항목을 아예 렌더링하지 않아도 된다
   (disabled_actions에 있다고 버튼을 반드시 보여줘야 하는 건 아니다 — allowed_actions에
   없으면 기본은 비노출, disabled_actions는 "왜 없는지" 설명이 필요할 때만 UI가
   선택적으로 쓴다).
2. **digest만으로는 동시성이 약하다**: `candidate_digest`는 내용 해시라 재검토를
   여러 번 해도 내용이 그대로면 같은 값일 수 있어 "이번 재검토 시도"를 구분하지
   못한다. candidate에 **`revision`**(정수, `REVIEW_REQUIRED`에서 시작 1, 매 review/
   reopen 성공마다 +1)을 추가한다. reopen 요청은 `expected_candidate_digest`와
   `expected_candidate_revision` 둘 다 받고 **둘 다** 일치해야 적용된다(내용 불변
   경쟁과 revision 경쟁을 각각 잡는다). 하나라도 어긋나면 `MEMORY_CANDIDATE_REVISION_STALE`
   (digest 불일치면 기존 `MEMORY_CANDIDATE_DIGEST_STALE` 유지, 우선순위: digest 먼저
   검사). `decision_contract.reopen.candidate_revision`으로 현재 값을 노출한다.

## 배경 — 확인된 불일치 (감사 문서 §확인된 처리 불일치 1-5)

UI가 REVIEW_REQUIRED 후보 전부에 동일한 4버튼(IGNORE/KEEP/EXPLORE/START_PRODUCT_DESIGN)을
노출하지만, 서버 쪽 다운스트림 효과는 kind별로 다르다:

- `tools/universe_app/feature_node_proposal.py:_source_entries` — MEMORY kind는
  `state=="START_PRODUCT_DESIGN"`일 때만 product-intent 입력이 된다(`memory_design`
  분기). MEMORY+EXPLORE는 제안 생성기 입력이 되지 않으며, 위 v2 결정에 따라
  애초에 허용 액션에서 제외된다.
- `attach_memory_review_next_work`(`tools/universe_server.py:19272`)는 state가
  EXPLORE 또는 START_PRODUCT_DESIGN이면 무조건 `generate_feature_node_proposals`를
  호출한다. MEMORY는 이제 EXPLORE 자체가 불가능해지므로 이 무의미한 호출 경로가
  사라진다.
- 재검토 불가: `review_memory_candidate`(`:19174`)는 `current_state != REVIEW_REQUIRED`면
  무조건 `MEMORY_CANDIDATE_STATE_CONFLICT`. 잘못 검토된 MEMORY를 사용자가 되돌릴
  방법이 없다(기존에 이미 EXPLORE로 잘못 검토된 MEMORY 포함 — §2 reopen이 유일한
  교정 경로).
- UI toast가 클릭한 candidate가 아니라 `review_inbox.bundles[0]`을 쓴다(감사 문서 5번) —
  이건 UI 담당이지만, 서버가 review 응답에 candidate별 결과를 실어주지 않으면 UI가
  고칠 수 없다. 이 계약은 그 candidate별 결과를 명시한다.
- `review_inbox_next_work._classify_text`(`:72`)는 키워드 블록리스트라서 CLI 확인
  문구("return exactly CODEX_CLI_OK...")처럼 목록에 없는 문자열은 PRODUCT_INTENT로
  샌다. 문자열을 더 추가하는 대신 출처(source_kind/origin_ref) 기반 적격성 표시로
  옮긴다(§3).

## 1. `review_memory_candidate` 응답에 `decision_contract` 추가

`POST /v1/projects/{project_id}/memory-candidates/{candidate_id}/review` 및
candidate를 반환하는 모든 read 경로(list/get)에 다음 필드를 candidate 객체에 얹는다.
기존 필드는 그대로 둔다(additive).

```jsonc
"decision_contract": {
  "schema": "universe.memory-candidate-decision-contract.v1",
  "state": "REVIEW_REQUIRED",            // candidate.state 그대로, 편의상 복제
  "allowed_actions": ["IGNORE", "KEEP", "START_PRODUCT_DESIGN"],
  // kind=="MEMORY" 예시. IDEA/HYPOTHESIS/PRODUCT는 네 개(EXPLORE 포함) 그대로 허용.
  // state != REVIEW_REQUIRED면 기본적으로 [] (재검토 가능성은 reopen에서 별도 표현)
  "disabled_actions": {
    "EXPLORE": "MEMORY_EXPLORE_NO_AUTOMATION"
    // action -> 안정된 코드. list/get/review 전 경로에서 동일 코드 유지.
    // UI는 이 코드로 문구를 결정할 수도, allowed_actions에 없으니 그냥 숨길 수도 있다 —
    // 서버는 의미(왜 막혔는지)만 소유하고 노출 여부는 강제하지 않는다.
  },
  "current_decision": null,               // state==REVIEW_REQUIRED면 null, 아니면 state
  "next_action": {
    "kind": "NONE",
    // REVIEW_REQUIRED  -> NONE (아직 결정 안 됨)
    // MEMORY+START_PRODUCT_DESIGN, {IDEA,HYPOTHESIS,PRODUCT}+{EXPLORE,START_PRODUCT_DESIGN}
    //                  -> "PRODUCT_PROPOSAL_ATTEMPTED", target_ref = review_inbox bundle
    //                     또는 feature_node_proposal id (실제 생성 여부는 effects로 구분)
    // MEMORY+KEEP      -> "RAG_ADOPT_AVAILABLE", target_ref = candidate_digest
    //                     (adopt_memory_candidate에 바로 쓸 digest)
    // {IDEA,HYPOTHESIS,PRODUCT}+KEEP -> "ACKNOWLEDGED_NO_AUTOMATION"
    //                     (KEEP은 MEMORY만 RAG 채택 대상이라 kind != MEMORY면
    //                     자동화 없음을 명시)
    // 모든 kind+IGNORE  -> "NONE"
    "target_ref": null
  },
  "reopen": {
    "allowed": false,
    "reason": null,                 // ALREADY_REVIEW_REQUIRED | RAG_ALREADY_ADOPTED | ...
    "candidate_digest": "<sha256>",  // §2 재검토 요청에 되돌려 보낼 값
    "candidate_revision": 1          // §2 재검토 요청에 되돌려 보낼 값 (v2 신규)
  }
}
```

`next_action.kind` 값 집합은 닫힌 enum
(`NONE|PRODUCT_PROPOSAL_ATTEMPTED|RAG_ADOPT_AVAILABLE|ACKNOWLEDGED_NO_AUTOMATION`)으로
서버가 소유(`KNOWLEDGE_RETAINED_NO_AUTOMATION`은 v2에서 제거 — MEMORY+EXPLORE 자체가
없어졌으므로). `attach_memory_review_next_work`의 반환값(`generated.effects`)을 그대로
실어 실제 생성 여부(`feature_node_created` 등)와 `next_action.kind`가 항상 같이 온다 —
UI는 이 필드만 읽고 자체적으로 kind×state 분기를 재구현하지 않는다(그게 지금
문제였던 "네 버튼 균일 노출"의 원인).

## 2. 명시적 재검토(reopen) — 별도 엔드포인트, 별도 이력, digest+revision

`review_memory_candidate`를 고쳐서 상태를 덮어쓰지 않는다. 대신:

```
POST /v1/projects/{project_id}/memory-candidates/{candidate_id}/reopen
body: {
  "expected_candidate_digest": "<sha256>",
  "expected_candidate_revision": 1,
  "reason": "<free text, <=500>"
}
```

- 현재 `state`가 REVIEW_REQUIRED가 아니고 `KEEP` 상태로 이미 RAG에 adopt된 적이
  없을 때만 허용(adopt된 MEMORY는 origin_ref로 이미 project memory에 연결돼
  있으므로 되돌리면 그 연결이 끊긴 채 남는다 — adopt 이후는 reopen 금지,
  `RAG_ALREADY_ADOPTED` 에러).
- 검증 순서: `expected_candidate_digest`가 현재 `candidate_digest`와 다르면
  `MEMORY_CANDIDATE_DIGEST_STALE`(409). digest는 맞는데
  `expected_candidate_revision`이 현재 `revision`과 다르면
  `MEMORY_CANDIDATE_REVISION_STALE`(409) — 동시 재검토/경쟁을 내용 변경과 순번
  변경 두 축으로 각각 방지.
- 성공하면 `state`를 `REVIEW_REQUIRED`로 되돌리고 `revision`을 +1 하되, 기존
  `memory_candidate_review` 행은 **삭제하지 않는다** — 새 `memory_candidate_reopen`
  테이블(또는 기존 review 테이블에 `superseded_by_reopen_at` 컬럼)에 남겨 불변
  이력을 유지한다. 재검토 후 다시 결정하면 새 review row가 추가되고(그때도
  revision +1), candidate에는 review 이력 배열(`review_history: [...]`, 최신이
  마지막, 각 항목에 `revision` 포함)을 노출해 UI가 과거 결정을 보여줄 수 있게 한다.
- reopen 자체도 `memory_candidate_review`류 테이블에 `decision: "REOPENED"`로
  기록(`MEMORY_CANDIDATE_DECISIONS`에는 넣지 않음 — reopen은 review decision이
  아니라 별도 액션이라 기존 enum을 건드리지 않는다).
- 최초 생성된 candidate의 `revision`은 1. review 성공(§1 decision 기록)과 reopen
  성공 각각 +1 — 즉 review 자체도 revision을 올린다(재검토 경쟁만이 아니라 최초
  review와 그 직후의 동시 재검토 요청 사이 경쟁도 잡기 위함).

## 3. 입력 적격성 — 문자열 필터 대신 출처/용도 경계

`review_inbox_next_work._classify_text`와 `feature_node_proposal._source_entries`가
각각 독립적으로 "이게 진짜 제품 의도인가"를 판정하는 게 근본 문제(감사 문서 §추가
확인 1번). 새 워크플로별 문자열을 추가하는 대신, 공통 판정 함수 하나로 합친다:

```python
# tools/universe_app/intent_eligibility.py (신규, 최소)
def classify_intent_eligibility(*, source_kind: str, origin_ref: str | None,
                                 text: str) -> str:
    """PRODUCT_INTENT | TEST_ONLY | GENERIC — 하나의 판정 지점."""
```

- `source_kind`/`origin_ref`가 CLI probe로 알려진 출처
  (`universe://cli-probe/...` 같은 예약 prefix, 또는 배치 config의
  `origin: "CLI_VERIFICATION"` 플래그)면 텍스트 내용과 무관하게 `TEST_ONLY`.
  이게 감사 문서가 요구한 "출처·용도에 따른 공통 입력 적격성 경계"다 — CLI 확인
  루프가 만드는 candidate/memory는 애초에 origin_ref에 그 사실을 남기게 만드는
  쪽이 문자열 블록리스트보다 견고하다(신규 배치 종류를 만들라는 게 아니라, 이미
  있는 batch run/observer 소스 태깅에 값 하나 추가하는 정도).
  이 부분은 candidate/memory 생성 경로(어디서 origin_ref를 세팅하는지) 확인이
  더 필요 — **미확정, 구현 전 추가 조사 필요**.
- 위 판정이 안 되는 기존 텍스트 휴리스틱(TEST_ONLY_MARKERS/GENERIC_MARKERS/토큰수)은
  fallback으로 유지하되 두 파일이 이 함수 하나를 호출하도록 정리한다.

## 4. 이번 슬라이스에서 하지 않는 것

- UI 렌더링/문구/접힌 배치 설정 이동(Codex 담당).
- 새 후보 종류(kind)나 새 배치/Collector 추가.
- 기존 사용자 후보 일괄 재검토·재분류.
- RAG 노드 그래프 연결 자동화 확장(§4 지식 그래프 축은 그대로 둠).

## 완료 기준 매핑

| 공동 완료 기준 | 이 계약의 대응 |
| --- | --- |
| kind×decision/API 직접 호출 불일치 차단 | §1 `decision_contract.allowed_actions`/`disabled_actions`(MEMORY는 EXPLORE 제외), 서버가 review 요청 시에도 같은 표를 검증 |
| 명시적 재검토 digest/revision·동시성·불변 이력 | §2 `/reopen` + digest **and** revision 이중 충돌 검사 + review_history |
| candidate별 후속 결과 | §1 `next_action`(review 응답 + list/get 응답 모두에 실림, bundle[0] 의존 제거) |
| 지식 채택/노드 연결 독립성 | 변경 없음 — `adopt_memory_candidate`/`graft_knowledge_nodes` 그대로, next_action은 참조만 |
| 실제 UI 동작·새로고침 | Codex 담당, 이 계약이 그 구현의 입력 |
