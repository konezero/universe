# RAG 수집 누락 보완 — 2026-09-12

## 확인된 원인과 변경

실서비스 시작 시 UNKNOWN은91개였다. Claude 형식 오류50개, 원본 없음41개였다. 운영 DB 복사본에서 현재 파서로 다시 읽자 형식 오류41개가 복구됐다. 추가8개는 UUID 없는 custom-title/cost-state 메타데이터에서 중단됐고1개는 파일이 사라져 있었다. 이 두 메타데이터 종류를 명시적 목록에 추가했다. 알 수 없는 UUID 없는 이벤트는 계속 거부한다.

원본 없음으로 등록된 Codex31개는 보관 폴더의 동일 파일을 찾았다. 파일 식별자와 실제 세션 메타데이터 ID가 모두 같을 때만 경로를 갱신한다. 다른 파일, 식별자 없는 파일, 여러 일치 항목, 이미 등록된 목적지는 자동으로 합치거나 대체하지 않는다.

자동 AI 배치 준비 시 원본8개를 순환 점검한다. 현재 코드로 기존 정확한 커서부터 재스캔하고, 앞서 관측하지 않은 기록을 별도 RAG 이력 테이블과 커서로 수집한다. 실시간 Observer 커서/활동 상태/Bus 전달은 되감지 않는다. 파일당512이벤트와256KiB를 기본 예산으로 사용하며 한 이벤트는 기존4MiB 상한을 따른다. 파일 읽기는 기본0.25초 구간으로 제한한다. 경로 탐색과 파일 시스템 호출까지 포함한 절대 실행 시간 상한을 보장하지는 않는다.

이력은 원문 없이 기존 Activity 필드만 저장한다. 종료 경계는 최초 실시간 활동의 byte offset 또는 당시 관측 커서다. 이력 ordinal은 byte offset+1로 안정적으로 식별하며, 기존 실시간 ordinal을 다시 매기지 않는다. Claude 최신 분기 연결과 본문 읽기 순서는 파일 위치를 기준으로 한다. 오류 발생 시 성공한 앞부분의 커서와 오류 코드/위치를 남기며, 반복·재시작·동시 호출에서 이미 넣은 이력을 중복 생성하지 않는다.

긴 단일 활동은 마스킹 후2,000자 조각으로 나누어 한 번에 최대16개를 읽는다. 다음 조각 위치와 전체 마스킹 본문의 digest를 활동 재개 정보에 저장한다. 원본이나 연결된 활동이 바뀌면 재개를 거부한다. 성공한 추출 run에 대해서만 기존 원자적 커서 갱신을 적용한다. HTTP 호출자가 비공개 본문 구간을 직접 지정할 수 없다.

메모 화면은 긴 메시지의 조각 진행률과 최근 원본 점검/과거 활동 추가/수집 진행/확인 필요 수를 표시한다. 이는 최근 점검한 원본(자동 최대8개, 명시적 scan은1개) 기준이며 전체 원본의 누적 총계가 아니다. 점검 결과는 모델 실행의 성공 여부와 독립적으로 저장해 실패 뒤에도 표시한다.

## 운영 경로

- 자동 FAST_EXTRACT 준비: 순환 원본 점검 → 활성 소스/활동 구간 선택 → 근거 재검증 → 모델 실행 → 성공한 구간 커서 저장.
- 명시적 원본 복구·과거 수집: 로컬 운영자 인증을 요구하는 기존 POST /v1/session-observer/sources/{id}/scan에 include_history:true. 매 요청은 한 소스의 제한된 구간만 처리한다. 기본 scan은 실시간 관측 동작을 유지한다.
- 새 테이블: provider_rag_history_activity, provider_rag_history_cursor, provider_rag_maintenance. 이력 수집 상태와 AI 추출 성공 위치는 서로 다른 커서다.

## 검증

- 관련57건 PASS: 기존 Observer23/source window13/Runtime 준비7/FAST_EXTRACT6 및 새 수집8.
- 70,001자 실제 JSONL/Observer fixture를3구간으로 읽어 마스킹된 본문 전체가 일치함을 확인. 동일 길이 본문 변조 시 재개 거부.
- 과거 이력의 제한 수집/저장소 재개방 후 재개/중복0/실시간 커서 불변, Claude 과거 부모 연결, 동시 이력 수집,8개 원본 순환, 원본 이동과 교체 구분, 잘못된 형식 커서 보존 PASS.
- 실서비스 운영자 scan API: include_history 문자열 입력400, 정상 boolean 요청 성공.
- 기존 UNKNOWN91개 점검 결과63개 복구(Claude49, Codex14), 과거 활동926개 추가.56개 이력 완료,7개 이력 진행,28개 확인 필요. 이미 등록된 보관 원본17개는 유지했고, 원본을 찾지 못한11개는 UNKNOWN으로 유지했다.
- 증거: .ai/runtime/tmp/rag-gap-copy-recovery.json, rag-gaps-live-maintenance.json. 서비스만 재시작했으며 Supervisor/사용자 터미널은 재시작하지 않았다.
- 기존 FAST_EXTRACT 테스트 일부 종료 시 remote_gateway 정리 PermissionError 및 ResourceWarning이 출력됐으나 테스트 결과는 PASS였다.

