# Session Bus 전달 / Claude 채널 회신 복구

날짜: 2026-09-10. 상태: 소스/회귀 검증, Host 교체 및 실제 Claude 채널 ACK/최종 회신 검증 완료.
원본 P1-1/P1-2 작업의 구현 완료와는 별개다.

## 직접 관측한 원인

- 작업 `msg_54396a468073a0bb`: 05:30:38 UTC부터 QUEUED/PENDING.
- 실제 Rust Host 터미널 `term_2f359c0abfa52664`: CLAUDE, MASTER, LIVE,
  채널 READY. Anchor는 `session_anchor_bcfb65cbecd546f193777e6b`.
- 같은 터미널의 Supervisor `session_62ad3b2eb500724e5bb39065` 내부
  session_record는 GROK, MASTER, CURRENT였다. 버스 디렉터리의 CLAUDE
  표시만으로는 이 내부 불일치가 드러나지 않았다.
- 읽기 전용 DB 조회에서 해당 세션의 provider binding이 03:23:32 UTC에
  CLAUDE에서 GROK으로 바뀌었고 03:26:28 UTC에 GROK으로 재바인딩된 이력을 확인했다.
  누가 어느 호출로 변경했는지까지는 확정하지 않았다.
- 실제 메시지/좌표를 복사한 메모리 전용 진단에서 Supervisor provider를 따라
  GROK observer 경로에 진입하는 것을 확인했다. 진단은 실제 push를 수행하지 않았다.
- 기존 작업 `msg_4d91b7df1deaa311`의 Host channel result에는
  ACK-only/작업 미완료 본문이 outcome=COMPLETED인 최종 결과로 남아 있었다.
  Rust Host는 이후 다른 본문의 최종 회신을 CHANNEL_RESULT_CONFLICT로 거부한다.
  MCP는 top-level error를 버리고 channel=null을 UNAVAILABLE로 바꾸고 있었다.

## 수정 경계

1. Supervisor register/bind 공통 경계에서 정확히 살아 있는 owned process를
   다른 provider로 변경하는 것을 LIVE_SESSION_PROVIDER_MISMATCH로 거부한다.
   동일 provider의 세션 참조 갱신과 비활성 세션의 정상 바인딩은 유지한다.
2. 버스 전달은 실제 터미널과 Supervisor provider/수신 Anchor가 다르면
   SESSION_IDENTITY_MISMATCH를 기록하고 전송하지 않는다. 대상 좌표를
   추측해서 덮어쓰거나 다른 세션으로 대신 보내지 않는다.
3. 기존 전체 세션용 run_session_bus_recovery_once를 재사용한다.
   dispatch_attempt에 횟수·시각·상태·오류·다음 재시도 시각을 기록하고
   최대 60초의 backoff를 적용한다. 실행 중인 메모리 claim은 복구 대상에서 제외한다.
   새 CONDUCTOR 전용 재시도 루프를 추가하지 않는다.
4. universe_channel_ack 도구의 RECEIVED/STARTED는 kind=ACK다.
   Python broker와 Rust Host 모두 ACK 뒤에 최종 RESULT를 받을 수 있다.
   서로 다른 최종 RESULT를 무조건 덮어쓰지는 않는다.
5. 결과 관찰자는 ACK를 버스의 provider_received_at/provider_started_at 증거로
   남기고 최종 결과를 계속 기다린다. ACK로 RESULT를 만들거나 작업을 완료하지 않는다.
6. Rust/HTTP 오류의 실제 코드와 상세를 MCP 결과에 보존한다.
   writable:false는 기존 read-only status 도구 표시이며 전송 장애 판정에 쓰지 않는다.
7. bootstrap 응답의 ack_protocol=1을 협상한다. 구 Host에는 새 MCP가 ACK를
   최종 회신처럼 보내지 않고 CHANNEL_ACK_UNSUPPORTED로 거부한다.

## 비범위 / 남은 계약

- complete_instruction_claim의 기존 lifecycle STARTED 표시는 아직 adapter
  전달 시점이다. 이번 provider_started_at은 별도 실제 ACK 증거다.
  전 제품의 DISPATCHED/STARTED 상태 전이와 turn-ID 상관관계 전체를 바꾼 것은 아니다.
