# 페르소나 배정과 전용 컨덕터 자동화 구현계획

상태: IMPLEMENTATION_PLAN / 2026-09-14 사용자 구현 지시.
상위 TODO: `todo_persona_conductor_automation_20260914`.

## 1. 확정된 제품 방향

프로젝트의 큰 목표·구조·골격은 사용자와 대화 컨덕터가 브레인스토밍하며 계속 만든다. 별도의 CONDUCTOR 세션은 자연어 페르소나를 배정받아 그 골격 안에서 프로젝트를 이끈다. 자동화는 페르소나 배정 기능의 한 사용 사례이며 고정된 AUTOMATION Role enum이나 새 Mode를 추가하는 것으로 대체하지 않는다.

사용자는 ‘한 프로젝트를 이끌어가는 노련한 프로젝트 팀장’처럼 어떤 관점과 역량으로 일하는지 쓰고, 그 아래 하는 일·책임·한계를 자연어로 작성한다. 페르소나는 말투가 아니라 판단과 산출물을 바꿔야 한다. 사람이 편집할 수 있고 LLM도 같은 계약으로 읽고 작성·배정할 수 있어야 한다.

팀장은 미구현 항목을 무작정 소진하지 않는다. 프로젝트 목표와 현재 설계·구현을 비교하고, 목표에 맞는 일을 선택한다. 설계가 부족하면 적절한 회의를 구성하고 결과를 평가한 후 실행 가능한 작업으로 연결한다. 받은 구현 결과는 증거로 검토하고, 미충족 조건은 보완한다. 큰 골격·목표·범위 변경은 이유·대안과 함께 사용자/대화 컨덕터에 올리고 해당 의존 작업만 대기한다.

## 2. 기존 근거와 현재 간극

- 메모 `memory_f70cd0465c699b321b932cc8` / `meeting-role-persona-caliber`: 전문 분야·요구 역량·강점·anti-pattern, 역할을 정한 뒤 모델 선택. caliber는 무조건 높은 effort를 뜻하지 않는다.
- 메모 `memory_d28960a33096e1e3c68ae5d1` / `node-planning-meeting-role-contract`: mandate, bias, attack_targets, required_evidence, deliverable, limits를 담는 책임 계약.
- [회의 역할 가이드](meeting-topic-role-guide.md), [제품 목표·소유 경계](universe-design-and-bench-flow.md).
- `universe_server.py`의 NODE_PLANNING_ROLE_BRIEFS 및 계획 회의 배정은 구조화된 회의 역할 정보를 이미 제공한다. 지속 세션 페르소나 관리·배정·runner가 구현됐다는 뜻은 아니다.
- [Host 전달](host-turn-delivery.md), [Master 완료 회신](master-completion-delivery.md): Bus→Supervisor→Host→provider 전달과 결과 수신을 재사용한다. 별도 PTY 입력·Enter·Stop 훅 Bus 조회 루프를 만들지 않는다.

## 3. 최소 데이터 계약

아래 필드는 설계 제안이다. 구현 시작 시 공개 레지스트리와 실제 저장 모델을 조사해 재사용하고, 없는 계약만 추가한다. 서로 다른 개념을 단일 세션 상태로 합치지 않는다.

| 객체 | 저장할 의미 |
| --- | --- |
| Persona definition | 안정 id, 이름, 자연어 설명/책임 본문, revision, 출처, 보관 상태. 선택적 구조화 전문성/산출물/한계는 원문과 구분한다. |
| Session persona assignment | 정확한 Session Anchor, persona id와 고정 revision, 담당 프로젝트/범위, 배정 revision, 적용 시각/결과. |
| Automation run | 실행 id, 배정 참조, 사용자 지시/범위와 현재 목표·계획 버전, 활성/대기/일시정지/종료 상태, 진행 cursor, 현재 배정/회의, budget와 실패/반복 이력. |
| Work/meeting attempt | 원 TODO·계획·목표와의 연결, 안정 request/message/frame/run id, 선택 근거, 결과/검토 근거, 다음 결정. |
| Escalation | 큰 골격의 빈틈·충돌·범위 변경 제안, 영향, 대안, 필요한 결정, 결정 전 멈추는 의존 작업. |

