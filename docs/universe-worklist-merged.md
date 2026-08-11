## Universe 작업 목록 통합 정리 (한 파일)

대화 내용 + 기존 문서(정본 2개 + 임시본 참고용) 기준으로 정렬한 목록입니다.

### 운영 규약 (2026-08-06 고정)

| 구분 | 역할 |
|------|------|
| **Universe Todo (`project_todo`)** | **호스트 실행·운영 큐** (Universe가 돌리거나 관찰할 일). 제품 백로그 대체 아님 |
| **프로젝트 자체 보드** | 제품 엔지니어링 일 — 유지 가능 (Issues, next_actions 등) |
| **이 마크다운 워크리스트** | **이력·구현 수준 스냅샷 / 인덱스.** 일상 진행 추적용 아님 |
| **Seed worklist 버튼** | **제거됨**. 호스트 큐 항목은 Todo로 직접 추가 |

호스트 쪽 열린 일은 UI Todo(또는 `GET /v1/todos`) + `detail`에 문서 경로.  
제품 일은 프로젝트 보드. 정책 plant: `.ai/universe/TODO_TRACKING_POLICY.md`  
(Career 배포; 정본 설명 `docs/universe-todo-tracking-policy.md`).

제품군 경계는 `docs/universe-product-suite.md`가 정한다. Universe는
프로젝트 편입 템플릿을 소유하고, Career는 Runtime Release DB를 생산하며,
Rendezvous는 원격 발견과 연결 승인을 소유한다.

universe 프로젝트 시드 예: install_mode 구현 Todo, packaging/E2E/Memory/Bench 등.

**구현 수준 갱신:** 2026-08-06 (Todo = 정본 큐; install_mode 문서 고정) · 이전 2026-07-31  
**대조 기준:** `tools/` · `tests/` · `docs/` · 로컬 DB `%LOCALAPPDATA%\Universe\universe.sqlite3`  
**등급:**

| 등급 | 의미 |
|------|------|
| `DONE` | 코드+테스트(또는 문서 계약)로 구현됨. 제품 polish/실데이터는 별도 |
| `PARTIAL` | 핵심 경로는 있으나 갭·미연결·실패 이력·미제품화 잔존 |
| `LIVE_GAP` | 구현은 있으나 로컬 실사용/프로젝트 연결 데이터가 비거나 중단 |
| `DESIGN` | 설계·메모만 (코드 미착수 또는 표현층 미착수) |
| `NOT_STARTED` | 의도만 있고 구현 표면 없음 |

### 1) 문서 분산 위치(이력 소스)

- 정본(활성): `docs/universe-design-and-bench-flow.md`
- 정본(운영 규약): `docs/local-universe-service.md`
- 임시/보조본(참고): `.ai/runtime/tmp/planning-universe-design-and-bench-flow.md`
- 임시/보조본(참고): `.ai/runtime/tmp/local-universe-service.md`
- 임시/보조본(참고): `.ai/runtime/tmp/planning-local-universe-service.md`

### 2) 통합 작업지도 (Implementation Sequence + Todo 규약)

#### 2-1) 핵심 구현 시퀀스

