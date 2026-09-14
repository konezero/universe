# AGENTS 정책과 실행 증적 전환 검토

기준일: 2026-09-14. 사용자 지시: AGENTS/Skills를 세밀하게 조사하고 수정하며,
Execution Guard는 허가증 발급 대신 실행 증적을 남긴다.

## 확인 근거와 해석의 한계

[OpenAI의 Astra Skills·프롬프트 안내](https://developers.openai.com/blog/rethinking-skills-and-prompts-for-gpt-6-astra)는
구체적인 Skill 적용 조건, 필요한 시점의 문서 로딩, 명확한 완료 조건을 권한다.
이를 이 저장소에 적용해 상시 지시와 작업별 계약을 분리했다. 문서의 일반적
모델 특성만으로 이번 중단이 모델 차이 때문이라고 확정할 수는 없다. 동일한
Host·지시·도구 상태에서 모델을 비교한 실험은 하지 않았다.

직접 확인한 소유 경계는 다음과 같다.

- Universe AGENTS의 공통 블록은 설치기가 생성한다. 수정 원본은
  `C:/workspace/career/runtime-source/.ai/distribution/context_management_runtime_pack/project_runtime_installer.py`다.
- Universe의 설치된 `.ai/runtime/reference_runtime` 파일은 공유 Runtime store의
  내용 해시 기반 링크다. 설치 파일을 직접 고치면 다른 프로젝트와 릴리스 검증에
  영향을 줄 수 있다. 공통 코드는 career의 `runtime-source/.ai`에서 수정했다.
- 설치된 Guard 문서는 Session Boot의 실행 허가와 일회용 receipt를 요구했지만,
  일반 작업용 Boot 정책은 같은 executor를 시작하지 말라고 했다. repo-root CLI가
  존재해도 읽기 순서와 문구만 보면 중단하기 쉬운 실제 계약 충돌이었다.
- 공통 파일 Guard의 지원 연산은 CREATE/MODIFY/DELETE/MOVE다. COMMAND가 문서의
  예시에 있었고 direct-work check는 연산 검사 전에 NOT_REQUIRED를 반환했다.
  이 응답은 서버 재시작 실행 능력이나 권한의 증거가 아니었다.
- 재시작의 실제 소유자는 인증된 로컬 Action과 외부 서비스 helper다. Guard의
  prepare/bind/check/consume은 그 앞에 추가된 별도 절차였다.

## 발견 사항과 적용한 변경

| 발견 사항 | 적용한 변경 | 유지하는 경계 |
|---|---|---|
| 매번 Manifest→START→Boot→Core·공통 정책을 순서대로 읽게 함 | 최초 진입 자료와 작업별 상세 계약을 구분하고 변경 없는 문맥 재사용 | 예약 명령·Mode 전환은 여전히 먼저 처리 |
| 사용자 지시와 실행 능력 부족을 모두 승인 문제처럼 다룸 | 기존 지시 범위 내 완료·검증 지속, 추가 결정이나 권한만 별도 판단 | 권한을 꾸며내거나 거부된 실행 경계를 우회하지 않음 |
| Guard 허가 발급→즉시 소비가 자동화를 막는 추가 왕복 | 실행 소유자가 현재 조건을 검사하고 시도·검증·결과를 기록 | 인증, 대상, 범위, preimage, Worker 계보와 중복 방지 |
| direct-work check가 연산 확인 없이 NOT_REQUIRED 반환 | 실제 연산 검증 결과와 증적 반환; 잘못된 COMMAND 예시 제거 | 증적 기록기는 범용 명령 실행기가 아님 |
| Host Guard 기록이 프로세스 메모리에만 존재 | 프로젝트가 연결된 Host는 파일 DB에 증적 보존 | 세션 없는 관측은 가짜 Anchor 없이 기록 |
| 모든 작은 작업에 Boss/검토가 기본처럼 읽힘 | 직접 Parent 작업을 기본으로 하고 필요한 경우에만 위임 | 읽기 전용 위임도 선언된 Task Frame 경계 유지 |
| 설치 시 Mode/Role이 현재 상태처럼 보임 | 생성 문서에 설치 기본값임을 명시; 현재 상태는 Registry/Anchor로 확인 | UNKNOWN을 확인되지 않은 사실로 채우지 않음 |
| 모든 수정에 전체 경로 추적·과도한 재검증을 유도 | 사고는 확인된 소유 경계 추적, 일반 변경은 영향 범위에 맞는 검증 | 원인 추정과 관측 구분, 변경된 실패 경로 검증 |
| 임시 DB만 있으면 테스트 전체가 격리됐다고 보기 쉬움 | 프로세스·상태 파일·정리 대상까지 격리 확인 | 무관한 운영 리소스를 테스트 정리 대상으로 삼지 않음 |

## 실행 증적 계약

기존 사용자 지시 또는 구현된 자동화 권한이 실행 범위를 정한다. Work Receipt는
그 지시 범위를 표현하는 기존 기록으로 남는다. 매 실행마다 별도의 Guard 허가를
발급·갱신·소비하지 않는다. 증적 ID나 instruction_ref는 인증 정보가 아니다.

파일 gateway는 같은 operation_id로 ATTEMPTED, VALIDATED, 실제 결과를 기록한다.
검증용 check는 EXECUTION_CHECK_PASSED/FAILED이며 나중에 쓸 수 있는 허가가 아니다.
consume은 EXECUTION_RECEIPTS_RETIRED를 반환한다. 예전 permit 행은 삭제하거나
새 증적으로 재해석하지 않는다.

공통 repo-root record는 대화형 세션 없이 Host 관측을 저장할 수 있다. 기록에는
작업·대상·지시 참조·관련 해시·검증 코드만 포함하며, 토큰·원문 프롬프트·소스 본문은
포함하지 않는다. 이런 운영 기록을 자동으로 RAG 지식 후보로 수집하지 않는다.

최초 증적 저장 실패는 실행 전 저장 오류로 반환한다. 실행 이후 저장 실패는 실제
실행 결과와 별도로 표시한다. 응답 유실은 UNCONFIRMED이며 성공으로 간주하거나
자동 재실행하지 않는다. 재시작은 Host의 DISPATCHED와 서비스의 COMPLETED를
같은 작업 ID로 연결한다. 상세 계약은 [서비스 재시작 문서](service-restart-execution.md)에 있다.

## 검증과 적용 범위

관련 검증 125건을 통과했다: Guard·gateway·바인딩·Worker 등 48건,
서비스 실행·Action·control 34건, 텍스트 편집 29건, 생성 정책 3건,
정책 경계 4건, 설치·보존 검증 6건, 세션 없는 record CLI 1건이다.
전체 저장소 테스트나 설치된 공통 릴리스의 전면 검증을 수행했다는 뜻은 아니다. 허가증 없이 실제 파일
쓰기, 범위 이탈·변경된 preimage 차단, 저장 실패 전후의 실제 결과 보존을 확인했다.
설치기 fixture는 임시 저장소에서 생성된 정책과 기존 프로젝트 문구 보존을 확인한다.

실서버 작업 `restart-evidence-fa366ea68a2248e9b4bec967bb1fdaad`는 허가 발급 없이
COMPLETED에 도달했다. PID 38244→20992, endpoint 유지, PTY Supervisor 18468 유지,
remote gateway/connector READY를 관측했다. Host 증적의 세 단계와 서비스 완료
기록이 같은 작업 ID를 사용한다.

현재 적용 범위를 구분한다.

- Universe: 프로젝트 AGENTS 정책과 서버 재시작 Host 도구는 수정됐으며 실제 실행을 검증했다.
- career: 공통 Runtime, Skills, Core 계약, 공통 정책과 생성기의 원본 소스를 수정했다.
- 설치된 공통 릴리스: 아직 이전 linked release다. 원본 테스트 통과나 웹 서버
  재시작을 공통 릴리스 배포 완료로 표시하지 않는다. 다음 공통 릴리스의 정상
  빌드·설치가 끝나면 AGENTS의 임시 이행 안내를 제거한다. 다른 프로젝트에 대한
  자동 업그레이드와 영속 자동화 권한 체계 구현은 이번 변경의 완료 증거에 포함하지 않는다.