페르소나 본문은 실행 권한이나 자동화 허가를 만들지 않는다. 사용자 지시의 범위, 실행 owner의 검증, 어댑터 지원은 별도다. runner는 persona 지시문에서 코드를 임의 실행하는 스케줄러가 아니다.

배정은 terminal_id 수명과 분리하여 같은 Anchor의 Resume에서 복원한다. 다른 세션에 페르소나를 자동 전이하지 않는다. 진행 중 페르소나 수정은 기존 run에 묵시 반영하지 않고 다음 안전한 경계에서 명시적 revision 적용으로 처리한다. 삭제 대신 보관한 페르소나도 과거 실행 근거에서 읽을 수 있어야 한다.

## 4. 사용 흐름과 실행 판단

1. 사용자가 페르소나를 작성하거나 기존 것을 편집한다. 제목·자연어 책임 본문이 기본 입력이다. 복잡한 내부 식별자 입력을 요구하지 않는다.
2. 별도 CONDUCTOR 세션에 페르소나와 담당 프로젝트를 배정한다. 배정만으로 실행하지 않는다. 현재 대화 컨덕터는 그대로 유지한다.
3. 사용자가 자동화 시작을 선택하면 프로젝트 목표·계획·제외 범위·run 한도를 확인하고 durable run을 시작한다. 처음부터 무제한 schedule grant를 만들지 않는다.
4. 담당 컨덕터가 현재 TODO·설계·결과를 읽어 목표 적합성을 판단한다. 결정은 선택 이유·근거 참조로 남긴다. 단순 키워드 점수나 enum 분기만으로 목표 적합성 판단 완료를 주장하지 않는다.
5. 설계가 충분하면 bounded TODO를 Master에 배정한다. 부족하면 명명된 질문, 회의 종류, 최소 역할/페르소나, 근거, 산출물, 회의 한도를 준비한다. 필요한 실제 하위 호출은 Task Frame 경로를 따른다.
6. 회의 결과가 목표·제약에 맞는지 검토한다. 승인된 골격 안의 구체화는 실행 계획에 반영한다. 목표나 큰 골격 변경은 escalation으로 올리며 회의 발언을 곧 실행 권한으로 취급하지 않는다.
7. 실행 결과가 오면 원 완료조건과 증거를 대조한다. provider 종료·테스트 통과·TODO 완료를 분리한다. NOT_RUN을 PASS로 바꾸지 않는다. 부족하면 정확한 보완 질문/실패 경계를 지정해 이어가고 충족하면 다음 작업을 고른다.
8. pause/stop은 새 배정을 즉시 금지한다. 진행 중 작업은 지원되는 취소 계약이 없는 한 임의 kill하지 않고 결과를 기록한다. resume은 기존 배정·회의부터 재조정한다.

결과 도착·의존 작업 해소·사용자 결정이 다음 판단의 계기다. 타이머는 복구/재확인을 위한 수단이며 빈 큐를 무한히 주입하지 않는다. 중복 이벤트·반복 tick은 같은 결정을 재배정하지 않는다. quota·입력 부족·반복 무성과는 이유와 재개 조건을 표시한다. 완료된 항목·보류 항목·다른 프로젝트 작업은 제외하고 범위 안의 독립 작업을 선택할 수 있다.

## 5. 순차 구현 작업

### P1 — 페르소나 정의와 세션 배정

