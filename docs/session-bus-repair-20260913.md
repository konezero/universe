# 세션 버스 복구 검증 — 2026-09-13

## 확인된 원인과 수정

1. 정확한 수신 세션을 찾은 뒤에도 `session_record.currentness == CURRENT`를 요구했다. 이 값은 프로젝트/모드의 최근 활동 순위이며 복수 Master의 큐 수신 자격이 아니다. 이제 살아 있는 세션의 provider·프로젝트·모드·앵커를 대조하고 활동 순위로 제외하지 않는다.
2. Codex 초기화는 입력 문구가 처음 그려지는 순간 Enter를 보낸 뒤 성공으로 기록했다. 실제 재현에서는 입력창에 문구가 남고 훅 시간 초과 복구가 Ctrl+C를 보냈다. 초기화도 일반 prompt 제출기의 안정화·효과 검증을 사용한다.
3. Grok에서 출력 증가만으로 전달 성공을 기록하고, 입력창에 남은 문구를 재시도 때 덧붙일 수 있었다. 현재 입력창에 같은 문구가 남으면 성공으로 인정하지 않으며 재시도는 이미 입력한 문구를 제출한다.
4. PTY를 관측하지 않는 다른 Runtime의 전역 sweep이 lease 없는 살아 있는 세션을 DISCONNECTED로 바꿨다. 미관측 `None`과 관측 결과가 빈 `{}`를 구분한다. 살아 있다는 증거를 만들거나 currentness를 강제로 바꾸지 않는다.
5. 큐 복구가 provider 기록 수집·resident 정리와 같은 maintenance 루프에 묶여 지연됐다. 별도 5초 루프로 분리했으며 기존 메시지/backoff를 유지한다. 동시 훅·복구의 한 세션 turn claim은 같은 잠금 안에서 원자적으로 예약한다.
6. 빈 PTY를 20ms마다 새 TCP 연결로 조회했다. 현장에서 TIME_WAIT 37,743개와 Claude ACK의 WinError 10048/10053을 관측했다. PTY read는 요청된 대기 구간당 한 번 조회하고, 빈 출력의 liveness 조회는 초당 한 번으로 제한한다. 채널 poll과 결과 조회도 각각 0.5초/1초로 조정했다. 시간 경과에 따른 연결 감소는 관측했지만 무제한 세션 수의 부하 시험을 의미하지 않는다.
7. Claude 채널 상태는 실제 회신 기능이 있어도 writable:false로 표시했다. 실제 token/ACK 협상 결과에 따라 reply_supported/ack_supported를 반환한다. TCP/HTTP 실패는 operation·endpoint·error_code·detail을 보존한다. 동일 ACK/결과만 제한적으로 재시도하며 event poll이나 터미널 write는 자동 중복 전송하지 않는다.
8. Grok이 Claude 호환 MCP 설정에서 Claude 전용 회신 도구를 사용했다. 다른 provider에는 이 도구를 노출하지 않으며 Codex/Grok bus 입력에는 정확한 HTTP reply 경로와 수신 좌표를 포함한다.

## 실제 관측

- Universe Codex: `term_1f50e7ffc75acff0`에서 실제 SESSION_READY, provider ID/훅 바인딩, 원래 RAG 작업 `master_msg_1799c3b422024e3f86bc521ce16c1c53`의 PROCESSING(CODEX)을 확인했다.
- Career Claude: `term_4e8af5db83924b7b`가 원래 RAG 작업 `master_msg_862f458136754eab9a2bbec9d89122fa`를 PROCESSING(CLAUDE)으로 claim했다. 시작 당시 currentness는 UNKNOWN이었다.
- Career Grok: `term_9d20db7264c93efe`에서 실제 SESSION_READY와 바인딩, 큐 알림 처리 및 MASTER_MESSAGE_QUEUE_EMPTY 응답을 확인했다. Claude가 이미 claim한 RAG 작업은 중복 claim하지 않았다. 기존 입력창 잔류 메시지는 같은 문구 재제출로 복구했다.
- Claude 왕복: `msg_8faee7dab07bcec2`에 RECEIVED/STARTED ACK가 기록됐고 2026-09-13T04:12:12Z에 REPLIED가 됐다. 최종 결과 `msg_42813e9f4a702ea9`의 본문은 SESSION_BUS_ROUNDTRIP_OK이다. 실패한 앞선 진단 메시지는 FAILED로 기록했고 성공으로 바꾸지 않았다.
- 기존 Rust Host 소유 세션을 보존한 채 Supervisor/Universe 서비스를 재시작해 Python 변경을 반영했다. Rust 소스/바이너리는 이번 수정 대상이 아니다. `/1` 호환성 표시는 빌드 해시 검증이 아니다.