| # | 항목 | 수준 | 소스 근거 (요약) | 실데이터/잔여 |
|---|------|------|------------------|---------------|
| 1 | Universe Bench/Skill Catalog 스키마 및 관측 집계 | **DONE** (+ `LIVE_GAP`) | `skill_catalog`/`skill_run_observation` 테이블; `GET /v1/bench/skills`; `test_skill_observation_*`, `test_skill_plan_ranks_*`; feat `002892e`/`a993708` | 로컬: catalog 1, observation 1. 프로젝트별 대량 관측·비교 UI는 P1 |
| 2 | Fresh Project 의도/스펙/디자인/루트 제안 계약 | **PARTIAL** | compositions/adoptions/refinement API; Conductor `FRESH_PROJECT_DRAFT`; tests `test_fresh_project_*`; feat `9a64c9a`/`4bd6563` | composition 6, adoption 1; refinement run 2건 **FAILED** (worker structured/transport) |
| 3 | Context Pack + Skill Plan 제안 파이프라인 | **DONE** (+ `LIVE_GAP`) | context pack / skill plan / adoption schemas; non-executing tests; master bind `02669bd` | context_pack 3, skill_plan_proposal 3, **adoption 0** → `USER_SELECTION_REQUIRED` 잔존 |
| 4 | Project-side Task Frame SkillRunObservation 발행 | **DONE** | queue + drain + redaction tests; `test_universe_task_frame_skill_observation.py`; publisher receipt | queue 1 **INGESTED**. 어댑터 follow-up은 호스트 경로 의존 |
| 5 | Project publication provider 큐/소비/순서 보장 | **PARTIAL** | skill-observation-queue drain, career-promotion-queue API, idempotent receipts | career queue 0; 일상 multi-project drain 운영 검증은 E2E 미완 |
| 6 | Seed/Skill Plan handoff + OS_INSTALL/OS_UPDATE 계획 | **DONE** (코드) / **LIVE 닫힘** (GCS) | `project_seed_apply`, `project_release_apply`, skill plan master apply tests; design doc §6 “implemented” | 2026-07-31: dispatch deliver 경로 수정(`.ai/master/inbox` 허용) → **DELIVERED→COMPLETED**; seed-asset-proposal apply로 GCS `.ai/universe` 5 assets 발행 |
| 7 | Experience 및 인과 비교 | **PARTIAL** | experience-case/match/pattern APIs + tests (`test_experience_case_*`) | 로컬 case/observation/pattern **0**. UI 통합·인과 비교는 약함 |
| 8 | UI 로컬라이제이션 | **NOT_STARTED** | `index.html lang="en"` 고정; i18n 키/전환 없음 | design doc §8 presentation follow-up |
| 9 | Conductor/Project Provider 설정 (`AUTO/GROK/CODEX/CLAUDE`) + SQLite | **DONE** | `cli_provider_setting`; `/v1/settings/providers`; resident host restart on change; tests | 로컬 settings 2 (CONDUCTOR + GCS MASTER), 값 AUTO |
| 10 | 단일 ACP 세션 게이트웨이 (Conductor, Project Master 공용) | **DONE** | `agent_session_gateway.py`; provider session boundary `e9c158d`/`78ef57a`; ACP tests | Task Frame ephemeral vs resident session 분리 테스트 존재 |
| 11 | 데스크톱형 SPA + Tauri/트레이/오토스타트 | **PARTIAL** | `tools/universe_ui` SPA (graph/todo/conductor/master); desktop-first docs | **Tauri/tray/autostart/installer 없음**. SPA 본편은 동작 표면 있음 |
| 12 | 중앙 Host Profile 해상도 | **DONE** | `host_profile.py`, settings host-tools discover/select/verify, `test_host_profile.py`, `c915510` | 런타임 호출자가 중앙 Profile 소비 |

#### 2-2) Todo 규약(중요 경계) — 구현 수준: **DONE** (규약·API·UI 골격) / **LIVE_GAP** (실사용 0)

- Todo는 `BACKLOG / READY / IN_PROGRESS / BLOCKED / DONE`, `P0~P3` → **구현됨** (`normalize_todo`, tests)
- 범위(scope): `UNIVERSE`, `PROJECT`, `NODE` → **구현됨**
- Todo는 실행이 아님: `Dispatch / Task Frame` 전환은 별도 단계 → **계약·테스트로 유지**
- 생성/수정/삭제/순서 변경 가능 → **API+UI**
- `PROJECT`/`NODE` 연결 지원 → **폼·필터 존재**
- revision 충돌 처리: `TODO_REVISION_CONFLICT` → **테스트 존재**
- Conductor `TODO_DRAFT` → 리뷰 후 사용자 저장 → **구현됨** (`a491d00`)
- **로컬 `project_todo`: 0건** (기능은 있으나 채워진 진행목록 없음)

### 3) 남은 작업 (우선순위) — 구현 수준 갱신

#### P0

| 항목 | 수준 | 비고 |
|------|------|------|
| Live Session Room + 증분 Multi-Room 라우팅 | **ROOM_PERMISSION_DONE / MEETING_AUTOMATION_NEXT** | 정본 `docs/live-session-room-routing.md`. ordered Room event/cursor, non-replay, Codex/Claude/Grok resident Project Master 및 명시적으로 연결된 외부 Room participant 증분 입력, provider delta/final 관측, Room-scoped permission 결정, 연결 해제와 resume 좌표 불일치 fail-closed 완료. 자동 debate loop가 후속 |
| Todo UI 완성: 프로젝트/Node 연결, 편집, 우선순위 | **PARTIAL** → 개선 | CRUD·필터·draft + priority filter + **Seed worklist**. Todo는 사용자 열람/정리·지시 참고용 (Master queue/전달 경로 없음). Plan handoff Deliver는 별도 표면 |
| UI 지도/컨트롤 정비: 그래프/선택/Inspector/대화창, 모바일 반응형 | **PARTIAL** → 개선 | 뷰 전환 노출, pan/zoom/fit, Inspector 프로젝트 상시(닫기 가능), Conversation 대상 라벨, Esc, 모바일 toolbar/hint polish |
| 제품화 패키지: 트레이, 자동 시작, 설치 프로그램, 서버 상태/재시작, 설정 화면 | **PARTIAL** → 3 | CLI + tray + portable + **embed Python** (`--with-python`) + per-user install script. Signed MSI/MSIX 미착수 |
| 통합 E2E 검증: 설치 → 연결 → Master 대화 → 작업 전달 → 결과 회수 | **시나리오+스모크** | 정본: `docs/universe-e2e-product-scenario.md`. 하네스: `tools/universe_e2e_smoke.py` (`run`/`check`) + `tests/test_universe_e2e_product_scenario.py` |

