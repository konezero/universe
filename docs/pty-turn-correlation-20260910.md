# PTY 작업–턴 연결: Codex 명시적 이벤트 경로

상태: SOURCE IMPLEMENTED / isolated tests PASS / Codex live deployment PASS.
Work Receipt: `work_4b93e8ec29674961ec41f284`.

## 확인한 사실과 범위

기존 PTY 디스패처는 `instruction_ref: session-bus:<message_id>`를 입력하지만,
관찰기는 이벤트 ID/순번만 보존하고 완료를 시간·순번으로 추정했다.
일반 의미 메시지의 COMPLETED 투영은 턴 종료 증거와 같지 않다.

실제 로컬 Codex rollout에서 다음 구조를 내용 없이 확인했다.

- `event_msg / task_started`: `turn_id`
- `event_msg / item_completed / UserMessage`: 같은 `turn_id`, text content
- `event_msg / task_complete`: 같은 `turn_id`

새 Codex Rust Host 입력 경로는 매 전달 시 `dispatch_<32 hex>`를 생성하고
첫 두 줄에 전달 참조와 정확한 bus message ID를 넣는다. Bus lifecycle에는
이 참조와 정확한 observer source ID를 기록한다.

관찰기는 UserMessage의 맨 앞 헤더에서만 ID를 추출한다. 동일 source/turn의
명시적 task_complete에 하나의 유일한 입력 연결이 있을 때만 연결을 전달한다.
Assistant 인용, 도구 출력, 다른 턴, 충돌하는 복수 입력은 연결 근거가 아니다.
DB에는 turn/message/dispatch ID만 추가하며 본문을 보존하지 않는다.

서버는 새 전달의 source + dispatch + message + terminal turn 증거가 모두
일치할 때만 완료 결과를 만든다. 일치하지 않으면 기존 시간/순번 추정으로
폴백하지 않는다. 첫 턴도 사전 순번 baseline 없이 검증할 수 있다.

## 지원 경계 / 기존 계약

| 경로 | 이번 변경 |
| --- | --- |
| 새 Codex Rust Host PTY 전달 | 명시적 turn 연결, 불일치 시 완료 보류 |
| Claude 채널 / native reply | 기존 명시적 회신 유지 |
| Grok / 기존 미연결 PTY 전달 | 기존 시간·순번 계약 유지, 정확 연결 미완 |

이것은 모든 프로바이더의 정확 연결 완료가 아니다. Grok에 대해서는 이 턴에서
안정적인 명시적 turn-ID 입력/종료 형식을 검증하지 않았다. 기존 경로의 휴리스틱은
남아 있다. 제거 책임은 provider adapter/observer 소유 경계에 있으며, Grok의
정확한 입력–종료 연결 또는 메시지별 명시적 회신 보장을 검증하고 기존 진행 중
전달을 소진한 뒤 시간/순번 경로를 제거해야 한다.

ID/이벤트 누락, 스캔이 입력 이후부터 시작된 경우, 취소/오류의 명시적 종료
형식이 확인되지 않은 경우에는 정확 자동 완료를 주장하지 않는다. 별도 회신이나
복구가 필요하다. 새 코드는 provider의 실제 실행을 ACK했다고 가장하지 않는다.

## 검증

- 새 exact correlation 및 dispatcher→observer→bus 통합: 8 PASS.
- 기존 provider observer: 19 PASS.
- 채널: 21 PASS. Session Bus: 30 PASS (기존 ResourceWarning 2건).
- 서버 provider_observer 선택: 5 PASS.
- 임시 DB를 다시 열어 연결 지속성, 본문 미저장, 잘못된 source/attempt/message/turn,
  인용 헤더, 충돌 입력 거부 확인.
- 초기 테스트는 임시 저장소와 mock Host 사용. 이후 실제 Codex 모델 호출 및
  운영 서버 재적용 검증은 아래 기록 참조. 전체 제품/UI 검증은 NOT_RUN.

재현 요청: `.ai/runtime/tmp/pty-correlation-test-*.json` 및 기존
`channel-fix-test-channel.json`, `channel-fix-test-bus.json`.

## 실제 적용 검증 — 2026-09-10 11:39–11:44 UTC

- Universe를 최신 코드로 재시작. 최종 서버 PID `26492`, READY.
  기존 Grok 컨덕터/Claude 터미널은 보존했다.
- 임시 Codex CLI v0.153.4의 실제 세션에서 고정 응답만 요청했다.
  시험용 terminal `term_d7a872b3cfec3147`, source
  `source_8f843e413fab43a995126b2dd67157ad`.
- 1차 `msg_1b43e0f399a235a2`: 모델은 응답했으나 PTY가 헤더 줄바꿈을
  공백으로 바꿔 연결이 누락됐다. 자동 완료는 생성되지 않았다. 실제 입력 기록으로
  원인을 확인하고, 두 개의 정확한 선두 ID 필드 사이 공백 변환을 허용하도록 수정했다.
  인용 헤더는 여전히 거부한다. 해당 시험은 FAILED로 기록했고 성공으로 덮어쓰지 않았다.
- 수정 후 재시작, 2차 `msg_2cc51a0694dec02e` 전달 11:42:59 UTC.
  참조 `dispatch_0a34c22c2a0fa6cac63bd426cc10e2b5`, 실제 turn
  `01a08b20-d139-7962-a79e-1921862b4045`.
- 입력 ordinal 51과 task_complete ordinal 56에 같은 message/dispatch/turn ID가
  기록됐다. 중간 의미 메시지의 COMPLETED 투영에는 연결 ID가 없어 채택되지 않았다.
- 11:43:40 UTC 원본이 REPLIED로 전환, 결과 `msg_37b44cd912fb8a07` 생성.
  결과 참조는 동일 source의 `activity_3f0cd4bf6809a3a5bf904a75`이다.
  실제 화면에서 `PTY_EXACT_LIVE_OK_R2_20260910` 응답도 확인했다.
  모델의 별도 Bus reply 도구/HTTP reply 폴백 없이 관찰기가 결과를 생성했다.
- 검증용 Codex 터미널만 정상 종료했다. 원본 로그/인박스 보존, 기존 Grok/Claude
  두 터미널 LIVE 확인. Grok 정확 turn 연결의 미완 상태는 바뀌지 않는다.
- 최종 회귀: 8 + 19 + 21 + 30 + 5 tests PASS (총 83).

### 종료 후 상태 확인의 한계

기존 Claude의 default 지정은 복원되었고 Conductor Supervisor는 LIVE/CURRENT이다.
그러나 Claude는 terminal 계층 LIVE와 달리 Supervisor에서 DISCONNECTED/STALE이며,
last_seen_at은 `2026-09-10T09:47:49.502000Z`로 남아 있다. 종료한 테스트 Codex는
STOPPED/CURRENT, default=false이다. 따라서 기존 세션 상태가 모두 복원되었다고
주장하지 않는다. 정확한 원인은 UNKNOWN이며, Codex 회신 실검증 PASS와 별개의
Supervisor/provider 관측 상태 불일치로 남긴다. 상태를 맞추기 위한 DB 직접 수정이나
가짜 관측 시각 주입은 수행하지 않았다.
