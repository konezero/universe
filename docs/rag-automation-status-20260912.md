# RAG 자동화 상태 (2026-09-12)

## 현재 동작

프로젝트 실행 세션을 사용자가 지정할 필요가 없다. 예약 배치와 ‘지금 실행’은 같은 Host 준비 경로를 사용한다. Host가 프로젝트의 선택된 Runtime으로 배치 전용 실행 프로세스를 시작하고, 저장된 활성 설정 ID/revision에 근거한 새 instruction-v2 Task Frame과 turn을 만든다. 후보 추출이 끝나거나 실패하면 프레임과 프로세스를 정리한다.

기존 Master/provider 세션을 선택하거나 Supervisor에 가짜 세션을 등록하지 않는다. Mode Current Anchor도 변경하지 않는다. UI는 ‘배치가 실행될 때 자동으로 준비하고, 끝나면 정리합니다.’로 표시한다. API의 준비 상태는 ON_DEMAND이며 interactive_session_required=false다. 이는 준비 방식이며 Provider의 향후 성공을 보장하는 상태는 아니다.

자동 입력은 활성 등록 소스를 순환하며 한 번에 최대 64개 소스, 256개 발췌, 32,000자 안에서 수집한다. 성공한 실행에만 다음 소스 위치를 저장한다. 빈 소스와 원문 위치를 검증할 수 없는 소스는 사유를 남기고 건너뛴다. 명시적으로 지정한 소스의 검증 오류는 그대로 반환한다. 화면에서 최근 수집·다음 배치·제외 개수와 제외 이유를 확인할 수 있다. 이 커서는 소스 순환 위치이며 전역 이벤트의 완전 수집을 증명하지 않는다.

검토·RAG 채택·노드 연결 확정은 각각 별도 결정이다. 이번 실제 실행은 REVIEW_REQUIRED 후보만 만들었다. 후속 중복 정리·분류·품질 점검의 현재 설정은 규칙 기반이며 AI 추출 성공과 구분한다.

## 확인한 원인과 수정 소유 경계

- 이전 준비 어댑터는 정확히 하나의 LIVE 프로젝트 Host attachment를 요구하여 열린 세션이 없으면 멈췄다. Universe의 배치 실행 수명과 비공개 자격증명 경계를 담당하는 UniverseBatchRuntime/MemoryBatchRuntimePool로 교체했다.
- 한글 원문의 semantic text digest는 Observer의 JSON ASCII escaping과 소비자의 Unicode 직렬화가 달랐다. producer/consumer가 공통 semantic_text_digest를 사용하도록 수정했다. Activity 해시와 기존 원문은 재작성하지 않았다.
- 기본 예약 입력은 등록 소스 전체 377개를 최대 64개 계약으로 넘겼다. 자동 소스 선택을 제한하고 성공 후 순환 위치를 저장하도록 바꿨다.
- 실제 기본 추출 1차는 FAST_EXTRACT_RESULT_VERBATIM_FORBIDDEN으로 거절됐다. 기존 원문 복사 검증을 유지하고, 출력 계약에 한글 재서술·연속 12단어 복사 금지를 명시했다. 같은 입력의 2차 실행은 통과했다. 과거 원문 위치 불일치는 수집 이력 문제로 분리하며 이번 변경이 원본 이력을 복구했다는 뜻은 아니다.

## 검증 근거