#### P1

| 항목 | 수준 | 비고 |
|------|------|------|
| Memory RAG: 미연결 메모, 노드 부착, 검색, 야간 배치 | **PARTIAL** → 1차 | API+UI+deterministic propose-links. 정본 `docs/universe-memory-rag.md`. Nightly LLM 배치는 후속 |
| Bench 고도화: 프로젝트별 관측, 모델/Skill 비교, Context Pack 반영 | **PARTIAL** → 개선 | Bench 탭 + duration + Context Pack 목록 + match Why 표시 |
| Future/Experience Plane: Cases, Why, Patterns | **PARTIAL** → 개선 | Bench/Experience + Match dims 표시. Pattern/Why 심화는 후속 |
| Future 제안 통합 화면: Seed/구조/Bench/Experience 구분 | **PARTIAL** → 1차 | Inspector **Future** 탭으로 Seed/예측/Bench/Memory/Handoff 집약 |

#### P2

| 항목 | 수준 | 비고 |
|------|------|------|
| Release 운영: 서명, 채널, 롤백, 다프로젝트 일괄 업데이트 | **NOT_STARTED** / 계약 유지 | import/plan/apply 기본 DONE. 서명·채널·롤백은 non-goal 유지, 별 트랙 |
| 확장: 다국어, 원격·모바일, OAuth, P2P, MCP Adapter | **NOT_STARTED** | 설계 방향만. packaging portable/tray가 로컬 배포 경로 |

### 4) 로컬 런타임 스냅샷 (2026-07-31 관측)

```text
project_connection: 1 (GCS)
project_todo: 5 (seed worklist)
project_dispatch: 1 COMPLETED (seed discovery; was QUEUED)
project_seed / projection: current seed + assets under GCS .ai/universe/
project_master_handoff: 1 DELIVERED_TO_MASTER (Fresh Composition)
release_artifact / release_proposal: 0 / 0
skill_run_observation: 1
experience_*: 0
fresh_project_composition: 6; refinement_run failed: 2
cli_provider_setting: 2 (AUTO)
```

### 5) 한 줄 총평

```text
코어 런타임·Bench·Provider·ACP·Host Profile·Todo/Fresh API : 대부분 DONE
제품 P0 (UI polish·패키징·E2E)                            : PARTIAL / packaging 미착수
Seed 실프로젝트 핸드오프 (GCS Dispatch)                   : LIVE 닫힘 (COMPLETED + assets)
지능형 P1 (Memory RAG·Experience UI·Future 통합)          : DESIGN~PARTIAL
P2 확장                                                    : NOT_STARTED
```

**다음 착수 추천 (소스 갭 기준):**

1. ~~Todo / seed / E2E / Bench UI / map / packaging / tray / portable~~  
2. ~~P1 Memory RAG 1차~~  
3. ~~Future 통합 탭 1차~~  
4. ~~Bench/Experience 고도화 1차~~  
5. P2 Release 서명·채널 / 원격 확장은 계약상 후순위 유지  
6. ~~임베드 Python + per-user portable install~~  
7. packaging 후속: signed MSI/MSIX

### 6) 변경 이력

