# Persona 자동화의 Master·Worker·Reviewer 연결 계약

상태: DESIGN_CORRECTION / 2026-09-18

이 문서는 Persona 자동화에서 Mode, Session Anchor, Master 작업 지시,
Provider 실행을 서로 혼동하지 않도록 연결 경계를 정리한다. 이 문서는
기존 `persona-worker-review-automation-contract.md`의 역할·검토 규칙을
대체하지 않고, 세션 생성과 메시지 전달 경계를 보완한다.

## 핵심 결정

**Mode는 Conductor와 Master에만 존재한다.** Worker와 Reviewer는 새로운
Mode가 아니다. Worker와 Reviewer는 Rust Host가 만든 자신의
`session_anchor_ref`를 사용하고, `parent_anchor_ref`로 해당 작업을
소유한 Master에 연결된 typed assignment다.

따라서 다음을 Worker/Reviewer에 적용하면 안 된다.

- Conductor/Master용 Mode Anchor를 자식 세션에 복사하는 것
- Worker/Reviewer를 Mode Registry에 새로운 모드로 등록하는 것
- 별도의 `provider_session_ref`가 없다는 이유로 Provider 세션이 없다고
  판단하는 것
- Master의 Mode 훅을 Worker/Reviewer attach 훅으로 재사용하는 것

Rust Host가 Provider 세션을 생성할 때 이미 Session Anchor를 알고 있다.
`provider_session_ref`가 투영되지 않았거나 비어 있는 것은 레지스트리
투영 또는 구형 필드의 문제이지, 실제 Provider 세션 부재의 증거가 아니다.

## 식별자와 역할

| 항목 | Conductor/Master | Worker/Reviewer |
| --- | --- | --- |
| Runtime Mode | 있음 | 없음 |
| Mode Anchor | 자신의 현재 Mode Anchor | 생성하지 않음 |
| Session Anchor | Rust Host가 만든 세션 Anchor | Rust Host가 만든 child Session Anchor |
| 부모 연결 | 프로젝트/노드 소유 관계 | `parent_anchor_ref`로 Master Anchor를 가리킴 |
| Task Frame 실행 역할 | Parent/Master 또는 Conductor | `BOSS` 또는 `WORKER` |
| Persona·작업 유형 | Master/Conductor Persona | 구현 Persona, 검토 Persona, 기타 bounded 작업 유형 |
| 범위 | 프로젝트 또는 Feature Node | 정확한 project/node/Todo/Task Frame |
| Provider 실행 | 직접 실행하거나 Worker를 배정 | 필요한 경우 자신의 Host 경로에서 실행 |

Career Task Frame에서 `BOSS`와 `WORKER`가 실제 실행 역할이다. Reviewer는
별도 Mode나 Role이 아니라 검토 Persona를 가진 Worker turn이며, Career
정책의 `SUB_REVIEWER` 경로로 표현한다. `IMPLEMENTER`와 `REVIEWER`는
Persona 또는 작업 유형을 설명하는 값이지 Task Frame Role이 아니다.

Reviewer가 독립 검토를 해야 하는 경우에는 별도 Worker actor/run과 독립
검토 turn을 사용한다. 이것이 반드시 영속 Fleet 세션을 새로 만든다는
뜻은 아니다.

## 올바른 메시지 흐름

### 1. Master가 Todo를 계획한다

Node Master는 자기 노드의 authoritative Todo projection을 읽고 다음 중
하나를 선택한다.

- 작은 작업: `MASTER_DIRECT`로 직접 실행
- 작업자 필요: Task Frame Worker turn 생성 (Boss는 필요할 때만 사용)
- 결과 검토 필요: `SUB_REVIEWER` Worker turn과 검토 Persona 지정

선택에는 정확한 project, node, Todo, optional Task Frame, assignment
revision, Master Anchor, Persona revision, completion 조건이 고정된다.

### 2. Master가 Worker/Reviewer에게 지시한다

작업 지시와 검토 요청은 **Master를 부모로 하는 typed Session Bus/Task
Frame 메시지**로 보낸다. 이 메시지가 Worker/Reviewer 자동화의 정식
오케스트레이션 경로다.

메시지는 최소한 다음을 포함한다.

- `parent_anchor_ref`: 작업을 소유한 Master Anchor
- `session_anchor_ref`: 수신 Worker 또는 Reviewer의 Session Anchor
- exact project/node/Todo/Task Frame
- assignment revision과 Persona revision
- idempotency key와 이전 메시지 대체 정보
- evidence 요구사항과 다음 상태 조건

