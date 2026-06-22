# PLAVE 자막 납품 자동화 — 프로젝트 요약

> 새 채팅에서 작업을 이어갈 때 이 파일을 첨부하거나 내용을 붙여넣으세요.

---

## 프로젝트 개요

**목적**: PLAVE 유튜브 라이브 방송이 끝나면 자막 작업을 자동 분배하고, 번역 완료 시점마다 클라이언트에게 자동 납품 메일을 보내는 시스템.

**폴더**: `C:\Users\User2\Documents\Claude\Projects\plave_monitor\`

---

## 파일 구조 및 역할

| 파일 | 역할 |
|------|------|
| `launcher.py` | 작업 스케줄러가 매일 09:00 실행. Notion 확인 후 22시에 plave_auto.py, 13시에 monitor.py를 자동 실행 |
| `plave_auto.py` | 방송 종료 감지 → aicontents.team 업로드 → ko_draft SRT 다운로드 → 작업자 파트 분배 → Gmail 발송 |
| `monitor.py` | 5분 간격 폴링. 번역·QC·4개국어 파일 완료 시점마다 클라이언트에게 납품 메일 자동 발송 |
| `notion_gate.py` | Notion DB '자막 납품 현황' 쿼리 + 상태 업데이트. is_upload_today(), is_delivery_today(), is_delivery_active(), update_status_by_workflow() |
| `logger.py` | 공용 로거. monitor.log 파일 + 콘솔 출력. ERROR 시 skawn.kim@dost11.kr 에 Gmail 알림 |
| `.env` | 모든 민감 정보 (API키, 이메일/비번, 작업자 목록) |
| `register_scheduler.bat` | 작업 스케줄러 등록 (launcher.py를 매일 09:00 실행) |
| `run_auto.bat` | plave_auto.py 수동 실행용 (테스트 시 `run_auto.bat <youtube_url>`) |
| `get_token.py` | Gmail OAuth 토큰 재발급 (invalid_grant 오류 시 실행) |
| `credentials.json` | Gmail OAuth 앱 자격증명 |
| `token.json` | Gmail OAuth 액세스/리프레시 토큰 (Production 모드 → 영구 유효) |
| `pending.json` | Gmail 발송 실패 시 video_id 백업. 재실행 시 감지해 업로드 없이 재발송 |
| `sent_log.json` | monitor.py 발송 이력 (중복 발송 방지) |

---

## 전체 자동화 워크플로우

### A. launcher.py (매일 09:00 자동 실행)

```
09:00 작업 스케줄러 → launcher.py 실행
  ↓
Notion '자막 납품 현황' DB 조회
  ├─ '업로드일' == 오늘? YES
  │    ├─ '총 작업 기간' == 오늘 범위? YES → 스레드로 22:00 plave_auto.py 대기 (컴퓨터 켜져 있음)
  │    └─ '총 작업 기간' == 오늘 범위? NO  → [Hibernate 전략]
  │         21:50 wake task 등록 (PowerShell, WakeToRun=True)
  │         22:00 plave_auto.py task 등록 (PowerShell, --hibernate 플래그)
  │         → launcher.py는 tasks 등록 후 종료 (Hibernate는 퇴근 시 직접)
  └─ '총 작업 기간' == 오늘 범위? YES → 13:00에 monitor.py 실행 예약

(업로드일 + 납품일 모두 해당 시 스레드 2개로 병렬 대기)
```

**Hibernate 전략 흐름 (업로드일만 해당되는 날)**
```
09:00  launcher.py → wake/auto tasks 등록 → 런처 종료
       (낮 동안 정상 업무)
퇴근 시 사용자가 직접 Hibernate(최대 절전) 진입
  ↓ (컴퓨터 절전)
21:50  작업 스케줄러가 Hibernate에서 깨움 (WakeToRun)
  ↓
22:00  plave_auto.py --hibernate 자동 실행
  ↓ (방송 감지 → 업로드 → SRT 분배 → 메일 발송)
완료 후 임시 tasks(plave_wake_tonight, plave_auto_tonight) 정리
  ↓
30초 후 다시 Hibernate 진입
```

### B. plave_auto.py (22:00 자동 실행 또는 수동)

```
1. Notion 확인 → 오늘 PLAVE 항목의 작업자 목록 로드
2. pending.json 확인 → 이전 Gmail 실패 건 있으면 바로 재발송 (3~5 단계 스킵)
3. YouTube 폴링 (10분 간격, 22~23시)
   ├─ 라이브 중 감지 → 종료 대기
   └─ 종료 감지 → (video_id, title) 획득
