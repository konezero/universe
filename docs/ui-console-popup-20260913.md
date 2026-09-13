# 웹 UI 조회 시 빈 콘솔 창 생성 조사 — 2026-09-13

## 확인된 원인

웹 UI의 프로젝트 메모 목록 조회는 `/v1/projects/{project}/memory-candidates`를 통해 현재 소스와 후보를 비교한다. `tools/universe_app/memory_source_review.py`의 공통 `git()` 함수가 `rev-parse`와 `ls-files`를 실행하면서 Windows의 `CREATE_NO_WINDOW` 옵션을 전달하지 않았다. 숨겨진 Universe 서버에서 이 경로가 실행될 때 Git 자식 프로세스에 콘솔 호스트가 붙고, Git 종료 후에도 빈 콘솔 호스트가 남았다.

재현 당시 서버 PID는 56768이었다. 실제 메모 API 조회와 동시에 프로세스 계보를 관찰했다.

| Git PID | 작업 | 자식 conhost PID | 확인 |
| --- | --- | --- | --- |
| 56096 | rev-parse | 36336 | Git 종료 후 잔존 |
| 44628 | ls-files | 29888 | Git 종료 후 잔존 |
| 60132 | rev-parse | 47532 | Git 종료 후 잔존 |
| 48468 | ls-files | 58244 | Git 종료 후 잔존 |

각 Git의 부모는 Universe 서버였다. 같은 시기에 OpenConsole 프로세스도 생성됐다. OpenConsole과 conhost의 개별 연결은 시각적 추정으로 확정하지 않았다. 수정 전 메모 조회는 클라이언트의 12초 제한에서 TimeoutError가 발생했고 서버의 Git 실행은 계속 관찰됐다.

## 수정과 검증

공통 Git 실행에 `stdin=subprocess.DEVNULL`과 `creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)`를 적용했다. 기존 bytes 출력, NUL 구분 파일 목록, 오류 계약은 유지했다. 같은 파일에서 진행 중인 다른 Master의 RAG 변경은 보존했다.

- `python -m unittest discover -s tests -p test_memory_source_review.py`: 8건 PASS (15.989초).
- UI를 새로 여는 옵션 없이 Universe 서버만 재시작했다. 적용 후 PID 52744, `/health` HTTP 200 / READY.
- 같은 메모 API: HTTP 200 / MEMORY_CANDIDATES_COLLECTED, 후보 104건, 2.674초.
- API 조회를 포함한 24초 프로세스 관찰에서 이 서버의 새 conhost/OpenConsole 생성이 관찰되지 않았다. 빠르게 종료되는 Git 프로세스 자체는 이 표본에서 포착되지 않았으므로 모든 짧은 프로세스가 수집됐다고 주장하지 않는다.
- 관찰 기록: `.ai/runtime/tmp/ui-console-popup-after.json`.
- 재현으로 생성한 위 4개 conhost는 PID·프로세스명·부모 PID·생성 시각이 일치하고 부모 Git이 종료된 것을 다시 확인한 후 종료했다. 사용자 provider 세션은 종료하지 않았다.

## 증거의 범위

실제 서비스 API와 프로세스 계보로 재현·검증했다. 브라우저 화면을 통한 시각 검증은 수행하지 않았다. 별도 오래된 PowerShell(PID 15196)의 Python 자식 및 SurSvc에서 시작된 콘솔도 관찰됐지만 Universe 서버 자손이 아니었다. 해당 PowerShell의 명령행은 현재 권한으로 조회되지 않아 목적은 UNKNOWN이며, 원인으로 단정하거나 종료하지 않았다. 기존 출처 미확인 콘솔을 일괄 종료하지 않았다.

Work Receipt: `work_41f4cade8d9db2612556c647`.