브라우저 캐시, 첫 번째 터미널, 프로젝트 전체 스캔, 최근 세션 추정으로
수신자를 정하면 안 된다.

### 3. Worker/Reviewer가 Master에게 결과를 보낸다

Worker는 `WORK_RESULT`, Reviewer는 `REVIEW_VERDICT` typed Action으로
**Master Anchor에 결과를 반환**한다.

Master는 결과를 다음처럼 분리해 기록한다.

1. assignment 접수 및 전달 상태
2. 실제 Provider 실행 결과
3. Reviewer `PASS`/`NEEDS_REVISION`/`BLOCKED`/`NOT_RUN`
4. Todo 상태 전이

큐 접수나 터미널 생존만으로 Provider 실행이나 Todo 완료를 주장하지
않는다. Reviewer `PASS`가 필요한 작업은 Master가 그 결과를 확인한 뒤에만
Todo를 완료한다. `NEEDS_REVISION`, `BLOCKED`, `NOT_RUN`은 정확한 후속
Todo와 다음 Master cycle로 이어진다.

### 4. Host queue의 위치

Host queue는 Master와 Worker/Reviewer 사이의 협업 채널이 아니다. Host
queue는 Worker 또는 Master가 실제 Provider 프롬프트를 실행해야 할 때
사용하는 **하위 Provider transport**다.

즉, 다음 두 경로를 구분한다.

```text
Master --typed Session Bus/Task Frame--> Worker 또는 Reviewer
Worker/Reviewer --typed result/verdict--> Master

Worker의 실제 Provider 실행 --필요한 경우에만--> Rust Host native queue
```

Host queue receipt는 협업 지시의 수신 증거가 아니며, `NATIVE_QUEUED`는
Provider가 Persona를 적용했다는 뜻도 아니다. Provider 적용은 같은
message id, assignment revision, Persona revision, Session Anchor가
Host-ledger의 `PROMPT_SUBMITTED` 또는 `STARTED`에 도달할 때만 별도로
기록한다.

## 훅 분리

공통 transport 유틸리티만 공유하고, 세션 생명주기 훅은 분리한다.

### Conductor/Master Mode 훅

- Registry에서 현재 Mode와 Mode Anchor를 해석한다.
- Conductor/Master Session을 생성·재개·정지한다.
- Mode Anchor와 Master ownership CAS를 검증한다.

### Worker/Reviewer attach 훅

- Rust Host가 반환한 child Session Anchor를 받는다.
- `parent_anchor_ref`, assignment role, revision, exact scope를 고정한다.
- Worker/Reviewer를 Mode Registry에 등록하지 않는다.
- 부모의 `UNIVERSE_SUPERVISOR_SESSION_ID`, Mode Anchor, terminal identity를
  자식이 덮어쓰지 못하게 한다.
- Resume/retry는 같은 child Anchor와 assignment revision을 재사용하고,
  stale owner는 CAS로 거부한다.

### Master result intake 훅

- Worker result와 Reviewer verdict를 Master Anchor 기준으로 받는다.
- 참여자, evidence, proposal, agreement/NEEDS_REVISION, next action을
  Task Frame 범위에 고정한다.
- 결과를 Todo 전이와 review gate에 연결한다.

### Provider transport 훅

- 실제 Provider prompt 전달과 Host ledger 관측만 담당한다.
- Session Bus의 Master 협업 메시지를 직접 대체하지 않는다.
- `provider_session_ref`를 필수 authority로 사용하지 않는다. 필요하면
  Rust Host가 제공한 실행 메타데이터로 결과에 첨부하되, Session Anchor와
  동일한 Provider 세션을 식별하는 보조값으로만 취급한다.

## 생성·재연결 불변식

- Worker/Reviewer 생성은 Master의 Mode Anchor를 변경하지 않는다.
- child Session Anchor는 정확히 하나의 `parent_anchor_ref`를 가진다.
- 자식 실행이 부모 Session을 레지스트리에 재등록하지 않는다.
- 같은 assignment revision의 재시도는 같은 idempotency 결과를 재사용한다.
- 다른 revision, 다른 Persona, 다른 Task Frame은 새 메시지다.
- Resume 후에도 child Anchor, parent Anchor, assignment revision이 유지된다.
- Provider 실행, Reviewer verdict, Todo DONE은 각각 독립된 증거가 있어야 한다.

## 현재 문제의 해석