4. aicontents.team 업로드
   ├─ 같은 YouTube URL이 이미 있으면 기존 video_id 재사용 (중복 업로드 방지)
   └─ 없으면 Playwright로 새 업로드
5. ko_draft.srt 대기 (5분 간격, 최대 3시간 폴링)
6. SRT 파싱 → 자막 시간 기준 파트 분배 (PART 1~N)
7. pending.json 저장 (Gmail 실패 대비)
8. 작업자 각 1명씩 Gmail 발송 (담당 파트 + aicontents URL)
9. pending.json 삭제
```

### C. monitor.py (13:00~납품 완료까지 5분 간격 실행)

```
매 5분마다 check_once() 실행:
  ── 섹션 1: (사용 안 함 / 초기 업로드는 plave_auto.py 담당)
  ── 섹션 2: 번역 3개 완료 감지 → 클라이언트에 번역 납품 메일
  ── 섹션 3: EN/JA/ZH QC 완료 감지 → QC 납품 메일 + vi/ind/es/th 번역 트리거
  ── 섹션 4: 4개국어 번역 완료 감지 → 4개국어 납품 메일

Notion is_delivery_active() == False → 루프 종료
```

---

## 메일 발송 구조

| 발송 시점 | 수신자 | 내용 |
|-----------|--------|------|
| SRT 분배 직후 | 작업자 (PART별 1명씩) | 담당 구간 + aicontents.team URL |
| 번역 3개 완료 | 클라이언트 | EN/JA/ZH 번역 자막 첨부 |
| QC 완료 | 클라이언트 | QC 완료본 첨부 |
| 4개국어 완료 | 클라이언트 | vi/ind/es/th 자막 첨부 |

- `TEST_MODE=true` → 모든 메일이 SMTP_USER(본인)에게만 발송
- 모든 메일에 HTML 서명 포함 (build_html_body() 함수)
- Gmail API 사용 (SMTP 아님)

---

## 핵심 설정 (.env)

```env
YOUTUBE_API_KEY=...
PLAVE_CHANNEL_ID=UCPZIPuQPrfrUG9Xe_okEmQA
AICONTENTS_EMAIL=skawn.kim@dost11.kr
AICONTENTS_PASSWORD=...
NOTION_API_KEY=...
NOTION_DB_ID=31a4320ee27f803bb33fcf5e31937024
TEST_MODE=true          # 실제 운영 시 false로 변경
SMTP_USER=skawn.kim@dost11.kr
WORKER1_NAME=김도원
WORKER1_EMAIL=vvayaway@gmail.com
# ... WORKER2~6
DOWNLOAD_DIR=./downloads
```

---

## Notion DB 구조 (자막 납품 현황)

| 속성 | 타입 | 용도 |
|------|------|------|
| 콘텐츠 유형 | select | "✳️PLAVE" 필터링 |
| 업로드일 | date | plave_auto.py 실행 여부 판단 |
| 총 작업 기간 | date range | monitor.py 실행 여부 판단 |
| 상태 | status | "한국어 QC 중" / "En&Ja QC 중" / "En&Ja QC 완료" |
| 작업자 | multi_select | 작업자 이름 목록 (WORKER*_NAME과 매핑) |

---

## Gmail OAuth 토큰 관리

- **Production 모드** 전환 완료 → token.json 영구 유효 (만료 안 됨)
- 만약 `invalid_grant` 오류 발생 시:
  1. `token.json` 삭제
  2. `python get_token.py` 실행 → 브라우저 인증
  3. 생성된 `token.json` 확인

---

## pending.json 패턴 (Gmail 장애 복구)

```
plave_auto.py 정상 흐름:
  SRT 분배 완료 → save_pending() → Gmail 발송 → clear_pending()

Gmail 실패 시:
  pending.json이 남아 있음

재실행 시:
  load_pending() 감지 → YouTube 폴링/업로드/SRT 대기 전부 스킵
  → 저장된 video_id + srt_path로 바로 Gmail 재발송
  → clear_pending()