TODO `todo_persona_assignment_slice_20260914`.
- 기존 세션/회의 역할과 저장·Action 계약을 조사한다.
- 자연어 페르소나 create/read/list/update/archive 및 assign/read/change/unassign 경로를 마련한다. 동시 수정은 revision/CAS, 재실행은 idempotency로 처리한다.
- 사람 UI와 LLM은 같은 서버 Action을 사용한다. 페르소나 편집창과 세션 배정/표시를 제공하고 실제 적용 지침을 전달한다.
- ‘노련한 프로젝트 팀장’은 편집 가능한 예시일 뿐 하드코딩된 고정 직무가 아니다.
- 검증: 임의 새 페르소나 작성·편집·정확한 세션 배정·해제, 잘못된 프로젝트/Anchor 거부, Host 재시작/Resume 유지, revision 충돌, 다른 세션 비영향, 실제 프롬프트 적용 증거.

#### P1 구현 노트 (2026-09-14, 실제 코드 근거)

**Provider별 원문 전달 지원 상태** (`tools/universe_app/terminal_host.py::persona_delivery_supported`):
- **CLAUDE**: 완전 지원. `--append-system-prompt-file <path>` (실제 CLI 플래그, `claude --help` 및 빈 실행으로 실측 확인)로 본문을 바이트 그대로 전달한다. 개행/따옴표/역슬래시/유니코드 모두 훼손 없음.
- **CODEX / GROK**: 미지원(확인된 사실, `--help` 부재만으로 단정한 것이 아니라 실제 `~/.codex/config.toml`, `-c key=@path` 실측, `grok agent --agent-profile`이 비대화형 서브커맨드 전용임을 각각 확인). 본문에 개행이나 리터럴 따옴표가 있으면 managed shell 경계를 안전하게 넘길 수 없다. 이 경우 **본문을 평탄화해서 보내지 않는다** — 해당 실행의 argv에서 페르소나 프래그먼트를 통째로 제외하고, 배정 레코드에 `unsupported_at/unsupported_provider/unsupported_reason`을 남긴다(`applied_at`과 상호 배타). 개행·따옴표가 없는 단문 페르소나는 CODEX/GROK에도 그대로(인라인) 전달된다.

**페르소나 캐시 파일(CLAUDE 전용) 수명/소유/재생성 정책**: `%LOCALAPPDATA%/Universe/persona-prompt-cache/<sha256(본문)[:40]>.md`. Universe 소유의 쓰기전용 캐시이며 어떤 터미널의 생애주기에도 묶이지 않는다 — 같은 본문을 참조하는 모든 배정/재시작/provider 세션이 같은 파일을 공유해 재사용한다. `persona_revision_snapshot`에서 언제든 재생성 가능하므로 능동적인 정리(GC)는 구현하지 않았고, 임의로 삭제해도 다음 참조 시 자동 재생성되어 안전하다. 파일 수는 "만들어진 적 있는 서로 다른 페르소나 본문 수"에 비례하며 실행 횟수에 비례하지 않는다.

**적용 증거의 정직한 한계**: `applied_at`은 실제로 이 정확한 배정(`persona_id`+`persona_revision`+`assignment_revision`)의 원문이 실행 인자에 실제로 포함된 새 spawn에서만 CAS로 기록된다. Host 재사용(`host_reused_existing`)인 경우, 또는 provider가 미지원이라 페르소나가 argv에서 빠진 경우 모두 `applied_at`을 남기지 않는다. 배정 저장(성공) ≠ 실제 적용(provider별로 조건부)이라는 구분을 UI(세션 배정 패널)와 `persona.assignment-read` 응답 모두에서 드러낸다.

#### P1 transport update (2026-09-15, Codex Master)

