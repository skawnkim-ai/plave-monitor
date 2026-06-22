# PLAVE 자막 납품 자동화

자세한 배경/표/이력은 `PROJECT_SUMMARY.md` 참고. 이 파일은 Claude Code가 매 세션 시작 시 자동으로 읽는 빠른 컨텍스트용.

## 한 줄 요약
PLAVE 유튜브 라이브 종료 → 자막 작업 자동 분배 → 번역/QC 단계별 완료 시점마다 클라이언트에게 자동 납품 메일 발송. Windows 작업 스케줄러로 매일 무인 실행됨(실제 이메일 발송 포함).

## 파일 역할
- `launcher.py` — 매일 09:00 실행. Notion 확인 후 22:00에 `plave_auto.py`, 13:00에 `monitor.py` 실행 예약. Hibernate(절전) 전략 포함.
- `plave_auto.py` — 방송 종료 감지 → aicontents.team 업로드(Playwright) → ko_draft SRT 다운로드 → 작업자 파트 분배 → Gmail 발송.
- `monitor.py` — 5분 간격 폴링. 번역/QC/4개국어 완료 시점마다 클라이언트 납품 메일 발송 (`send_email`, `send_translation_email`, `send_qc_email`, `send_secondary_email`).
- `notion_gate.py` — Notion DB '자막 납품 현황' 쿼리/상태 업데이트.
- `logger.py` — 공용 로거. ERROR 발생 시 skawn.kim@dost11.kr 로 Gmail 알림 (TEST_MODE와 무관하게 항상 발송 — 의도된 동작).
- `status.json` — 마지막 실행의 단계별 결과 기록 (icon/step/detail/시각).

## 절대 건드리면 안 되는 것
- `.env`, `credentials.json`, `token.json` — 민감정보. `.gitignore`에 이미 제외되어 있음 (변경 시 그대로 유지).
- `TEST_MODE` 분기 — `plave_auto.py`의 `send_worker_email`, `monitor.py`의 4개 발송 함수 모두 `to = TEST_MAIL_TO if TEST_MODE else ...` 패턴으로 가드됨. 새 발송 함수를 추가할 때 반드시 같은 패턴 유지.
- 실제 운영 중인 무인 스크립트(`plave_auto.py`, `monitor.py`)를 수정할 때는 실제 이메일 발송 로직을 건드리는 것이므로 변경 전 TEST_MODE=true 상태에서 먼저 검증.

## 알려진 이슈 / TODO
- `TEST_MODE=false` 전환 후 실제 운영 테스트 아직 안 함.
- `WORKER1_NAME`~`WORKER6_EMAIL`이 `.env`와 `plave_auto.py`(`WORKER_EMAIL_MAP`)에 하드코딩되어 있어 작업자 추가 시 양쪽 다 수정 필요.
- `test_login.py`에 aicontents.team 평문 비밀번호가 하드코딩되어 있음 — git에 커밋되지 않았는지 확인 필요.
- `logger.py`의 에러 중복 방지(`_alerted_errors`)는 프로세스 재시작 시 초기화되므로 같은 에러가 재시작 후 다시 알림될 수 있음.

## 자주 쓰는 명령
```
python test_login.py          # aicontents.team 로그인/조회 테스트 (읽기 전용)
python get_token.py           # Gmail OAuth 토큰 재발급 (invalid_grant 시)
python plave_auto.py <youtube_url>   # 수동 실행 (테스트용)
python -m py_compile *.py     # 문법 검증
```
