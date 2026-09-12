# RAG 대화 소스 확장 — 2026-09-12

## 적용 범위

등록되고 활성화된 Codex·Claude·Grok 대화 기록을 RAG 추출 입력으로 받는다. 자동 선택과 수동 실행이 동일한 입력 제공자 목록을 사용한다. 추출을 수행하는 모델은 기존 CODEX/gpt-5.6-luna/MAX이며, 입력 제공자와 별도로 기록한다. 다른 추출 모델 지원을 의미하지 않는다.

원인: memory_fast_extract.redact_activity_batch와 memory_source_window.select_source_window가 추출 모델 제공자 상수를 입력 소스 제한에도 사용했다. Observer는 이미 세 제공자의 기록 읽기를 지원했다. 입력 허용 목록을 별도 상수로 분리했다.

Claude 상태 표시는 부모를 비활성화하고 말단 이벤트만 남긴다. 이를 그대로 RAG에 사용하면 이전 대화가 누락된다. 상태 표시 계약은 유지하고, RAG 배치와 근거 검증에서 최신 이벤트의 parentUuid 연결을 따라 관측된 조상을 포함한다. 이전 분기의 근거를 다시 제출하면 SEMANTIC_ACTIVITY_NOT_ATTESTED로 거부한다. 기록 중간부터 관측한 소스는 DB에 존재하는 조상까지만 읽는다. 미관측 과거 기록을 자동 복원하지 않는다.

Claude·Grok의 본문은 명시적 텍스트 블록만 읽는다. 도구 결과·도구 입력·생각 블록의 중첩 content를 사용자 발언으로 끌어오지 않는다. 기존 비밀값 마스킹, 발췌 예산, 근거 검증, 검토 후 채택 절차는 유지된다.

## 검증

- Observer 23건, source window 13건, FAST_EXTRACT 6건, Runtime 준비 7건: 총49건 PASS.
- 세 제공자 자동 선택, 알려지지 않은 제공자 거부, 실제 Observer→배치 정규화→엄격한 본문 검증, Claude 최신 분기 조상 포함/버려진 분기 거부, 도구·생각 제외를 검증했다.
- 일부 기존 테스트 종료에서 ResourceWarning 및 remote_gateway 정리 PermissionError가 출력됐으나 테스트 결과는 PASS였다.
- 실서비스 수동 FAST_EXTRACT: memory_batch_run_effe77f9a80fc476c2fb9404, COMPLETED, 약41초, 후보5개 신규 생성. 자동 채택 없음.
- 실제 입력: CLAUDE source_470d700869404aeba247cd7472efb043 및 GROK source_3a1f53860ccb46c2b25530ba41d5e934. 호출 전 엄격한 근거 읽기에서 각각 활동9/5개, 발췌1/7개, 본문1,649/6,527자를 확인했다. 활동에는 본문이 없는 상태 이벤트도 포함된다.
- 실행 제공자 CODEX, 모델 provider://CODEX/model/gpt-5.6-luna, effort MAX. 실행 영수증 codex-app-server:01a095cb-4355-7812-929d-b4d984970719:universe-runtime-host:54e7f49df57e460894ef1a34bde8b911.
- 로컬 실행 증거: .ai/runtime/tmp/rag-multi-source-selection.json 및 rag-multi-source-live-result.json. 원문은 문서나 결과 기록에 복사하지 않았다.

## 남은 범위

이번 실서비스 검증은 명시적으로 지정한 두 소스의 혼합 수동 실행이다. 자동 예약의 세 제공자 전체 순환은 이번 live 검증 범위가 아니다. 자동 경로의 선택/활동 구간은 테스트로 검증했다. 명시적 소스 지정 실행은 기존 본문 병합 경로를 사용하므로, 위 사전 엄격 발췌 수가 실제 모델 입력량과 같다는 의미는 아니다.

UNKNOWN 소스의 형식/위치 복구, 미관측 과거 기록 수집, 단일 활동32,000자 초과 분할, 원문 기반 의미 검증과 실제 AI 통합은 별도 후속이다. 전체 RAG 완료나 추출 내용의 사실성 검증을 선언하지 않는다.