- PASS: 관련 Python 회귀 49건. 배치 수명/자격증명, 준비 프레임, 예약 경계/스케줄러, 입력 제한/커서, Unicode 원문 검증을 포함한다. 출력 지시 보완 후 영향 범위 12건도 PASS(앞선 집계와 중복).
- PASS: 실제 배치 Runtime 시작→CURRENT/LIVE→종료. Mode Anchor 테이블 전후 동일. 실행 종료 후 배치 Python 프로세스 0개, Supervisor의 batch-runtime provider 세션 0개.
- PASS: 실제 단일 소스 Provider 추출. run memory_batch_run_cbdbaee3c355438e3d4ee0d9, CODEX/gpt-5.6-luna, 약 24초.
- PASS: source_ids/runtime_binding 없이 공식 memory.batch.run Action 실행. run memory_batch_run_865b57d87e340a399172fd63, attempt=2, COMPLETED, 약 47초. 한글 MEMORY 후보 3개 생성. 소스 1개 수집, 빈 소스 2개 제외, 활성 소스 283개 다음 배치로 연기. 다음 위치 source_0394e8578c8e488aa55bd654b3738de7 저장과 API 재조회 일치.
- 실제 호출 영수증: codex-app-server:01a09318-cbfa-7f50-9672-f3f58c7d7d7b:universe-runtime-host:aa4ffcb32c484c758050a2ab396b8032. 로컬 실행 증거: .ai/runtime/tmp/rag-owned-default-retry-result.json.
- PASS: node --check, hover Memory UI 회귀, git diff --check. 실제 메모 화면에서 자동 준비 안내 확인. 서비스 전용 재시작 후 READY.

## 남은 검증과 이력

실제 시계가 예약 시각에 도달하여 자동 발화하는 것과 일일 한도 소진/재시작 복구의 live 검증은 NOT_RUN이다. 예약 콜백은 검증한 공통 경로를 사용하며 스케줄러 회귀로 확인했다. 실제 사용자 후보의 검토→채택→노드 연결→검색까지의 변경 검증 역시 NOT_RUN이다. 따라서 전체 RAG/Collector TODO는 DONE으로 처리하지 않는다.

이전 ‘사용자 프로젝트 세션 필요 / 준비 어댑터 미구현’ 설명은 현재 동작이 아니다. 1차 UI 체크포인트는 3b11324394e3182c399ba9dac6af0a92ab5c074c이며, 배치 소유 실행 후속 변경은 b8fa69560d52985aae34843cff972b493a540f2f로 커밋했다. TODO: todo_rag_scheduled_frame_preparation_20260912, 상위 todo_c272adb801ac45348d0315e36c2d07c6.

## 커밋 후 수동 실행에서 확인한 후속 수정

다음 커서의 source_0394e8578c8e488aa55bd654b3738de7는 실제 등록상 GROK이다. 자동 소스 선택은 이를 포함했지만 기존 redact_activity_batch 계약은 Codex Activity만 허용하여 FAST_EXTRACT_PROVIDER_INVALID로 provider 호출 전에 실패했다. 커서는 이전 성공 위치를 유지했다. 추출 모델 설정(CODEX/gpt-5.6-luna) 오류가 아니라 입력 소스 지원 범위와 자동 선택의 불일치다.

자동 선택에서 기존 FAST_EXTRACT_PROVIDER 계약과 다른 소스를 제외 사유와 함께 건너뛰도록 수정했다. UI는 ‘현재 추출기가 지원하지 않는 소스’로 표시한다. Grok/Claude 추출 지원을 추가한 것은 아니다. 명시적 source_ids 요청의 기존 검증은 유지한다. 혼합 공급자와 지원 소스가 전혀 없는 경우를 포함한 관련 21건 회귀 PASS, JS 문법/diff 검사 PASS.

추가 실서비스 추적에서 Observer의 activity_refs 최대 512개를 넘는 소스가 자동 준비 단계의 SEMANTIC_EVIDENCE_INVALID를 발생시켰고, 이 단계의 예외가 HTTP 오류 변환 밖에 있어 연결만 닫혔다. 자동 선택은 이를 FAST_EXTRACT_SOURCE_TOO_LARGE로 보고하고 건너뛰며, 예상 밖 Observer/추출 검증 오류는 원래 code/detail을 보존한 UniverseError로 반환한다. 512개 경계·혼합 소스·오류 전달을 포함한 관련 23건 PASS. 큰 소스 내부의 활동 단위 분할 수집은 아직 구현하지 않았다. 해당 소스는 일부를 몰래 잘라서 완료 처리하지 않으며 UI에서 분할 필요로 보인다. 전체 Collector 완료 증거는 아니다.


후속 세 배치의 실제 수동 실행·재실행, Bench API/UI 검증 및 모델 편차 대응 계획은 [RAG·Bench 수동 검증](rag-bench-manual-audit-20260912.md)을 참조한다. 실행 성공과 내용 품질 검증은 구분한다.


