# Memory 후보 종류와 UI 감사 — 2026-09-11

상태: 조사 완료, 제품 코드 변경 없음. 사용자 요청: RAG 종류 분리의 구현 상태와 Memory Explore/UI 확인.

## 결론

종류 분리는 미구현 백지가 아니다. 서로 다른 세 축이 구현돼 있으나, 후보별 허용 동작을 서버와 UI가 공유하지 않아 MEMORY의 EXPLORE가 후속 처리에서 끊긴다.

| 축 | 문서 계약 | 구현 근거 / 한계 |
| --- | --- | --- |
| 후보 종류 | MEMORY / IDEA / HYPOTHESIS / PRODUCT — universe-memory-rag.md의 Redacted candidates | tools/universe_memory.py의 MEMORY_CANDIDATE_KINDS. 종류 저장·검증 존재. UI는 모든 종류에 동일한 네 결정을 노출 |
| 메모 성격 | 관찰·브레인스토밍·질문·확정 결정 | MEMORY_STATES = OBSERVED / BRAINSTORM / QUESTION / DECISION_NOTE. 후보의 MEMORY kind와 별개 축 |
| 연결 상태 | 미연결·연결 제안·연결 확정 | MEMORY_LINK_STATES = UNLINKED / PROPOSED / LINKED. 일반 LLM retrieval은 LINKED만 조회 |
| 지식 그래프 | DOCUMENT / DECISION / MEMORY — universe-unified-node-graph-model.md §2b | tools/universe_node_graph.py:graft_knowledge_nodes에 DECISION/MEMORY 분기 존재. 문서·결정·관찰·테스트 근거의 전역 수집 완료를 뜻하지 않음 |

RAG는 노드에 연결된 검색 가능한 지식 전체를 뜻한다. Memory 화면의 모든 후보가 이미 정본 RAG로 채택됐다는 뜻은 아니다. 실패 지식은 별도 kind가 아니라 MEMORY candidate의 구조화된 failure_knowledge이며 일반 채택 Memory와 분리된 recall 문맥으로 반환된다.

## 확인된 처리 불일치

1. UI `tools/universe_ui/app.js:renderMemoryCandidates`는 REVIEW_REQUIRED인 모든 후보에 IGNORE / KEEP / EXPLORE / START_PRODUCT_DESIGN을 노출한다.
2. `UniverseStore.review_memory_candidate`는 종류별 제한 없이 결정을 기록한다. 같은 결정 재호출은 허용하지만 EXPLORE에서 KEEP 등 다른 결정으로 변경은 STATE_CONFLICT다.
3. `attach_memory_review_next_work`는 프로젝트 제안 생성기를 호출한다. 호출 자체가 클릭한 후보가 제안 입력에 포함됨을 뜻하지 않는다.
4. `tools/universe_app/feature_node_proposal.py:_source_entries`는 MEMORY + EXPLORE를 제외하고 MEMORY + START_PRODUCT_DESIGN만 포함한다. IDEA/HYPOTHESIS/PRODUCT는 두 결정을 모두 입력으로 받는다.
5. UI의 다음 행동 toast는 클릭한 candidate에 대응하는 결과가 아니라 `review_inbox.bundles[0]`를 사용한다. 클릭 항목의 후속 처리 성공으로 해석할 수 없다.

순수 함수 8조합 probe 결과: MEMORY EXPLORE 입력 0개, MEMORY START_PRODUCT_DESIGN 입력 1개; IDEA/HYPOTHESIS/PRODUCT의 두 결정은 각각 1개. 실제 사용자 후보를 다시 검토하거나 상태 변경하지 않고 실행했다.

## 추가 확인

- `review_inbox_next_work._classify_text`는 CLI 확인용 문구 `When explicitly requested, return exactly CODEX_CLI_OK with no additional text.`를 PRODUCT_INTENT로 분류한다. MEMORY + EXPLORE일 때는 후속 종류 검사로 REVIEW_ONLY가 되지만 START_PRODUCT_DESIGN이면 제품 의도로 통과할 수 있다. 현재 문자열 필터를 더 덧붙이는 대신 출처·용도에 따른 공통 입력 적격성 계약이 필요하다.
- `renderMemory`는 `link_state !== UNLINKED`를 Linked memory에 넣으므로 PROPOSED도 포함한다. 화면의 Linked 표시는 실제 LINKED만이라는 의미와 다르다.
- 현재 UI는 기본 REVIEW_REQUIRED 필터, 검토 뒤 사라지는 버튼, 별도의 결과 링크 부재 때문에 항목이 어디로 갔는지 알기 어렵다.

## 화면 근거와 제한

![Memory 후보의 동일 네 버튼](../.artifacts/ui/memory-candidate-review-audit-20260911.png)

1. 기존 Universe 탭에서 Memory 화면을 열었다. 배치 설정이 후보 검토보다 위에 있고 종류/단계/상태가 각각 원시 enum으로 표시된다.
2. CLI 확인용 MEMORY 카드에 네 버튼이 함께 노출됨을 캡처했다. 작은 회색 상태·출처 텍스트는 가독성 검토 대상이다. 실제 대비비·키보드 접근성 전수 검사는 NOT_RUN.
3. 캡처한 기존 탭은 REVIEW_REQUIRED를 유지하고 있었으나 별도 API에서 같은 후보는 이미 EXPLORE로 관측됐다. 캡처는 버튼 구성의 증거이며 최신 DB 상태의 증거가 아니다. 페이지 reload나 사용자 재검토는 수행하지 않았다.

## 개선 순서

1. 공통 종류별 action 계약: 서버가 allowed_actions, disabled_reason, candidate별 next_action/target을 계산하고 UI가 그대로 렌더링. MEMORY의 지식 보관과 제품 아이디어 전환을 별개 의미로 표현한다. 후속 작업 없는 MEMORY EXPLORE는 노출·수락하지 않는다.
2. 명시적 복구: 이미 EXPLORE된 MEMORY를 사용자가 재검토할 수 있는 revision/digest-bound 동작과 불변 검토 이력을 추가한다. 기존 후보 일괄 KEEP/채택/삭제는 하지 않는다.
3. 입력 적격성: 제안 생성기와 검토함이 같은 출처·용도 판정을 사용하도록 정렬. CLI probe/fixture/범용 요약이 실제 제품 의도를 대신하지 않도록 검증한다.
4. UI: 지식 후보 / 아이디어·가설·제품 제안 / 보관된 지식으로 구분하고, 보관된 지식은 미연결·연결 제안·연결 확정을 명확히 나눈다. 배치 설정은 별도 접힌 영역으로 이동한다. 검토 직후 클릭한 후보에 해당하는 결과와 다음 행동을 보여준다.
5. 검증: 종류×결정 행렬, API 직접 잘못된 요청, 재검토 중복·동시 요청·digest 충돌, candidate별 결과 귀속, RAG 채택·노드 연결의 독립성, 새로고침·다른 탭 상태 갱신 및 실제 UI 동작.

기존 소유 TODO: `todo_c4836d989cab441da55c1309f5545e40`(메모·RAG 검토함→다음 작업). 이번 조사로 구체화된 위 범위를 그 작업의 구현 슬라이스로 삼는다. 별도 taxonomy 저장소나 신규 배치/Collector를 만드는 일은 이번 결함 해결 범위가 아니다.