## 회귀 검증 범위

- provider 3종 × CURRENT/STALE/UNKNOWN, 잘못된 프로젝트·모드·provider·앵커, 종료 세션
- 프로젝트별 큐, 동시 Master claim 1회, 같은 세션의 동시 instruction claim 1회
- 빈 PTY 구간의 IPC 요청 수, 초기화 결과 구분, composer 잔류와 중복 paste 방지
- 미관측 sweep과 빈 PTY 관측 구분, 실제 provider 교체 전 명시적 생존 확인
- 채널 ACK와 최종 결과 분리, 오류 원인 보존, idempotent ACK 재시도와 poll 비재시도
- 관련 Python suite와 기존 서버 instruction/reconnect/wake 회귀를 실행했다. 상세 실행 결과는 작업 기록에 보존한다.

RAG 작업의 PROCESSING은 RAG 분류 자체의 완료를 뜻하지 않는다. 이번 작업은 세션 시작·수신·회신 경로를 복구하는 범위다.

## 추가로 확인한 공통 결함과 최종 검증

- 같은 DB의 서버 두 개(PID 29652/61075, PID 35824/54642)가 동시에 관측됐다. 서버별 메모리 큐가 달라 Grok의 회신이 다른 서버에서 BUS_MESSAGE_NOT_FOUND로 실패했다. DB별 OS 파일 잠금으로 초기화부터 종료까지 단일 소유권을 유지한다. 종료 시 state 삭제만으로 이전 PID 종료를 인정하지 않고, Windows 프로세스 조회 권한 거부도 종료로 간주하지 않는다. 준비 중인 live 서버에는 추가 start를 하지 않는다. 서버 생성자의 동기 큐 복구도 별도 복구 루프로 옮겼다.
- 실패한 Grok 메시지 `msg_ca70fd41b69ee668`는 provider 턴 종료만으로 COMPLETED가 합성됐다. 이 결과는 성공 증거에서 제외한다. 새 Codex/Grok PTY 전달에는 SESSION_BUS_HTTP와 awaits_authoritative_reply를 저장해 실제 HTTP 회신만 최종 결과로 인정한다.
- 긴 Master 작업도 provider만 기록한 lease가 만료돼 재배정됐다. 관리 세션의 큐 알림은 정확한 terminal/anchor를 포함하고 서버가 live 프로젝트 Master와 대조한다. 저장한 정확한 앵커가 Supervisor에서 LIVE이면 임대를 유지하고 연결 종료 후에는 회수한다. CURRENT 순위는 사용하지 않는다. 내부 Store의 레거시 직접 소비자는 수동 lease 갱신 계약을 유지한다. 외부 HTTP claim은 terminal_id/session_anchor_ref가 필수이며 누락 시 MASTER_MESSAGE_OWNER_REQUIRED로 거부한다.

실제 최종 왕복:

| Provider | 지시 | 실제 결과 | 본문 |
|---|---|---|---|
| Claude | msg_8faee7dab07bcec2 | msg_42813e9f4a702ea9 | SESSION_BUS_ROUNDTRIP_OK |
| Grok | msg_824b4f43b3f7593a | msg_cd1fbb18f6659916 | SESSION_BUS_GROK_ROUNDTRIP_OK |
| Codex | msg_3cff158b4965343d | msg_84414d0dbe10a0fb | SESSION_BUS_CODEX_ROUNDTRIP_OK |

Grok은 2026-09-13T04:34:53Z에 실제 HTTP reply가 저장됐다. Codex도 별도 진단 세션에서 HTTP reply를 저장했다. provider 턴 종료의 합성 결과로 대체하지 않았다.

원래 RAG 두 작업은 처음 PROCESSING을 관측한 뒤 TTL 회수로 QUEUED가 된 사실도 확인했다. 원래 실행/질문 대기 중인 세션의 좌표를 검증하고 정확한 message_id를 지정해 그 세션으로 다시 연결했다. 이 복구는 다른 큐 항목을 가져오거나 새 RAG 작업을 실행하지 않는다. 작업을 DONE으로 처리하지 않았다.

최종 배포: Universe PID 56768 READY, Supervisor PID 47852 READY. 실제 두 번째 serve 실행은 UNIVERSE_SERVICE_ALREADY_RUNNING으로 exit 1이었으며 기존 PID/state와 health READY가 유지됐다. 완료한 Claude 진단 2개, Codex 진단 1개, Grok 진단 1개만 종료했다. 기존 RAG 두 세션과 사용자 세션은 유지했다.