```

---

## 작업 스케줄러 등록 방법

```
register_scheduler.bat 더블클릭
→ "subtitle_monitor_launcher" 작업 등록
→ 매일 09:00 launcher.py 자동 실행
```

---

## 현재 상태 (2026-06-15 기준)

- [x] 폴더 통합 완료 (plave_work_start → plave_monitor)
- [x] .env 통합 (모든 설정 단일 파일)
- [x] Gmail OAuth Production 모드 전환
- [x] pending.json 복구 로직
- [x] YouTube URL 중복 업로드 방지
- [x] HTML 이메일 서명
- [x] launcher.py Notion 기반 스케줄링
- [x] 모든 .py 문법 검증 통과
- [x] **Notion DB 상태 자동 업데이트 기능 완료**
- [x] **Hibernate 전략 추가 (컴퓨터 미사용 시 자동 절전/기상/재절전)**
- [ ] TEST_MODE=false 전환 후 실제 운영 테스트 필요

---

## Notion DB 자동 상태 업데이트

**신규 기능**: 파일 감지 및 메일 발송 시점마다 Notion DB '상태' 항목을 자동으로 업데이트합니다.

### 상태 변경 매핑

| 트리거 이벤트 | 상태 변경 | 실행 위치 |
|---------------|-----------|----------|
| **ko_draft 파일 → 작업자 발송** | `작업 전` → `한국어 QC중` | `plave_auto.py` |
| **ko_QC 파일 감지 → 클라이언트 발송 + 번역 트리거** | `한국어 QC중` → `En/Ja QC 중` | `monitor.py` |  
| **en_QC, ja_QC, zh_QC → 클라이언트 발송** | `En/Ja QC 중` → `En/Ja QC 완료` | `monitor.py` |
| **es, vi, ind, th → 클라이언트 발송** | `En/Ja QC 완료` → `납품 완료` | `monitor.py` |

### 구현 세부사항

- **날짜 자동 추출**: 파일명 `260530` → Notion 날짜 `2026-05-30` 매칭
- **안전한 업데이트**: 상태 불일치 시에도 경고만 출력하고 진행
- **실패 허용**: Notion API 오류 시에도 메일 발송은 계속 진행
- **워크플로우 함수**: `update_status_by_workflow(step, target_date)`

### 추가된 함수 (notion_gate.py)

```python
get_plave_page_by_date(target_date)     # 특정 날짜 PLAVE 항목 조회
update_status(page_id, new_status)      # 상태 직접 업데이트
update_status_by_workflow(step, date)   # 워크플로우 기반 상태 변경
```

---

## Hibernate 전략 (컴퓨터 미사용 자동화)

컴퓨터를 끄지 않고 Hibernate(최대 절전) 상태로 두면 작업 스케줄러가 자동으로 깨워 실행합니다.

### 사전 준비 (1회 설정)

```powershell
# 관리자 권한 PowerShell에서 실행
powercfg /hibernate on
```

### 동작 방식

| 시각 | 동작 |
|------|------|
| 09:00 | launcher.py 실행 → Notion 확인 |
| 09:00~ | 업로드일 해당 + 납품일 미해당 → wake/auto tasks 등록 후 **런처 종료** |
| ~퇴근 | 정상 업무 (컴퓨터 사용) |
| 퇴근 시 | **사용자가 직접 Hibernate(최대 절전) 진입** |
| 21:50 | 작업 스케줄러가 Hibernate에서 컴퓨터 깨움 |
| 22:00 | plave_auto.py `--hibernate` 플래그로 자동 실행 |
| 완료 후 | 임시 tasks(plave_wake_tonight, plave_auto_tonight) 삭제 → 30초 후 Hibernate |

### 케이스별 동작

| 조건 | 동작 |
|------|------|
| 업로드일 O, 납품일 X | Hibernate 전략 (컴퓨터 자동 절전↔기상) |
| 업로드일 O, 납품일 O | 기존 방식 (컴퓨터 켜둠, 스레드 2개 병렬) |
| 업로드일 X, 납품일 O | 기존 방식 (13:00 monitor.py 대기) |
| 둘 다 X | 런처 즉시 종료 |

### 관련 파일 변경 내역

- **launcher.py**: `schedule_and_hibernate()` 함수 추가, `main()` 분기 추가
- **plave_auto.py**: `--hibernate` 플래그 파싱, 완료 후 tasks 정리 및 `shutdown /h`