The earlier CODEX/GROK paragraph above described the pre-queue boundary. It is
superseded for CODEX by the implemented Rust Session Host native queue:
ASCII single-line, quote-free bodies remain inline in `developer_instructions`;
Unicode, newline, or quote bodies are omitted from argv and sent unchanged in
one authenticated `codex queue --thread <provider_session_ref> --message`
argument. The Host receipt is persisted as `NATIVE_QUEUED` with its message and
submission ids. `applied_at` remains empty until Host recovery observes that
same message at `PROMPT_SUBMITTED` or `STARTED`; a queue receipt alone is not
provider application. A missing binding or receipt is `NOT_RUN` with
`unsupported_*` evidence. The persona is natural-language context carried by
the user-level queue, never a developer/system instruction or permission grant.
GROK remains unsupported for those complex bodies, and no real Grok call is
made while quota is constrained.

### P2 — 전용 컨덕터와 지속 실행 제어

TODO `todo_persona_automation_run_20260914`; P1에 의존.
- 페르소나 배정과 run start를 분리한다. 별도 CONDUCTOR를 기존 생성/Resume 경로로 연결한다.
- start/pause/resume/stop/status와 run 기록, lease/중복 방지, 배정 참조, 재시작 복구를 구현한다.
- 기존 Master queue/Bus를 사용하고 여러 컨덕터가 같은 TODO를 중복 배정하지 않게 ownership을 검증한다. 전달 접수와 작업 시작을 구분한다.
- 검증: 중복 start/tick, 일시정지 중 결과 도착, 재시작 전후 cursor/배정 보존, stale owner, quota/전달 실패, 정지 후 새 작업 0건. 테스트는 프로세스와 DB 모두 격리한다.

### P3 — 목표 적합성 판단과 설계 회의

TODO `todo_persona_goal_design_loop_20260914`; P2에 의존.
- 선택된 목표·계획 버전과 현 구현을 전달하고 근거 있는 실행/회의/상위 결정 요청을 기록한다.
- 회의 책임·페르소나·최소 참가자와 질문/산출물/한도를 실제 회의/Task Frame에 연결한다. 회의 모드 이름만 만들고 완료로 취급하지 않는다.
- 상위 결정이 오면 영향받는 계획·TODO만 이어간다. 예측은 Galaxy 전용, 직접 계획의 전제는 아니다.
- 검증: 설계가 충분한 작업, 설계 공백, 목표 밖 제안, 큰 골격 변경 escalation, 사용자 결정 이후 재개. 실제 LLM/회의 실행과 deterministic fixture를 구분한다.

### P4 — 실행·검토 반복과 실사용 표시

TODO `todo_persona_automation_acceptance_20260914`; P3에 의존.
- 담당 페르소나/프로젝트, 현재 판단·배정·회의, 대기 이유, 사용량/한도, 마지막 검토, 다음 조건을 하나의 관측 surface에 표시한다.
- Master 결과를 근거와 함께 검토하여 수정 요구 또는 다음 작업으로 연결한다. 단순 ‘완료했습니다’ 보고만으로 DONE 처리하지 않는다.
- [Fleet 실행 가시성](fleet-execution-visibility-design.md)의 상위 연결을 재사용한다. 이 slice가 별도 Fleet 전체 Worker 관측 TODO를 몰래 포함하지 않는다.
- 검증: bounded 실제 작업 1개에 배정→결과→검토, 설계 보완 사례 1개, 승인 범위 내 다음 작업 선정, pause/resume와 재시작. 역할 지침만 바꾼 상태나 self-ping으로 자동화 완료를 주장하지 않는다.

#### P2~P4 구현 노트 (2026-09-15, Codex Master)

`tools/persona_automation.py`에 별도 SQLite 투영을 추가했다. `persona_automation_run`은
정확한 Session Anchor와 persona/assignment revision을 고정하고 scope, goal version,
budget, cursor, 현재 배정·회의·판단·검토, quota 상태를 보존한다. start/pause/resume/stop/status,
CAS revision, lease와 stale-owner takeover, tick/dispatch/review/explicit-complete를
idempotent event로 기록한다. persona 배정 자체는 run을 만들지 않는다.