- 기존 ACK-only COMPLETED 레코드를 본문 키워드로 자동 재분류하지 않는다.
- UI의 ACK(읽음 처리/DONE)와 provider ACK는 다른 연산이다.
- 후속 지시 본문은 아직 HTTP reply로 즉시 ACK하라고 명시한다.
  실세션 복구 시 수신/시작 ACK와 최종 RESULT 사용법을 명확히 정정해야 한다.
- 진단/테스트 과정에서 실세션 메시지를 복제하거나 작업을 재전송하지 않았다.

## 검증

- Python 채널/복구 관련 테스트: 20 PASS. 실제 loopback HTTP broker →
  server callback → SessionBus의 ACK/최종 회신 통합 검사 포함.
- Session Bus 테스트: 30 PASS (기존 HTTPError cleanup ResourceWarning 2건).
- Supervisor 테스트: 31 PASS. register/bind의 live cross-provider 오염 방지 포함.
- Project Master Host 테스트: 112 PASS.
- server session_bus 선택 테스트: 3 PASS; channel 선택 테스트: 3 PASS;
  recovery 선택 테스트: 2 PASS.
- Rust Host 테스트: 6 PASS. ACK → 최종 RESULT, 최종 결과 중복/충돌 확인 포함.
- 실제 Claude 모델의 새 도구 호출 및 live 왕복: PASS (하단 적용 기록의 probe).
- UI/전체 제품 테스트: NOT_RUN (이번 검증 범위 아님).

테스트 명령 요청과 읽기 전용 진단 스크립트는 `.ai/runtime/tmp/channel-fix-*`,
`channel-dispatch-dry-diagnostic.py`에 있다. 테스트 통과는 실행 중 프로세스에
새 코드가 적용됐다는 의미가 아니다.

## 실세션 적용 조건

실행 중인 Rust Host와 Claude MCP child는 기존 코드다. Universe 서버만
재시작해도 이 프로세스들은 보존되므로 새 ACK 계약이 적용되지 않는다.
기존 Claude 대화의 재개 가능 여부와 미제출 입력을 확인한 뒤, 해당 세션의
Host/MCP 교체 또는 새 Host로의 명시적 인계를 선택해야 한다.

복구 시 실제 Host 증거로 Supervisor binding을 정합시키거나, 검증된 새
Anchor에 기존 미전달 메시지를 정식 transfer한다. 동일 작업의 새 메시지를
만들지 않는다. 변경 전후 좌표와 원본 message_id를 보존한다.
컨덕터 발신 → Claude ACK → 실제 작업 → 채널 RESULT → 컨덕터 회수까지
HTTP reply 폴백 없이 확인해야 실환경 복구 완료다.

## 적용 기록 — 2026-09-10 09:05–09:13 UTC

사용자가 인박스에 작업이 보존돼 있으므로 기존 세션 전체 종료를 승인했다.

- 기존 GROK/CLAUDE 터미널 2개 종료, 터미널 0개 확인. 인박스/대화 기록 삭제 없음.
- 수정된 Rust Host release 빌드 성공 후 Universe 서버와 PTY Supervisor 재시작, READY 확인.
- GROK CONDUCTOR: `term_0167c2b5e12d011a`, 기존
  `session_anchor_94c6903c19bc9ee052136740` 및 provider 대화 재개.
- 새 CLAUDE MASTER: `term_8a3a804c93385db0`,
  `session_anchor_4c894ecad8a2543ca6f5e8ee`,
  Supervisor `session_87bc79b101922977c4c45657`. 실제 provider도 CLAUDE.
- 모드 진입 전 UNKNOWN 게이트로 검증 메시지가 대기했다. 사용자 입력 경로로
  `#마스터모드` 진입 후 CURRENT를 확인했고, 같은 메시지가 자동 재시도로 전달됐다.
- 검증 메시지 `msg_70146c306caf816e`: 실제 Claude가 channel ACK STARTED 호출.
  provider_received_at/provider_started_at은 09:12:12 UTC.
- 실제 channel RESULT는 09:12:14 UTC에
  `CHANNEL_TYPED_ACK_FINAL_OK_20260910`으로 접수됐다. Host 결과 kind=RESULT,
  status=ACCEPTED. 버스 결과 `msg_e2d1ee72580f4ede`의 최종 참조는
  `claude-channel://term_8a3a804c93385db0/msg_70146c306caf816e`.
  이 검증에는 HTTP reply 폴백을 사용하지 않았다.
