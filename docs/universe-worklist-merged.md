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
| 6 | Seed/Skill Plan handoff + OS_INSTALL/OS_UPDATE 계획 | **DONE** (코드) / **LIVE 닫힘** (GCS) | `project_seed_apply`, `project_release_apply`, skill plan master apply tests; design doc §6 “implemented” | 2026-07-31: dispatch deliver 경로 수정(`.ai/master/inbox` 허용) → **DELIVERED→COMPLETED**; seed-asset-proposal apply로 GCS `.ai/universe` 5 assets 발행 |
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
| Todo UI 완성: 프로젝트/Node 연결, 편집, 우선순위 | **PARTIAL** → 개선 | CRUD·필터·draft + priority filter + **Seed worklist**. Todo는 사용자 열람/정리·지시 참고용 (Master queue/전달 경로 없음). Plan handoff Deliver는 별도 표면 |
| UI 지도/컨트롤 정비: 그래프/선택/Inspector/대화창, 모바일 반응형 | **PARTIAL** → 개선 | 뷰 전환 노출, pan/zoom/fit, Inspector 프로젝트 상시(닫기 가능), Conversation 대상 라벨, Esc, 모바일 toolbar/hint polish |
| 제품화 패키지: 트레이, 자동 시작, 설치 프로그램, 서버 상태/재시작, 설정 화면 | **PARTIAL** → 1.5 | CLI lifecycle + user install + **Windows tray** (`tray` / Universe-Tray.ps1). portable zip·MSI 미착수 |
| 통합 E2E 검증: 설치 → 연결 → Master 대화 → 작업 전달 → 결과 회수 | **시나리오+스모크** | 정본: `docs/universe-e2e-product-scenario.md`. 하네스: `tools/universe_e2e_smoke.py` (`run`/`check`) + `tests/test_universe_e2e_product_scenario.py` |

#### P1

| 항목 | 수준 | 비고 |
|------|------|------|
| Memory RAG: 미연결 메모, 노드 부착, 검색, 야간 배치 | **DESIGN** | `.ai/memory/inbox/2026-07-31-node-memory-rag-nightly-maintenance.md` (BRAINSTORM). 제품 코드 경로 없음 |
| Bench 고도화: 프로젝트별 관측, 모델/Skill 비교, Context Pack 반영 | **PARTIAL** → UI 노출 | 집계 API DONE. Inspector Bench 탭에 project-filtered bench + observations 표시. 대시보드/Context Pack 비교 고도화는 후속 |
| Future/Experience Plane: Cases, Why, Patterns | **PARTIAL** → UI 개선 | Case/match/pattern API+테스트 DONE. Inspector **Bench** 탭: observations/bench/cases + Record Case + Match. 라이브 case 0→UI에서 생성 가능. Why/통합 Future 화면은 후속 |
| Future 제안 통합 화면: Seed/구조/Bench/Experience 구분 | **NOT_STARTED** / 초보 | `future-paths`·composition 흐름은 분산. 통합 화면 미정착 |

#### P2

| 항목 | 수준 | 비고 |
|------|------|------|
| Release 운영: 서명, 채널, 롤백, 다프로젝트 일괄 업데이트 | **NOT_STARTED** | `core-release-db.md`에 non-goal/후속으로 명시. import/plan/apply 기본은 별 트랙 DONE |
| 확장: 다국어, 원격·모바일, OAuth, P2P, MCP Adapter | **NOT_STARTED** | 설계 방향만 (SPA 재사용 등) |

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

1. ~~Todo UI polish~~  
2. ~~#6 LIVE_GAP seed discovery dispatch 닫기~~  
3. ~~P0 E2E 시나리오 문서/고정 + 스모크 하네스~~  
4. ~~Bench/Experience Inspector UI 1차~~  
5. ~~UI 지도/컨트롤 정비 1차~~  
6. ~~제품화 packaging 1차 (CLI lifecycle + Windows user install)~~  
7. ~~packaging tray 후속~~  
8. packaging 후속: portable zip / MSI  
9. P1 Memory RAG / Future 통합 화면

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