## 실행한 회귀 검증

| 범위 | 결과 |
|---|---|
| terminal_host | 49 PASS |
| reconnection_host | 13 PASS |
| session_supervisor | 32 PASS |
| session_inject_hook | 35 PASS |
| master_message_queue | 23 PASS |
| universe_service_control | 17 PASS |
| session_bus_live_binding | 5 PASS |
| session_bus | 29 PASS |
| channel_dispatch_repair | 14 PASS |
| claude_channel_transport | 12 PASS |
| session_live_sweep | 9 PASS |
| 서버 Rust 전달/실제 회신 전 완료 금지 | 4 PASS |
| 서버 Master claim HTTP/잘못된 앵커 거부 | 4 PASS |
| 서버 live Master 알림 / ready-hook 큐 재시도 | 1 PASS / 5 PASS |

project_master_host 인접 suite는 최초 112건 중 변경된 생존 계약과 충돌한 1건을 확인했다. 해당 테스트를 명시적 종료 관측 후 교체 계약으로 수정하고 단독 PASS를 확인했다. 수정 후 전체 112건 재실행으로 표시하지 않는다.

전체 제품 benchmark, 모든 provider의 장시간 부하 시험, RAG 내용 정확도 검증은 이 표의 대상이 아니다. 이미 실행 중인 Claude MCP 자식 프로세스에는 poll 주기 변경이 소급 주입되지 않는다. 새 세션에 적용되며 기존 작업 세션은 보존했다. 완료한 진단 세션을 정리해 불필요한 poll 부하를 제거했다.

## 마지막 교차 세션 충돌 검증

부분 PTY 관측을 전체 inventory로 처리한 추가 원인을 확인했다. `perform_session_ref_inject`가 자신이 연결한 한 세션의 앵커만 `sweep_stale_live_sessions`에 넘겨 다른 live Master를 NO_PROCESS_LEASE로 DISCONNECTED 처리했다. 2026-09-13T04:26:41Z의 감사 이벤트에서 원래 Codex/Claude 두 세션의 잘못된 demotion을 확인했다. 이후 Codex의 동일 supervisor_session_id에 GROK 훅이 들어와 provider가 바뀐 이벤트도 확인했다.

이제 한 훅의 관측은 `inventory_complete=False`로 해당 세션만 복구한다. 다른 Host의 부재를 추론하지 않는다. 훅의 명시적 Supervisor ID가 live 터미널을 가리키면 provider/프로젝트/모드를 등록 전에 비교해 다른 좌표의 훅이 stale projection을 덮어쓰지 못하게 한다. provider 3종 간 6가지 잘못된 훅 조합과, 하나의 훅 후 세 provider의 LIVE 유지 회귀를 추가했다. sweep 9 PASS, Supervisor 32 PASS, hook 35 PASS, 서버 inject 4 PASS를 확인했다.

이미 손상된 Codex 행은 새 세션을 만들지 않고, 최초 검증된 provider identity와 계속 살아 있는 원래 Codex Host를 대조해 복구했다. `SESSION_HOST_IDENTITY_REPAIRED` 감사 이벤트를 남겼으며 currentness를 강제로 선택하지 않았다. 전체 live PTY inventory와 대조한 최종 상태는 원래 Codex=CODEX/LIVE, 원래 Claude=CLAUDE/LIVE이다. 두 기존 RAG 작업의 소유권과 PROCESSING을 다시 확인한다.

최종 HTTP 호환성 변경: `/v1/projects/{project_id}/master-messages/claim` 요청은 provider와 함께 terminal_id, session_anchor_ref를 보내야 한다. 관리 세션 알림에 이 JSON을 제공한다. 누락/불일치 요청은 claim 이전에 거부하며 provider 이름만으로 어느 Master인지 추측하지 않는다. 원래 Codex가 레거시 방식으로 갱신하던 claim에도 검증된 원래 앵커를 보완했으며, 최종 두 항목 모두 정확한 앵커가 있는 PROCESSING, 두 세션 모두 LIVE임을 확인했다. 관련 HTTP 4건과 discovery→claim 1건 PASS.

변경은 미커밋 상태다. 동시 진행 중인 다른 Master의 RAG 소스 변경은 보존했다. 최종 관측 원문은 `.ai/runtime/tmp/session-bus-repair-final-evidence.json`에 저장했다.
