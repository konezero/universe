# RAG 모델 출처 참조 계약 보완 — 2026-09-12

확인된 실패는 memory_batch_run_b26a10b9ed7f132608346473의 FAST_EXTRACT_RESULT_INVALID: candidates[5].ref_digests must reference selected Activity다. 실패한 원문 응답은 보관하지 않으므로 어떤 잘못된 해시였는지, 복사 오류인지 다른 해시와의 혼동인지는 UNKNOWN이다.

기존 입력은 활동/본문/배치/설정 등 여러 종류의 해시를 포함했고 출력은 ref_digests 문자열 배열만 요구했다. 모델이 긴 활동 해시를 직접 복사해야 했다. Codex Task Frame 어댑터는 이 출력 계약을 프롬프트에 전달하며, 현재 공통 부분 스키마 검증기는 enum까지 검사하지 않는다. 이 변경은 디코딩 단계에서 생성이 강제된다고 주장하지 않는다.

모델 응답 전용 계약을 universe.memory-fast-extract-model-result.v2로 변경했다. 이번 호출의 검증된 본문 근거에 연결된 활동 해시를 정렬해 E1/E2 같은 짧은 번호를 붙인다. 같은 활동의 여러 발췌는 같은 번호를 사용한다. context_pack.reference_catalog와 각 semantic_evidence.evidence_id로 연결하며, 출력은 evidence_ids로 선택한다. 스키마에는 허용 번호 enum, 최소1개, 중복 금지를 명시한다. 본문 없는 활동과 실패 회고는 근거 목록에 넣지 않는다. 재사용할 만한 내용이 없으면 빈 후보 배열도 허용해 근거 없는 후보 생성을 요구하지 않는다.

서버 decode_provider_references는 현재 호출의 동일 근거로 목록을 재구성한다. 알 수 없는 번호·빠진 번호·중복 번호·직접 제출된 ref_digests는 거부하며 누락된 출처를 추정하거나 자동 수정하지 않는다. 검증된 번호만 정확한 활동 해시로 변환한 뒤 기존 후보 검증을 수행한다. 내부 후보/영속 provenance는 기존 v1 ref_digests를 유지하고, 짧은 번호를 영구 출처로 저장하지 않는다. 새 실행 경로는 예전 모델 응답을 자동 수용하는 호환 분기를 두지 않는다.

검증: FAST_EXTRACT9건, Runtime 준비7건, source window13건 총29건 PASS. 기존 정상 HTTP 추출과 비밀값 마스킹/영수증 검증을 통과했다. 유효한 후보 다음에 E999 출처 후보를 붙인 HTTP 테스트에서 FAST_EXTRACT_REFERENCE_INVALID, run FAILED, 후보 저장0개를 확인했다. 정확한 해시 복원, 원본 응답 불변, 알 수 없는 번호/해시/중복/빈 참조/숫자 거부, 후보0개 허용을 검사했다. 일부 기존 테스트 종료 ResourceWarning은 출력됐으나 결과는 PASS였다.

이 변경은 출처 식별 오류를 줄이고 잘못된 참조를 차단하는 것이다. 존재하는 E1을 골랐더라도 실제 요약이 그 근거를 뒷받침하는지는 원문 기반 의미 검증의 후속이다. 무조건 재시도, 잘못된 후보만 조용히 버리기, 자동 채택은 추가하지 않는다.


## 실서비스 검증 — 2026-09-13 KST

기존 실패 run memory_batch_run_b26a10b9ed7f132608346473의 attempt2가 COMPLETED로 완료됐다. run ID가 같으므로 기존 입력/config digest가 같은 재시도이며, 새 모델 응답 계약을 적용했다.166.42초, 후보16개 신규 생성. 후보 provenance의49개 참조가 실제 Activity 해시인 것을 재조회했다. 서버의 호출별 decoder 및 기존 선택 활동 검증을 모두 통과했다. 보관 후보에 E번호를 출처 해시 대신 저장하지 않았다.

성공 커서 last_run_id가 위 run으로 갱신됐고 source_ed318cc5704742c6a8d80f2c536e9188의 활동926번이 재개 기준으로 저장됐다. 배치 Runtime 프로세스0개, Supervisor batch-runtime 세션0개, RUNNING 배치0개 확인. 증거 .ai/runtime/tmp/rag-ref-auto-live.json. 실패 이력을 지우지 않고 attempt2 성공으로 기록했다.

빈 후보 HTTP 완료 테스트도 추가했다. 후보0개일 때 저장 후보0개/run COMPLETED를 확인했으며, 실서비스 모델의 빈 후보 응답 사례는 NOT_RUN이다. 위 live16개 성공 이후 빈 결과 처리까지 포함한 최종 코드를 서비스에 반영했다.

이번 한 번의 재시도 성공은 오류율의 통계적 개선이나 후보 내용의 사실성을 증명하지 않는다. 후속 의미 검증과 전체 소스 순환 검증은 유지한다.

```yaml
outcome: SUCCEEDED
affected_planes: [source, API, runtime_orchestration, provider_transport, storage, process_lifecycle]
validation:
  source_API: PASS_29_TESTS
  invalid_reference_atomicity: PASS_HTTP_NO_CANDIDATE_WRITES
  empty_candidate_result: PASS_HTTP_ZERO_CANDIDATES
  provider_transport: PASS_LIVE_ATTEMPT_2_16_CANDIDATES
  storage: PASS_PROVENANCE_AND_SUCCESS_CURSOR
  process_lifecycle: PASS_ZERO_BATCH_PROCESSES
  UI: NOT_APPLICABLE
residual_risks: [semantic_support_not_validated, statistical_error_rate_not_measured]
evidence_refs: [.ai/runtime/tmp/rag-ref-auto-live.json]
changed_paths: [tools/memory_fast_extract.py, tools/universe_server.py, tests/test_memory_fast_extract.py]
```