## 검토량 감소 1차 구현

메모 화면을 ‘결정할 것 / 새로 모은 것 / 자동 정리한 것’으로 나눴다. 같은 프로젝트·종류의 동일 요약은 Unicode/공백 정규화 후 접힌 묶음으로 표시한다. 대소문자와 구두점은 기술 식별자 의미 보존을 위해 유지한다. 데이터 병합이나 자동 검토/채택은 수행하지 않는다. 원본과 통합 단계 후보는 펼쳐서 개별 확인할 수 있다.

등록된 CONFLICTED/CONFLICTS_WITH, provenance ref_digests 누락, KEEP, 미분류 검토 상태, 기존 SYNTHESIZE 생성기의 정형 문구를 ‘결정할 것’으로 우선 표시한다. 충돌 추론이나 사실성 검증 모델을 추가한 것은 아니다. IGNORE/SUPERSEDED와 저장 메모는 접힌 이력에 보존한다. 검색과 최대200개 후보 범위를 명시한다.

검증: 실제 화면 후보48개가27묶음으로 표시됨. 결정할 후보8묶음+연결제안8개, 정리19묶음. 검색 후1묶음만 표시, 펼치면 원본/통합2개가 보임. 신규 분류 회귀(충돌 우선·출처·placeholder·보관·프로젝트/종류/대소문자 분리·원본 불변)와 기존 hover 검토/채택 회귀 PASS. node 문법 및 diff 검사 PASS. 사용자 후보 결정은 변경하지 않았다.

후속: 의미상 유사한 내용의 통합 제안, 실제 원문 기반 충돌/근거 평가, 일일 변경분 요약과 여러 항목 검토 Action. 이번 화면 묶기만으로 전체 검토 자동화 완료를 선언하지 않는다.


## 일일 변경 요약 1차

검토함 묶기와 감사 문서는 49891e7로 커밋했다. 후속으로 화면 조회 시 브라우저 시간대의 오늘0시부터 조회시각까지 새 후보/기존 후보 변경/새 저장 메모/기존 메모 변경을 구분하는 ‘오늘의 메모 요약’을 추가했다. 미처리 확인 대상은 이전 날짜도 포함하며, 오늘 변경만 보기와 전체 기간 보기를 전환할 수 있다. 묶음에서 오늘 변경된 항목이 하나라도 있으면 원본 비교를 위해 전체 묶음을 유지한다.

집계는 현재 불러온 목록의 created_at/updated_at 기준이며, 이벤트 이력의 변경 종류나 삭제 수를 추정하지 않는다. 날짜가 없거나 잘못된 항목은 제외 수를 표시하고, 미래 시각은 오늘 결과에 포함하지 않는다. 최대200개 후보 제한을 그대로 명시한다. 전체 데이터의 서버 일일 보고서나 예약 메시지 발송 기능은 아니다.

검증: 날짜 경계/신규·수정 중복 방지/잘못된 날짜·미래 시각/빈 입력/원본 불변 테스트 PASS. 기존 묶기·hover 검토/채택 회귀, JS 문법 및 diff 검사 PASS.


대용량 활동 소스의 후속 분할·이어 수집 구현과 검증 범위는 [활동 구간 수집](rag-activity-window-20260912.md)을 참조한다. 기존512개 초과 소스 전체 제외의 한계를 보완하며, 단일 활동 본문 초과는 별도로 남는다. 비-Codex 입력은 이후 [Claude·Grok 소스 확장](rag-multi-source-20260912.md)에서 구현했다.


## Claude·Grok 입력 지원

등록된 활성 Codex·Claude·Grok 대화가 수집 대상이다. 추출 모델은 CODEX/gpt-5.6-luna/MAX로 유지한다. Claude 최신 대화 조상 포함 및 도구/생각 제외를 보완했다. 관련49건 PASS, 실제 혼합 수동 추출41초/후보5개 생성. 상세 근거와 한계는 [소스 확장 검증](rag-multi-source-20260912.md)을 참조한다. 이전의 비-Codex 입력 미지원 기록은 이 변경으로 대체한다.
