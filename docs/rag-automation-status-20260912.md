# RAG 자동화 점검 및 한글 화면 (2026-09-12)

## 현재 판정: 부분 완료

자동 수집 전체 완료로 표시하지 않는다. 현재 AI FAST_EXTRACT의 예약 실행은 이미 준비된 Task Frame 바인딩을 공급하는 어댑터가 없다. 구현된 수동 실행 API는 등록된 source_ids와 정확한 Host Runtime 연결, 준비된 turn을 요구한다. 이번 작업은 이 조건을 우회하거나 기본 규칙 추출로 설정을 바꾸지 않았다.

## 직접 확인한 원인과 수정

- live memory-batch-config: FAST_EXTRACT 예약 활성화, 최근 REQUEST_INVALID/FAILED_EXHAUSTED. 후속 3단계는 규칙 기반 실행.
- UniverseHTTPServer._run_scheduled_memory_batch는 stage/source_ids만 보내며 runtime_binding을 만들지 않음. 공통 실행 경계에서 누락을 MEMORY_BATCH_RUNTIME_PREPARATION_REQUIRED로 명시하도록 수정.
- 예약 callback이 HTTP 응답 envelope를 반환해 scheduler의 run_id 읽기가 실패했음. 실제 run 반환으로 수정하고 결과 ID 없는 응답이나 FAILED 상태를 성공 기록하지 않도록 회귀 추가.
- Memory 메뉴가 숨겨진 Inspector로 향하던 경로를 독립 화면으로 연결.

## 한글 사용자 흐름

메모 메뉴 → 후보 수집 / 중복 정리 / 아이디어 분류 / 품질 점검 카드 → 검토 대기 / 채택 대기 / 연결 제안 / 저장된 메모.
메모 검색과 본문 조회, 보관/무시/RAG 채택을 지원한다. 연결 제안 만들기는 PROPOSED만 저장하며, 메모에서 노드를 고른 뒤 연결 확정한다. 원문 메모 내용과 기술 식별자는 임의 번역하지 않는다.

## 검증

- PASS: Python 관련 36개 테스트(예약, 추출 어댑터, 결정 계약, 채택 digest, 연결된 결정, 검색 주입). 공통 오류 위치 변경 후 관련 6개 재검증.
- PASS: tests/test_hover_memory_ui.js (격리 API/DOM, 실패 유지·KEEP·채택·중복 제거).
- PASS: JS 구문, git diff --check.
- PASS: 실제 한글 개요, 71개 메모 집계(연결 제안 8, 연결 확정 61), 본문과 연결 노드 선택 표시.
- PASS: 서버 재시작, scheduler RUNNING 및 read-only GET.
- NOT_RUN: 이번 변경 후 실제 provider 예약 실행/사용자 후보 채택·연결 mutation. 외부 Provider 성공으로 fixture 결과를 대체하지 않는다.

## 남은 완료 조건

1. 기존 Host 소유 Runtime의 exact session/anchor와 유효한 instruction에 연결된 예약 Task Frame 준비 어댑터.
2. 매 회차 turn 생성·종료·실패 복구와 쿼터 검증. 기존 turn이나 가짜 approval 재사용 금지.
3. 실제 예약 추출 → 정리 → 사용자 검토/채택 → 연결 제안 → 검색 주입의 live 증거.

이 조건을 충족하기 전 전체 RAG 자동화 TODO를 DONE으로 바꾸지 않는다.
