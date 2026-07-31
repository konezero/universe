## Universe 작업 목록 통합 정리 (한 파일)

대화 내용 + 기존 문서(정본 2개 + 임시본 참고용) 기준으로 정렬한 목록입니다.

**구현 수준 갱신:** 2026-07-31 (Todo UI + Master 전달 polish)  
**대조 기준:** `tools/` · `tests/` · `docs/` · HEAD `7861684`+local · 로컬 DB `%LOCALAPPDATA%\Universe\universe.sqlite3`  
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
| 6 | Seed/Skill Plan handoff + OS_INSTALL/OS_UPDATE 계획 | **DONE** (코드) / **LIVE_GAP** (GCS) | `project_seed_apply`, `project_release_apply`, skill plan master apply tests; design doc §6 “implemented” | seed 3·projection 3 있으나 GCS `.ai/universe`는 README만; **dispatch 1 = QUEUED** (seed discovery 미완); release_artifact/proposal 0 |
| 7 | Experience 및 인과 비교 | **PARTIAL** | experience-case/match/pattern APIs + tests (`test_experience_case_*`) | 로컬 case/observation/pattern **0**. UI 통합·인과 비교는 약함 |
| 8 | UI 로컬라이제이션 | **NOT_STARTED** | `index.html lang="en"` 고정; i18n 키/전환 없음 | design doc §8 presentation follow-up |
| 9 | Conductor/Project Provider 설정 (`AUTO/GROK/CODEX`) + SQLite | **DONE** | `cli_provider_setting`; `/v1/settings/providers`; resident host restart on change; tests | 로컬 settings 2 (CONDUCTOR + GCS MASTER), 값 AUTO |
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
| Todo UI 완성: 프로젝트/Node 연결, 편집, 우선순위, Master 전달 | **PARTIAL** → 개선 | CRUD·필터·draft + priority filter + **Send to Master** (composer 프리필) + **Seed worklist** (빈 프로젝트 5건). 실 DB 채움은 런타임 Seed 버튼 1회 |
| UI 지도/컨트롤 정비: 그래프/선택/Inspector/대화창, 모바일 반응형 | **PARTIAL** | graph canvas, inspector, conductor/master room 존재. 반응형·컨트롤 정비·완성도는 잔여 |
| 제품화 패키지: 트레이, 자동 시작, 설치 프로그램, 서버 상태/재시작, 설정 화면 | **PARTIAL** / packaging **NOT_STARTED** | Provider/Host 설정 화면은 있음. tray/autostart/installer **미착수** |
| 통합 E2E 검증: 설치 → 연결 → Master 대화 → 작업 전달 → 결과 회수 | **PARTIAL** | 단위·통합 테스트 다수(OK). UI: Master handoff propose/deliver + Activity 노출. **제품 한 줄 E2E 시나리오/증적은 아직 없음**. GCS dispatch QUEUED 잔존 |

#### P1

| 항목 | 수준 | 비고 |
|------|------|------|
| Memory RAG: 미연결 메모, 노드 부착, 검색, 야간 배치 | **DESIGN** | `.ai/memory/inbox/2026-07-31-node-memory-rag-nightly-maintenance.md` (BRAINSTORM). 제품 코드 경로 없음 |
| Bench 고도화: 프로젝트별 관측, 모델/Skill 비교, Context Pack 반영 | **PARTIAL** | 집계·랭킹 기본 경로 DONE. 대시보드형 고도화·풍부한 관측 데이터 부족 |
| Future/Experience Plane: Cases, Why, Patterns | **PARTIAL** | Case/match/pattern **API+테스트 DONE**, 라이브 0, 전용 UI/인과 비교 약함 |
| Future 제안 통합 화면: Seed/구조/Bench/Experience 구분 | **NOT_STARTED** / 초보 | `future-paths`·composition 흐름은 분산. 통합 화면 미정착 |

#### P2

| 항목 | 수준 | 비고 |
|------|------|------|
| Release 운영: 서명, 채널, 롤백, 다프로젝트 일괄 업데이트 | **NOT_STARTED** | `core-release-db.md`에 non-goal/후속으로 명시. import/plan/apply 기본은 별 트랙 DONE |
| 확장: 다국어, 원격·모바일, OAuth, P2P, MCP Adapter | **NOT_STARTED** | 설계 방향만 (SPA 재사용 등) |

### 4) 로컬 런타임 스냅샷 (2026-07-31 관측)

```text
project_connection: 1 (GCS)
project_todo: 0 (UI Seed worklist로 채움 가능; 로컬 미적용이면 0)
project_dispatch: 1 (QUEUED — seed discovery)
project_seed / projection: 3 / 3 (current seed in Universe store)
project_master_handoff: 0 (UI propose/deliver 경로 추가; 로컬 미적용이면 0)
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
Seed 실프로젝트 핸드오프 (GCS Dispatch QUEUED)              : LIVE_GAP (목록 #6)
지능형 P1 (Memory RAG·Experience UI·Future 통합)          : DESIGN~PARTIAL
P2 확장                                                    : NOT_STARTED
```

**다음 착수 추천 (소스 갭 기준):**

1. ~~Todo UI + Master 전달 polish~~ (UI 착수 완료 — 로컬 Seed/Deliver 실사용 검증 남음)  
2. #6 LIVE_GAP — GCS Project Master Seed 발행으로 Dispatch 닫기  
3. P0 E2E 한 시나리오 고정(설치→연결→Master→handoff/dispatch→결과)  
4. P1 Memory RAG는 P0 한 줄 선 후  

### 6) 변경 이력

| 일자 | 내용 |
|------|------|
| (원본) | 대화+문서 통합 목록 |
| 2026-07-31 | 소스/테스트/로컬 DB 대조 후 구현 수준 등급 표 추가 |
| 2026-07-31 | Todo UI: priority filter, Send to Master, Seed worklist; Fresh/Skill handoff propose+deliver UI |