`persona.automation.*` Action은 서버에서 actor를 해석하며, `dispatch`는 기존
`UniverseStore.create_master_message`만 호출한다. provider 호출, self-ping, timer 기반 무제한
재시도는 이 경계에 없다. `EXECUTE` 판단에는 rationale과 evidence 참조가 필요하고,
`MEETING`/`ESCALATE`/`WAIT`는 next condition과 함께 대기 상태로 남는다. 결과는 PASS,
NEEDS_REVISION, BLOCKED, NOT_RUN으로 검토하며 PASS와 명시적 complete 요청이 모두 있어야
run이 완료된다.

Persona 화면에는 프로젝트 run의 상태·Anchor·판단·Master 배정·마지막 검토·다음 조건·quota와
pause/resume/stop 및 활성 Conductor 배정에서의 bounded start가 표시된다. 구현과 격리 SQLite
unit 검증에 더해 격리 live 검증을 수행했다. 실제 운영 Host와 세션은 변경하지 않았다.

## 6. 첫 실행의 페르소나 예시

> 한 프로젝트를 이끌어가는 노련한 프로젝트 팀장. 사용자와 대화 컨덕터가 정한 목표와 큰 골격을 이해하고 유지한다. 목표 달성에 필요한 미구현 작업과 설계의 빈틈을 찾는다. 필요하면 각기 다른 관점의 책임을 가진 참여자로 회의를 구성하고, 결과가 목표·제약에 맞는지 검토한다. 실행 가능한 작업은 적절한 Master에게 범위와 완료조건을 명확히 배정한다. 결과를 근거로 검토해 부족하면 보완하고 충족하면 다음 일을 이어간다. 큰 골격·목표·범위의 변경은 근거와 대안을 사용자와 대화 컨덕터에게 올린다. 검증하지 않은 내용을 완료로 포장하지 않으며, 반복해서 진전이 없으면 접근을 바꾸거나 필요한 결정을 요청한다.

## 7. 완료·검토·운영 경계

각 slice는 실제 구현과 관련 검증 결과를 별도로 기록한다. 모델 성공, 저장 성공, 화면 표시, 실제 운영 루프 성공은 동일하지 않다. 일부 구현만 끝나면 상위 TODO는 IN_PROGRESS이며 누락된 필드·어댑터·검증을 명시한다. 필요한 서버 재시작은 기존 service.restart evidence 경로로 수행하고 사용자 Host는 보존한다.

구현은 프로젝트 tools/tests/docs/UI 범위다. Core Mode/Role Registry를 덮어쓰거나 새 AUTOMATION Mode를 등록하지 않는다. 광범위한 권한 변경, 원본 기록 영구삭제, 예측 자동채택, 공동 편집 별도 프로젝트 구현은 제외한다. Git commit/push는 별도 사용자 지시 범위로 남긴다.

전용 자동화 컨덕터가 운영되기 전 첫 live pilot은 한 번에 하나의 bounded 작업을 처리하고 예산·중단 조건을 기록한다. 설계상 지속 기능을 제공하되 검증을 이유로 무제한 에이전트/회의/작업을 가동하지 않는다.
#### Deterministic selection and acceptance gates (2026-09-15)

The planner now requires an exact Goal selection and current revision before it can
choose work. The run or request must also prove the scope reference and the bound
Session Anchor owner. It considers only Todos attached to that selected Goal; a
BLOCKED Todo from another Goal in the same project cannot block or authorize this
run, and list order is never a substitute for selection evidence. A design gap
returns an explicit existing Work Plan meeting route with `provider_invocation:
NONE_UNTIL_EXPLICIT_ROUTE_CALL`; the route result must be reviewed before adoption
or application.

Review state is separate from queue receipt and provider application. `PASS` requires
`acceptance_status: VERIFIED_EVIDENCE` and rejects evidence that contains `NOT_RUN`.
`NOT_RUN`, queue-only receipts, UI path checks, and missing provider evidence remain
unaccepted states. No provider invocation is made by this planner or by its tests.