Worker/Reviewer 레코드에서 `provider_session_ref`가 비어 보이는 경우,
먼저 Rust Host가 생성한 `session_anchor_ref`와 `parent_anchor_ref`를
확인해야 한다. 두 Anchor가 유효하고 Host binding CAS가 통과한다면 해당
세션은 Provider 작업을 받을 수 있다.

`provider_session_ref`가 없다는 이유로 `PROVIDER_OBSERVER_IDENTITY_UNAVAILABLE`
를 내거나 Provider 전달을 차단하는 것은 이 계약에 맞지 않는다. 그 값이
필요한 경로는 실제 Provider 실행 결과를 외부 실행 식별자와 연결할 때뿐이다.

## 검증 기준

다음 회귀 사례를 반드시 확인한다.

1. Master Mode Anchor를 가진 세션이 Worker와 Reviewer assignment를 만든다.
2. Worker/Reviewer 생성 뒤 Master Mode Registry와 Anchor가 불변이다.
3. Worker 지시는 Host queue가 아닌 Master 부모 Anchor를 가진 Session Bus
   메시지로 전달된다.
4. Worker 결과와 Reviewer verdict가 Master Anchor로 돌아온다.
5. Provider transport가 필요할 때만 Host queue가 사용된다.
6. `provider_session_ref`가 비어 있어도 Anchor/CAS가 유효하면 협업 메시지는
   진행된다.
7. 잘못된 parent Anchor, stale assignment revision, 중복 메시지는 거부된다.
8. 실제 Provider 결과가 없으면 Reviewer `PASS`나 Todo `DONE`으로 승격되지
   않는다.

이 문서의 목적은 Worker/Reviewer를 새로운 Mode로 만드는 것이 아니라,
Master가 노드 Todo를 소유하고 typed Session Bus로 하위 작업을 지시·검토한
뒤 결과를 다시 받아 완료하는 제품 계약을 명확히 하는 것이다.

## Career Task Frame 실행 형태

모든 작업에 독립 Provider 세션을 만들지 않는다. Master는 Todo의 크기와
관찰·재개 요구를 보고 실행 형태를 선택한다.

```text
작은 bounded 작업
  -> MASTER_DIRECT

서브에이전트로 충분한 bounded 작업 또는 독립 검토
  -> Task Frame
  -> BOSS(선택) -> WORKER / SUB_REVIEWER
  -> Result Packet -> Master Parent

오래 실행되거나 사용자가 터미널에서 관찰·재개해야 하는 작업
  -> durable Fleet Worker Session
```

Task Frame Worker는 raw sub-agent spawn이 아니라 Host가
`WORKER_INVOCATION_READY`를 확인한 뒤 Worker actor와 `worker_run_ref`를
가진 bounded turn으로 실행한다. 결과는 Master Parent의 Result Packet으로
돌아온다. Fleet Worker Session은 장기 실행, 사용자 관찰, Provider Resume가
필요할 때만 사용하는 별도 실행 표면이다.

`fleet.worker-session-start`는 사용자가 장기 관찰·재개를 명시적으로
요청한 경우에만 쓰는 `FLEET_SESSION` 표면이다. 자동 Node Persona
automation은 이 Action이나 persistent Worker Host를 만들지 않고, 아래의
Task Frame 경로를 사용한다. 따라서 Worker/Reviewer는 assignment 역할이며
새 authority나 Runtime Mode가 아니다. 호환성을 위해 명시적 Fleet terminal이
운영하는 transport mode 표기는 자동화 경로의 Mode Registry와 분리해
취급해야 한다.

## Automation transport correction (2026-09-18)

Node Persona automation now reserves automatic IMPLEMENTER and REVIEWER turns
as `execution_shape=TASK_FRAME` assignments. The durable assignment keeps the
exact project, node, Todo, Task Frame, parent Master Anchor, assignment
revision, Persona task kind, and provider phase, but it does not create a
persistent `WORKER` Supervisor Mode or a provider Session Anchor. The Runtime
Host executes the bounded ephemeral turn after the run CAS commits and returns
a provider receipt/result packet to the Master-owned automation projection.

`execution_shape=FLEET_SESSION` remains available for an explicit Fleet UI
request that needs a long-lived Worker terminal, but that route is separate
from automatic node control. A queue receipt or assignment state is never a
provider result, Reviewer verdict, or Todo completion. The automation driver
must observe the Task Frame result, record `PASS`/`NEEDS_REVISION`/`BLOCKED`,
and only then continue the node Todo lifecycle.
