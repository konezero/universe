# 대기 전 Session Bus 후속 확인 훅

사용자 지시: 작업 중간에는 메시지를 읽지 않고, 응답을 마치고 대기할 때 현재 작업과 관련된 답변·후속·이어서 처리할 메시지를 확인하도록 지시한다. 훅 실행 자체로 작업을 수락하거나 완료하지 않는다.

## 구현과 소유 경계

- Codex 프로젝트 설정의 `Stop` 이벤트만 설치한다. SessionStart 바인딩 훅은 기존 경로를 유지한다. UserPromptSubmit/PostToolUse, 하위 에이전트, `stop_hook_active=true`는 메시지 조회 없이 건너뛴다.
- `tools/universe_session_bus_hook.py`가 현재 endpoint를 해석해 `/v1/session-bus/hooks`를 호출한다. 서버는 terminal, Session Anchor, Supervisor session, provider, 실제 provider session ID를 서로 대조한다. Mode currentness로 수신 대상을 선택하지 않는다.
- `tools/universe_app/session_bus_hooks.py`는 해당 Anchor의 대기 중 REPLY/CONVERSATION 존재 여부와 헤더만 읽는다. 메시지 본문 주입, claim, 읽음 처리, 자동 회신, TODO 완료는 하지 않는다.
- 대기 항목이 있으면 Stop의 `decision=block`으로 후속 확인 지시문을 전달한다. 에이전트가 기존 inbox/thread API로 원문과 처리 이력을 읽고 관련성을 판단한다. 실제로 선택해 처리한 메시지만 기존 상태 전이와 reply 계약으로 기록한다.
- 중복 보고는 작업을 재실행하지 않는다. PROCESS_REPLY의 수신 확인으로 새로운 답장을 요청하지 않는다. 관련 없는 새 작업은 섞어 실행하지 않는다. 관련 후속이 없으면 대기한다.
- 같은 Stop 연속 실행은 한 번만 이어가므로, 미해결 메시지가 있어도 무한 폴링하지 않는다. 다음 독립 턴의 Stop에서 다시 확인할 수 있다.

## 확인한 증거와 한계

- 변경 전 실험에서는 모델의 최종 출력만 있고 메시지가 STARTED로 남았다. 최종 답변 출력이나 /hooks 화면의 Active 표시만으로 훅 처리 성공을 판단할 수 없다.
- 처음 실험한 자동 회신/PostToolUse/UserPromptSubmit 방식은 사용자 정정에 따라 제거했다. 실험 메시지 `msg_4906f0a5da73f8d8`, `msg_a6f7fdbe165c7be4`는 canonical API로 CANCELLED 처리했으며 실제 사용자 작업의 성공 증거로 사용하지 않는다.
- 기존 Universe Master의 같은 provider 대화를 새 terminal `term_fd1185c220db350d`로 복구했다. Anchor는 `session_anchor_11010d5a163096ae0acbfbdc`, provider session은 `01a098e2-5840-79c0-8cbe-34e40c0c46f2`를 유지했다. Conductor와 Career provider 프로세스는 종료하지 않았다.
- 실제 Master에서 `STOP_REMINDER_IDLE_CHECK` 응답 후 Stop 관측 `NO_PENDING_MESSAGE`, `pending_count=0`을 확인했다. 캐시된 UserPromptSubmit 호출은 SKIPPED로 관측되었으며 메시지를 읽지 않았다. 현재 설치 설정에는 SessionStart와 Stop만 있다.
- 관측 파일: `%LOCALAPPDATA%/Universe/hook-observations/term_fd1185c220db350d.jsonl`. 실행 요청 증거: `.ai/runtime/tmp/session-bus-stop-reminder-idle-check.json`. 서버 반영 증거: `.ai/runtime/tmp/session-bus-stop-reminder-service-restart.json`.
- 회귀 78건 PASS: `tests.test_session_bus_hooks`, `tests.test_session_bus`, `tests.test_session_inject_hook`, `tests.test_session_bus_live_binding`. 추가한 terminal/anchor 불일치 검사 후 훅 suite 9건을 다시 확인했다. 기존 HTTPError fixture의 ResourceWarning은 남아 있으며 실패는 없다. `git diff --check` PASS.
- 위 최초 검증은 Codex 범위다. 아래 후속 작업에서 Claude/Grok 설치 및 입력 형식을 추가했다.
- 대기 항목이 있는 실제 Codex 후속 처리 PASS: `msg_e57fdb6a59ba1852`의 첫 응답 종료에서 Stop `PENDING_MESSAGE`, `pending_count=1`을 관측했다. 마스터가 지시문에 따라 실제 inbox와 원래 thread를 조회한 뒤 `/reply`로 `STOP_REMINDER_FOLLOWUP_PROCESSED` 결과를 기록했다. 원본은 REPLIED, 결과는 `msg_a3defdc356f819c8`이며 이어진 Stop은 SKIPPED로 끝났다. 수신자 Conductor로 전달된 별도 PROCESS_REPLY는 이 시점 QUEUED이므로 Conductor의 처리 완료까지 검증했다고 주장하지 않는다. 증거: `.ai/runtime/tmp/session-bus-stop-reminder-followup-evidence.json`.
- 단위 테스트에서는 훅의 무변경, 명시적 처리 후 제거, 재시작 후 재조회, 타 Anchor/새 WORK 제외를 확인했다.