Current status is deliberately split: P2's bounded run/lease/pause/resume/dispatch
storage and Action route are implementation-complete. P3 now has an explicit
server-owned `persona.automation.judge` boundary: it pins the run Anchor, selected
Goal/version/scope/Todo evidence, invokes one read-only Task Frame through Codex
`gpt-5.6-luna`, validates the returned `EXECUTE`/`MEETING`/`ESCALATE`/`WAIT`
target, and records the same invocation/result receipt. `MEETING` calls the existing
bounded Work Plan route and leaves the run `WAITING` until explicit review/adoption;
it never creates authority or applies a plan. The Work Plan parser accepts the
existing reviewed route variants and deterministically projects them to reviewable
milestones/Todos.

The prior live failure evidence remains under
`.ai/runtime/tmp/dispatch-e6f4fdc460156eea/`: the fresh meeting turn ended as
`CODEX_TURN_FAILED` after a setup/tool launch was interrupted, not because of a
quota, authentication, network, or parser error. The adapter now preserves that
provider error detail and operation correlation, and fresh meeting sessions do
not prepend the persistent Mode greeting.

Post-fix isolated evidence is under
`.ai/runtime/tmp/dispatch-486eab3287711787/` (UTF-8 JSON, temporary validation
only). Design-gap run `persona_run_c40f140e97f442959d523157` used two fresh
CODEX `gpt-5.6-luna` sessions, completed both turns, produced
`GOAL_WORK_PLAN_CANDIDATES_READY` with two `CANDIDATE` Work Plans, and left the
run `WAITING` behind the explicit review/adoption gate. The provider route
adapter accepts `route[].detail` as well as the existing description/summary
forms; the meeting run identifier also crosses the strict identifier boundary
without a colon. No plan was adopted or applied.

The earlier `dispatch-486eab3287711787/p4-bounded-result.json` is a
`STATE_CONTRACT_FIXTURE_ONLY` artifact. Its queue/review/completion values were
written by a deterministic storage fixture with `provider_call=NOT_REQUIRED`;
they are not Master provider execution or Conductor acceptance evidence. The
fixture is retained only to document the state and reopen contract, with
operational acceptance `NOT_RUN`.

## 8. P5 — 노드 Master 자동화 소유 (2026-09-15 사용자 확정 설계)

사용자 확정: "오토메이션은 마스터도 가능해야 돼. 큰 틀은 컨덕터, 세부사항은
마스터. 그래야 해당 노드별로 마스터 붙일 수 있다. 충돌은 각 마스터 협의 또는
워커 합의." 담당: Claude (Codex에게 이관하지 않음).

**역할 분담**: 프로젝트 CONDUCTOR는 기존 그대로 project-wide 자동화(목표
적합성 판단, 상위 설계, 전체 조율)를 소유한다. 각 feature_node를 담당하는
MASTER 세션은 그 노드로 범위가 좁혀진 자동화(세부 계획, Todo 선택, Worker
실행·검증·보완 run)를 소유할 수 있다. 이는 CONDUCTOR 전용 검사를 문자열만
"MASTER"로 바꿔 허용하는 것이 아니다 -- 서버가 실제 Anchor role/project/담당
node/현재 배정 revision을 검증하고, 어떤 client 요청 필드도 이 결속을
넓히거나 다른 노드로 옮길 수 없다.

**결속 위치 (기존 계약 재사용, 새 enum/DB 우회 없음)**:
- `session_persona_assignment.node_ref` (nullable TEXT, additive migration):
  NULL = project-wide(CONDUCTOR 기존 경로 그대로), non-NULL = 실제
  `feature_node.feature_id` (배정 시 존재/프로젝트 일치를 서버가 검증,
  `_validate_persona_assignment_node_ref`). 이 필드는 어떤 Anchor mode에도
  선택 필드다 -- 기존 P1의 "임의 세션에 자연어 페르소나 배정" 용도를
  건드리지 않는다(과거 회귀 발견: MASTER 배정에 node_ref를 강제하려다
  `tests/test_persona_assignment.py` 15건 회귀 -- 되돌리고 mode-무관 선택
  필드로 정정).