| 일자 | 내용 |
|------|------|
| (원본) | 대화+문서 통합 목록 |
| 2026-07-31 | 소스/테스트/로컬 DB 대조 후 구현 수준 등급 표 추가 |
| 2026-07-31 | Todo UI: priority filter, Seed worklist; Fresh/Skill handoff propose+deliver UI |
| 2026-07-31 | Todo→Master Queue 제거 (Todo=열람/지시 참고만, 전달 큐 불필요) |
| 2026-07-31 | #6: `.ai/master/inbox` deliver 허용; seed dispatch COMPLETED; GCS seed assets apply |
| 2026-07-31 | 문서 정본: `local-universe-service.md` MASTER inbox 경로 계약 고정 (default `.ai/inbox/MASTER` + alternate `.ai/master/inbox`) |
| 2026-07-31 | P0 E2E 시나리오 고정: `docs/universe-e2e-product-scenario.md` |
| 2026-07-31 | E2E smoke: `tools/universe_e2e_smoke.py` + tests; Bench/Experience Inspector 탭 |
| 2026-07-31 | UI map controls: show view switcher, pan/zoom/fit, inspector project open, conversation labels |
| 2026-07-31 | packaging 1차: service control CLI + Windows user install scripts |
| 2026-07-31 | packaging tray: Universe-Tray.ps1 + `universe_server.py tray` |
| 2026-07-31 | portable package: `tools/build_portable.py` + data/ env overrides |
| 2026-07-31 | Memory RAG API/UI + Future tab + Bench/Context Pack polish |
| 2026-07-31 | portable --with-python embed + Install-Portable-User.ps1 |
| 2026-08-05 | Worker Bench vertical slice: redacted execution context, Project queue ingestion, Worker/Task/Node comparisons, quota continuity, Runtime preflight/audit UI |

### 7) 2026-08-05 Dogfood follow-up

Completed in the current vertical slice:

- Redacted Skill observations now keep Provider, Model, Skill, Worker Role,
  Task Kind, Node, duration/token metrics, outcome, validation, failure, quota,
  source/evidence references, and Context Pack digest without prompt/source/
  command payloads.
- Universe stores and compares cumulative Project-local observations by Worker,
  Task, Node, Skill, Model, Provider, and Project.
- Provider quota exhaustion preserves the resident session and Task Frame,
  writes a `PROVIDER_QUOTA` continuity record, and appears in Runtime Audit.
- Runtime Settings shows non-mutating local executable/authentication preflight
  findings and configuration suggestions before Provider startup.

Still open:

- Real Grok bounded-session probe after quota reset and long-running recovery
  probes for every Provider.
- Multi-Project publication dogfood with enough samples to evaluate local
  binding quality against external benchmark priors.
- Accessibility semantics beyond the current desktop/mobile smoke pass.

### 8) 2026-08-10 Conductor integration pass

Completed locally:

- Project Runtime DB P1 source implementation and fresh-install package
  regression coverage.
- Verified backup plus index-only `.ai` migration with explicit host
  quiescence evidence and project-root-safe restore.
- Fresh-clone `UNIVERSE_ATTACHED` / `PROJECT_STANDALONE` installation planning
  with immutable ai-career source binding and false-READY rejection.
- Claude one-time MCP configuration cleanup, including timeout, startup
  failure, close, and transient Windows lock retry paths.
- Bounded, delta-only meeting coordinator with turn-boundary cancellation,
  durable summaries, and per-room single-flight enforcement.
- Redacted service-callable Memory RAG batch foundation and local dogfood Skill
  observation fixture.
- Portable icon, release non-mutation, fixed-loopback upstream, HOST_OFFLINE,
  and browser credential/header isolation tests.
- ai-career PR #269 Project Runtime DB release
  `94460a5228603a2ce2f80f6b0ee1a0092bf53f7d` installed through the Host Runtime
  Lifecycle adapter; validation
  `1c39336eb8bdb786b628314e2874f1d5d838eb18d7210aa65249860fc18710c4`
  completed `PASS / VERIFIED` and the service returned to `READY`.
- Service shutdown grace now covers resident-provider cleanup, preventing a
  completed shutdown from being misreported as `STOP_TIMEOUT`.
- Desktop live visual smoke: Session Observatory, Provider worker bindings,
  Worker Bench, and chat/map layout rendered with no fresh-page console warning
  or error. A 390 x 844 mobile viewport also rendered without page-level
  horizontal overflow; responsive contract tests retain the regression guard.

External or deliberately deferred:

- Real Grok bounded-session probe while the provider quota is exhausted.
- Long-running quota/restart recovery probes for every real Provider.

### 10) P0 receipt-aware streaming and server modularization

Approved plan: `docs/p0-receipt-streaming-and-server-modularization.md`.