Work Receipt: `work_278ff95bd64c731ad9fe18e8`. 기존 Master의 RAG 소스 변경은 보존했다.


## 후속: Claude / Grok 적용

사용자가 다른 프로바이더 적용 여부를 확인하여 누락된 설치 경로를 추가했다. 공통 구현은 Universe에 두고 Universe와 정본 `C:/workspace/career`의 `.claude/settings.json`, `.grok/hooks/session-stop.json`에 설치했다. 기존 SessionStart 및 다른 설정은 보존한다. 이후 Setup CLI Hooks에서도 Stop 설정을 독립적으로 병합하며 재설치는 중복을 만들지 않는다. 전역 개인 설정은 변경하지 않았다.

- Claude Code `2.1.270`: `session_id`, `hook_event_name=Stop`, `stop_hook_active` 입력을 사용한다.
- Grok `1.0.30 (04b7ffed98c6)`: 설치된 `~/.grok/docs/user-guide/10-hooks.md`에서 `sessionId`, `hookEventName=stop`, `stopHookActive`, `subagentType` 및 별도 세션 종료 Stop을 확인했다. 실제 `reason=end_turn`일 때만 처리한다. snake/camel 세션 ID가 충돌하면 읽지 않는다.
- Claude/Grok에는 `hookSpecificOutput.additionalContext`로 후속 확인 지시를 전달한다. Codex의 기존 `decision=block` 계약은 유지한다. 이 출력은 에이전트의 맥락 판단을 요청하며 훅 자체가 메시지를 수락·완료하지 않는다.
- Grok이 `.claude` 호환 훅을 함께 읽더라도 실제 `UNIVERSE_PROVIDER`와 일치하는 GROK handler만 조회한다. CLAUDE handler는 조회 전에 SKIPPED로 끝난다.
- 직접 증거: [Claude Stop 공식 계약](https://code.claude.com/docs/en/hooks#stop-decision-control), [Grok 공식 소스 문서](https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/docs/user-guide/10-hooks.md).

검증:

- source / protocol: 81 tests PASS. Grok의 종료 이유, 하위 에이전트, 반복 실행, 충돌 ID, 다른 provider handler를 모두 조회 전에 제외함을 검증했다. 기존 설정 보존 및 재설치 중복 방지, 잘못된 JSON의 Stop 설정 덮어쓰기 방지도 확인했다.
- configuration / install: Universe 및 Career의 `grok inspect --json`에서 `projectTrusted=true`, native GROK Stop과 Claude 호환 Stop의 실제 인식을 확인했다. 증거: `.ai/runtime/tmp/universe-other-provider-hook-inspect.json`, `.ai/runtime/tmp/career-other-provider-hook-inspect.json`.
- Career 설정 적용은 Career Runtime의 별도 in-root Work Receipt `work_302111db09766dfcc03c0e1d`와 receipt-aware gateway를 사용했다. Universe Work Receipt에 저장소 밖 경로를 추가하는 요청은 거절되어 사용하지 않았다. 기존 Career Mode Current Anchor나 provider 세션은 바꾸지 않았다. 적용 증거: `.ai/runtime/tmp/career-stop-hook-application-20260913.json`.
- Claude 기존 세션의 `/hooks` 화면에서 두 개(SessionStart, Stop) 설정을 확인했다. 실제 `CLAUDE_STOP_IDLE_OK` 응답 뒤 Stop `PENDING_MESSAGE`, `pending_count=1` 및 TUI의 `Stop hook feedback` 지시문 전달을 확인했다(PASS). 모델이 기존 이력을 계속 검토하여 약 4분에서 이 진단 턴만 중단했고 세션은 유지했다. 기존 `msg_441af3e8ac04c783`은 STARTED로 유지되므로 실제 후속 처리/회신 완료는 NOT_CONFIRMED다. 증거: `.ai/runtime/tmp/claude-stop-hook-live-check.json`, `.ai/runtime/tmp/claude-stop-hook-live-evidence.json`.
- Grok 실제 모델 응답/후속 처리: NOT_RUN. 앞서 사용자가 알린 한도를 고려해 모델 실행을 추가하지 않았다. CLI 설정 인식과 입력/출력 계약 테스트를 실제 후속 처리 성공으로 취급하지 않는다.
- storage / API: 기존 read-only hook endpoint와 상태 전이 계약을 재사용한다. 새 자동 claim/reply/완료 상태 전이를 추가하지 않았다.
- UI / browser: NOT_APPLICABLE. provider hooks 메뉴와 CLI 설정 검사만 해당한다.

Universe Work Receipt: `work_b4f2a84059b81457f902eef3`.

- 공통 설치 경로의 실행 서버 반영: 서비스 재시작 READY. 증거 `.ai/runtime/tmp/other-provider-stop-hooks-service-restart.json`. Provider 세션은 재시작하지 않았다.