- `persona_automation_run.node_ref`: run 시작 시 그 Anchor의 **저장된**
  배정에서만 복사되어 고정된다(`persona_automation.py::start_run`). 요청
  본문에는 이 필드가 아예 없다 -- client가 주장할 수 있는 값이 아니다.
  CONDUCTOR mode는 자신의 배정에 우연히 node_ref가 있어도 항상 무시하고
  project-wide로 시작한다(`_handle_persona_automation_action`의
  `persona.automation.start` 분기).
- 실제 소유권 검증은 `_validate_persona_automation_anchor`(자동화 시작
  시점)에서만 강제한다: CONDUCTOR는 기존 그대로 무조건 허용, MASTER는
  ACTIVE·같은 project·node_ref가 있는 배정이 있어야 허용
  (`PERSONA_AUTOMATION_NODE_ASSIGNMENT_REQUIRED`). MASTER에 project-wide
  fallback은 없다.

**동시 실행 (노드별로 마스터를 붙일 수 있다는 요구의 핵심)**: "activeRun"
단일성 제약을 project 단위에서 (project, node_ref) 단위로 좁혔다
(`persona_automation_run` 조회를 `node_ref IS ?`로 bucket). 서로 다른 노드는
각자의 MASTER 아래 동시에 RUNNING/WAITING/PAUSED 상태를 가질 수 있다. 같은
노드에 두 번째 run을 시작하면 기존과 동일하게
`PERSONA_AUTOMATION_ACTIVE_RUN_EXISTS`로 거부된다. CONDUCTOR의
project-wide bucket(`node_ref IS NULL`)은 기존과 동일하게 프로젝트당 1개로
유지된다.

**작업 선택 범위 제한**: `_persona_automation_plan`은 run에 `node_ref`가
있으면 그 노드로 Goal 목록을 먼저 좁힌다(`scope_kind == "NODE" and
node_ref == run.node_ref`, 기존 Goal 그래프 계약 그대로, 새 필드 없음).
좁혀진 목록 밖의 `goal_id`를 pin하려는 요청은 기존과 동일하게
`PERSONA_AUTOMATION_GOAL_NOT_FOUND`로 거부된다 -- 결과적으로 그 노드에
속하지 않는 Todo도 선택될 수 없다(Todo 선택은 이미 `todo.goal_id ==
selected_goal.goal_id`로만 좁혀져 있었으므로 추가 필터가 필요 없었다).

**충돌/협의 (이번 slice에서 구현하지 않음, 설계만 확정)**: 단순 충돌(같은
Todo/자원에 관심 있는 두 Master, 또는 관련 Worker)은 관련 Master끼리
협의하거나 Worker끼리 합의해 범위 내에서 해결한다 -- 기존 Bus/Task
Frame/회의 전달을 재사용하며 매번 Conductor로 직행하지 않는다. 합의는 범위
내 조정일 뿐 상위 목표·계획 변경 권한을 새로 만들지 않는다. 합의되지
않거나 노드/프로젝트 목표·상위 설계·권한 범위 변경이 필요하면 정확한 사유와
함께 Conductor에 escalation한다. 이 협의 프로토콜(충돌 항목/관련
Master·Worker/근거/제안/합의-미합의/다음 행동을 소유 범위와 연결해 기록하는
실제 메시지 경로)은 **아직 구현되지 않았다** -- 아래 8절 상태표에 NOT_RUN으로
남긴다.

**구현·검증 상태 (2026-09-15, Claude)**:
- 구현: `tools/universe_server.py`(assignment node_ref 컬럼/검증,
  `_validate_persona_automation_anchor` MASTER 분기, plan의 노드 필터,
  CONDUCTOR node_ref 무시), `tools/persona_automation.py`(run node_ref 컬럼,
  (project,node) 단위 active-run bucket).