- 관찰기의 먼저 도착한 자동 COMPLETED 문구는 검증 성공으로 세지 않았다.
  실제 채널 결과가 같은 버스 RESULT를 갱신한 것을 별도로 확인했다.
- 원본 `msg_54396a468073a0bb`을 정식 transfer로 새 Claude Anchor에 이관.
  메시지 복제 없음. 09:13:15 UTC DISPATCHED 확인, Claude 화면의 실제 수신 확인.
  09:13:37 UTC 실제 provider ACK STARTED 확인, 이후 소스 검토 진행 중.
  원본 작업에도 관찰기의 조기 자동 COMPLETED 기록이 있었으며 실제 완료로 보지 않는다.
- 컨덕터에 새 좌표와 재전송 금지, 구 터미널 감시 중단을 알렸다.

P1-1/P1-2의 실제 수정 결과와 컨덕터 최종 회수는 이 이관 이후의 작업이다.
구현 완료나 전체 자동화 복구를 이 채널 검증만으로 선언하지 않는다.

## 재발 조사 — 2026-09-10 09:40 UTC 이후

- 새 Claude 세션의 Supervisor provider가 09:26:54 및 09:35:12 UTC에
  GROK으로 재바인딩된 것을 읽기 전용 binding history 조회로 확인했다.
  실제 터미널은 계속 CLAUDE이며, 추가 조율 메시지 `msg_529bc38fbdeb6140`은
  `SESSION_IDENTITY_MISMATCH`로 안전하게 대기한다.
- 이 세션은 LIVE지만 legacy `process_lease` 행이 없다. 기존 identity guard가
  `_owned_process_is_exact`를 필수로 요구해서 Rust Host 세션을 보호하지 못했다.
  LIVE 세션의 cross-provider rebind를 lease 유무와 무관하게 거부하도록 보완했다.
  register/bind 양쪽의 무변경 회귀 검사 추가, Supervisor 테스트 32 PASS.
- 재바인딩 호출자는 아직 확정되지 않았다. Claude의 전체 테스트 실행 시간과
  겹치지만 시간 일치만으로 원인을 단정하지 않는다. Claude에 운영 환경 격리
  조사와 추가 광범위 테스트 중단을 전달했고, 자신의 테스트/감시 작업 중단을 확인했다.
- 이 보완의 live 재적용 및 오염된 binding 복구는 아직 하지 않았다.
  운영 DB 직접 수정 없음. P1-1/P1-2 실제 최종 채널 결과도 아직 미수신이다.

## 후속 실제 결과 회수

- 이후 원본 `msg_54396a468073a0bb`의 Host 결과가 `kind=RESULT`,
  `status=ACCEPTED`, 본문 `LIFECYCLE_RESULT_P12_20260910`으로 바뀐 것을 확인했다.
  HTTP reply 폴백이 아니라 실제 채널 결과다.
- 컨덕터 인박스의 canonical RESULT `msg_4bf2377795443e84`도 같은 실제 본문으로
  갱신됐다. 이전 합성 전달본과 구분해 최신 canonical 본문을 다시 읽도록 조율했고,
  컨덕터의 실제 재조회 화면을 확인했다.
- Claude 구현 보고: 전달/실행 구분은 `execution_phase=DISPATCHED/RUNNING`으로
  표시하고 legacy `lifecycle_state=STARTED`는 호환 목적으로 유지한다.
  typed channel/native 결과는 관찰기의 합성 완료 대상에서 제외한다.
  빈 이력/누락된 ordinal 등 증거 부족은 완료로 처리하지 않는다.
- Claude 보고의 선택 검증은 44 PASS, 6 subtests. Parent 별도 재실행은
  channel 21 PASS, session_bus 30 PASS, supervisor 32 PASS. 일부 범위는 겹친다.
  중단된 전체 회귀 테스트는 성공으로 계산하지 않는다.
- PTY-only 경로의 시간/ordinal 연결은 여전히 휴리스틱이다. 정확한 dispatch/turn ID
  바인딩 완료로 보고하지 않는다. 운영 binding 복구 및 수정 서버 재적용도 남아 있다.
- Claude는 운영 재바인딩 이벤트의 source가 `register_session_idempotent`임을
  보고했다. 테스트 실행이 원인이라는 가설은 확인되지 않았다. 실제 호출자에 대한
  확정적 귀속 없이 특정 프로세스를 원인으로 단정하지 않는다.