- [x] Add bounded Runtime-owned streaming payload staging and opaque content references in the clean PR269 worktree.
- [x] Bind Mutation Receipts after upload to payload kind, opaque ref, digest, size, target, preimage, and expected postimage.
- [x] Make exact byte-splice patch the default text MODIFY path; retain streamed full content for CREATE and fallback.
- [x] Commit Runtime source `1b882d40842abc93646745bc62b95c7f82e465e7`, OS_UPDATE Universe (PASS/VERIFIED; validation `16ba3f04d...`), and dogfood large CREATE/MODIFY, interrupted streams, digest mismatch, stale preimage, expiry, replay, and cleanup.
- [x] Extract pure Bench aggregation and comparison into `tools/universe_app/bench_service.py` and preserve the existing UniverseStore/API contract with focused tests.
- [x] Move Skill observation and Project observation queue SQL persistence behind `tools/universe_app/bench_repository.py`; preserve UniverseStore/API/SQLite contracts with repository tests.
- [ ] Continue extracting `tools/universe_server.py`; connection/auth/HTTP transport, SSE hubs, Memory batch configuration, and Bench aggregation/persistence are extracted, while Memory execution, Bench schema/bootstrap, Session/Provider, storage, API/runtime/CLI remain.
- [x] Maintain changed-module, smoke, API/DB contract, and full regression tiers; focused Bench/server 123 tests + 8 subtests and full 603 tests + 40 subtests passed on 2026-08-11.
- [x] Give the tray/supervisor durable ownership of every Session Boot executor: the Supervisor store retains exact process identity and a Windows-DPAPI-protected graceful-stop capability, Conductor/Project Master leases persist it, and the resident service exposes guarded HTTP adopt/stop routes with graceful shutdown only; no raw process fallback is allowed.
- [ ] Add scheduled real-Provider, browser, restart, quota, and long-running dogfood tiers.

Priority: **P0**. New feature work that expands `tools/universe_server.py` should wait unless it is required to complete this stabilization epic.
- Live native-provider completion adapter for automatic meeting runs.
- Gabia/VPS tunnel, public DNS/HTTPS, mobile Internet pairing, and signed
  MSI/MSIX deployment.

### 9) 2026-08-10 Memory synthesis and Conductor delegation foundation

Completed locally:

- Per-Project `FAST_EXTRACT`, `CONSOLIDATE`, `SYNTHESIZE`, and
  `INDEPENDENT_CHECK` configuration with Provider/model catalog validation,
  effort, schedule policy, quota, fallback, enabled, and dry-run fields.
- Redacted candidate pipeline and durable review states: `IGNORE`, `KEEP`,
  `EXPLORE`, and `START_PRODUCT_DESIGN`.
- Candidate writes remain separate from Memory publication, Current Anchor,
  Seed, authority, Assignment, and project source.
- Governed Codex `FAST_EXTRACT` adapter from registered Activity sources
  through a Host-claimed Task Frame turn, with transient redacted semantic
  excerpts, the exact `gpt-5.6-luna`/`MAX` ceiling, retryable failed runs,
  atomic review-only Candidate + SkillRunObservation/Bench persistence, and
  real-dispatcher/fake-provider integration coverage.
- Billable live Codex/Luna `FAST_EXTRACT` verification over a registered real
  transcript: the hardened `MEMORY`-only output contract completed on retry
  attempt 2, created two `REVIEW_REQUIRED` candidates, retained no raw
  transcript fields, and published one redacted Bench observation.
- Provider-observer forward progress for complete JSONL events larger than the
  ordinary scan budget, bounded by a 4 MiB single-event fail-closed ceiling.
- Bounded Conductor delegation records, a worker queue independent from chat,
  progress/result APIs, restart recovery, Conductor UI status projection, and
  a default bounded Project Master delivery/result-reference route.
- Redacted terminal Worker evidence persisted in the existing invocation ledger,
  with exact Frame/Turn/Worker-run lookup that survives executor retirement and
  never stores Provider text, Context Packs, endpoint tokens, or raw results.

Required follow-up:

- Remove the installed Runtime CLI's 1 MiB request-file ceiling from
  receipt-aware full-file mutation, or add a bounded streaming/chunk transport;
  the Host mutation gateway accepts the payload but the CLI currently blocks
  large source files before receipt consumption.
- Make Project Source Work identity stable across executor restart, or persist
  the original proposal/Work Receipt restoration material; timestamp-derived
  replacement receipts cannot silently become existing Frame lineage.
- Add a clock-driven scheduler for persisted stage schedules, with due-time,
  quota-window, retry/backoff, and shutdown/restart tests.
- Connect Boss/Worker delegation roles to the approved Task Frame executor;
  these roles currently fail closed instead of bypassing Task Frame policy.
- Extend provider-produced candidate, failure-recovery, and scheduler tests,
  and add browser interaction/accessibility QA for configuration and review.