- 검증: `tests/test_persona_node_master_automation.py`(12건, 격리 fixture
  project "TEST"의 실 HTTP round-trip -- 배정 검증, 자동화 시작 권한, 서로
  다른 노드 동시 실행, 같은 노드 충돌 거부, 노드 밖 Goal 선택 거부, CONDUCTOR
  회귀 불변). 기존 `tests/test_persona_assignment.py`(33건),
  `tests/test_persona_automation.py`, `tests/test_universe_action_registry.py`,
  `tests/test_todo_bind_goal_action.py` 회귀 없음(전체 재실행 확인, 관련
  `tests/test_universe_server.py` 서브셋도 확인 -- 유일한 실패는
  `window.prompt(` UI 문자열 검사로 이 변경과 무관, 변경 적용 전에도 동일하게
  실패함을 stash로 확인).
- **NOT_RUN (숨기지 않고 명시)**: (1) 사람 UI/LLM 공개 catalog 계약 확장 --
  `persona.assign`/`persona.automation.start`의 published `request_schema`에
  아직 `node_ref`를 노출하지 않았다(패턴은 `todo.bind_goal`에서 이미 확립,
  다음 slice 후보). (2) Persona/Fleet 상세 화면에 노드 Master 담당 범위·대기
  사유·협의 상대/결과 표시 -- UI 코드 변경 없음. (3) 협의/충돌 프로토콜의 실제
  메시지 경로(Bus/Task Frame/회의 재사용) -- 설계만 확정, 구현 없음. (4) 실
  운영 서버(재기동된 production process)에 대한 실제 Worker 작업 1건 배정→
  실행→검토 파일럿 -- 이번 회차는 격리 fixture(HTTP, 실 DB, project "TEST")
  수준 검증까지만 수행했고 production 세션/persona/run을 새로 만들지 않았다
  (기존 세션의 persona/Host를 임의로 만지지 않는다는 제약과 이번 1건 한도를
  지키기 위한 의도적 경계). (5) pause/stop이 새 배정을 막는지, resume이
  기존 배정부터 재조정하는지는 이번 node-scope 변경 이전부터 있던 기존
  `pause_run`/`resume_run`/`stop_run` 경로를 그대로 재사용하므로 코드 경로는
  존재하나, 이번 회차에 node-scoped run에 대해 별도로 실행 확인하지 않았다.
- P1은 여전히 IN_PROGRESS/미검증 상태이며 이번 slice로 검증됐다고 주장하지
  않는다. P1-P4 전체 provider/운영 Host 교체 수용도 이번 slice로 완료됐다고
  주장하지 않는다.

다음 후보(표시만, 착수 안 함): 위 NOT_RUN (1) -- `persona.assign` /
`persona.automation.start`의 공개 catalog 계약에 실제 request_schema(및
node_ref 필드)를 노출해 사람 UI와 LLM이 같은 계약을 읽게 하는 slice.

The actual bounded P4 evidence is now under
`.ai/runtime/tmp/dispatch-14eb816e105931ed/p4-actual-master-review-result.json`.
It uses a fresh isolated server and a real Codex `gpt-5.6-luna` Master turn:
the queue was created, claimed with the exact isolated Master anchor, completed
through the HTTP `DONE` route, and verified. A separate real Codex/Luna
Conductor Task Frame reviewed that returned result and recorded
`PASS/VERIFIED_EVIDENCE` through the durable review gateway; no provider call
was repeated when the first evidence-label storage attempt rejected a
`NOT_RUN` marker. This is bounded isolated acceptance only. P1 and operational
Host-wide acceptance remain `IN_PROGRESS`/`NOT_RUN`, Grok was `NOT_CALLED`, and
the existing user Host was preserved.