## 남는 한계

자동 전체 소스 순환을 끝냈다는 의미는 아니다. 한 번에8개씩 점검하며 아직 점검되지 않은 과거 기록은 후속 배치에서 처리한다. 보관 원본의 중복 등록17개를 합치는 정책과 사라진 파일11개의 외부 복구는 별도다. 매 호출4MiB를 넘는 단일 JSONL 이벤트는 기존 읽기 상한으로 차단된다. 이력 내부 형식 오류/파일 교체·절단은 자동으로 건너뛰지 않는다.

수집 완전성은 원본 기반 사실성·충돌 판정·실제 AI 통합·자동 채택의 완료를 뜻하지 않는다. 일반 소스 순환은 재검토를 허용하며 전역 영구 exactly-once AI 추출을 보장하지 않는다.

## 실제 모델 실행 결과

- 자동 선택 실행 memory_batch_run_b26a10b9ed7f132608346473: 약232.7초 후 FAST_EXTRACT_RESULT_INVALID. 모델이 candidates[5].ref_digests에 선택되지 않은 활동을 제시해 기존 근거 검증이 결과 저장을 거부했다. 자동 성공 커서는 기존 memory_batch_run_dd7326314bdd07470dc8897a/source_ea489a01720448e3b28d0949e438d2fe를 유지했다. 자동 실행 전체 PASS로 표시하지 않는다.
- 이어서 복구된 Claude source_afe6b111ea3748f794d7339c72dcedc5를 명시한 실행 memory_batch_run_54a54a37af9109a355a146a2: COMPLETED,41.15초, 후보1개 생성. 활동3개 중 본문 발췌3개/1,019자를 사전 검증했고 이력 발췌2개를 포함했다. 생성된 후보의 ref_digests는 실시간 활동을 가리켰으므로 과거 본문의 의미적 추출 완료를 주장하지 않는다.
- 실행 모델 CODEX/gpt-5.6-luna/MAX. 증거 .ai/runtime/tmp/rag-gaps-auto-live.json, rag-gaps-history-live-selection.json, rag-gaps-history-live-result.json.
- 70,001자 내부 구간의 실제 모델3회 연속 호출은 NOT_RUN. 해당 전체 읽기/재개/변조 거부는 실제 Observer 파일 fixture 테스트로 검증했다.

결과: 수집 복구·본문 분할·이력 저장/API는 PASS. 자동 모델 결과의 근거 형식은 위 실행에서 FAIL이며 품질 후속으로 남긴다. 전체 RAG 완료를 선언하지 않는다.

최종 실서비스 UI: 메모 화면에서 최근 원본 점검1개/확인 필요1개를 확인하고, 펼치면 CODEX source_fba711a7fbb842e3bf72628a488a5b04의 ‘보관 원본이 다른 항목으로 이미 등록됨’ 사유가 표시됨을 확인했다. API의 source_maintenance와 운영자 scan 결과 일치. 마지막 서비스 재시작 READY, 종료 후 배치 Runtime Python 프로세스0개/Supervisor batch-runtime 세션0개 확인. TODO todo_89efd4a14c884386855ae58a391908f4 revision10, IN_PROGRESS 유지.

```yaml
outcome: PARTIAL
affected_planes: [source, storage, API, runtime_orchestration, provider_transport, UI, process_lifecycle]
validation:
  source_storage: PASS_57_RELATED_TESTS
  API: PASS_LIVE_SCAN_AND_CONFIG
  runtime_orchestration: PASS_PREPARATION_AND_FAILED_RUN_CURSOR_PRESERVATION
  provider_transport: PASS_EXPLICIT_RECOVERED_SOURCE_RUN
  automatic_model_output: FAIL_UNSELECTED_ACTIVITY_REFERENCE
  UI: PASS_LIVE_MAINTENANCE_SUMMARY_AND_DETAILS
  process_lifecycle: PASS_READY_AND_ZERO_BATCH_PROCESSES
  distribution: NOT_APPLICABLE
residual_risks: [missing_files_11, duplicate_source_registrations_17, unvisited_history, model_reference_quality, events_over_4MiB]
evidence_refs: [.ai/runtime/tmp/rag-gaps-live-maintenance.json, .ai/runtime/tmp/rag-gaps-auto-live.json, .ai/runtime/tmp/rag-gaps-history-live-result.json]
changed_paths: [tools/provider_rag_collection.py, tools/provider_session_observer.py, tools/universe_app/memory_source_window.py, tools/universe_server.py, tools/universe_ui/app.js, tests/test_provider_rag_collection.py]
```


후속: 위 자동 모델 출처 실패는 [근거 번호 계약 보완](rag-reference-contract-20260912.md) 후 같은 run의 attempt2에서 성공했다. 원래 실패 증거는 유지하며, 입력 참조 형식 보완과 요약의 의미적 사실성 검증을 구분한다.
