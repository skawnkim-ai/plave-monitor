"""
PLAVE 자막 작업 분배 자동화
==================================================
흐름:
  [Notion] 오늘 PLAVE 업로드일 항목 + 작업자 다중선택 읽기
      ↓
  [YouTube API] 밤 10시부터 10분 간격 폴링
      - live 감지 → completed 전환 시 URL 추출
      ↓
  [aicontents.team] 업로드 폼 제출 (Playwright — 최소화)
      → 이후 HTTP API로 ko_draft.srt 폴링 + 다운로드
      ↓
  [SRT 분배] 재생시간 기준 PART 1/2/3 균등 분배
      ↓
  [Gmail API] 작업자별 담당 구간 메일 발송
==================================================
참고: monitor.py 의 로그인/API/Gmail 패턴 적용
"""

import base64
import json
import os
import re
import subprocess
import sys
import time
import logging
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import unquote, urlparse, parse_qs

import requests
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

load_dotenv()

# BASE_DIR 먼저 설정 (로그 파일 경로에 필요)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(BASE_DIR, "plave_auto.log"), encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════
#  설정
# ══════════════════════════════════════════════════════

# aicontents.team
SITE_EMAIL    = os.getenv("AICONTENTS_EMAIL")
SITE_PASS     = os.getenv("AICONTENTS_PASSWORD")
SITE_URL      = "https://www.aicontents.team"
LOGIN_PATH    = "/v3/users/sign_in"
AICONTENTS_VOCAB = "플레이브, 플리, 예준, 노아, 밤비, 은호, 하민"

# YouTube
YOUTUBE_API_KEY  = os.getenv("YOUTUBE_API_KEY")
PLAVE_CHANNEL_ID = os.getenv("PLAVE_CHANNEL_ID")

# Notion
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
NOTION_DB_ID   = os.getenv("NOTION_DB_ID")

# Gmail OAuth (monitor.py 와 동일한 파일 사용)
TOKEN_FILE       = os.path.join(BASE_DIR, "token.json")
CREDENTIALS_FILE = os.path.join(BASE_DIR, "credentials.json")
GMAIL_SCOPES     = ["https://www.googleapis.com/auth/gmail.send"]

# 테스트 모드: True = 본인에게만 발송
TEST_MODE      = os.getenv("TEST_MODE", "false").lower() == "true"
TEST_MAIL_TO   = os.getenv("SMTP_USER", "")  # 테스트 수신자

# 작업자 풀 (4명) — 이름은 Notion 작업자 필드와 정확히 일치
WORKER_EMAIL_MAP: dict[str, str] = {
    os.getenv("WORKER1_NAME", ""): os.getenv("WORKER1_EMAIL", ""),
    os.getenv("WORKER2_NAME", ""): os.getenv("WORKER2_EMAIL", ""),
    os.getenv("WORKER3_NAME", ""): os.getenv("WORKER3_EMAIL", ""),
    os.getenv("WORKER4_NAME", ""): os.getenv("WORKER4_EMAIL", ""),
    os.getenv("WORKER5_NAME", ""): os.getenv("WORKER5_EMAIL", ""),
    os.getenv("WORKER6_NAME", ""): os.getenv("WORKER6_EMAIL", ""),
}

DOWNLOAD_DIR      = Path(os.getenv("DOWNLOAD_DIR", "./downloads"))
DOWNLOAD_DIR.mkdir(exist_ok=True)

SENT_LOG          = Path(BASE_DIR) / "sent_log.json"
PENDING_FILE      = Path(BASE_DIR) / "pending.json"

# ── 이메일 서명 ──────────────────────────────────────
SIGNATURE_HTML = """<div style="font-size:14px;font-family:Gulim,굴림,sans-serif;"><table cellpadding="0" cellspacing="0" style="font-family:'Pretendard Variable',Pretendard,-apple-system,BlinkMacSystemFont,system-ui,Roboto,'Helvetica Neue','Segoe UI','Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic','Apple Color Emoji','Segoe UI Emoji','Segoe UI Symbol',sans-serif;color:rgb(99,102,241);"><tbody><tr><td style="vertical-align:middle;padding-right:15px;"><img src="https://i.imgur.com/JpADtyK.png" width="100" height="100" style="display:block;" loading="lazy"></td><td style="vertical-align:middle;"><strong style="font-size:16px;color:rgb(99,102,241);">김남주</strong><span style="color:rgb(99,102,241);margin-left:5px;">&nbsp;AI콘텐츠팀 PD</span><br><span style="color:rgb(156,163,175);">Kim nam ju / </span><span style="color:rgb(156,163,175);">AI-contents Team Producer</span><br><div style="height:5px;"></div><strong style="color:rgb(99,102,241);">M</strong> <span style="color:rgb(99,102,241);">010-2286-1893 </span><strong style="color:rgb(99,102,241);">E</strong> <span style="color:rgb(99,102,241);">skawn.kim@dost11.kr</span><br><div style="height:5px;"></div><span style="color:rgb(156,163,175);">255, Seongam-ro, Mapo-gu, Seoul, Republic of Korea </span><strong style="color:rgb(99,102,241);">11F</strong></td></tr></tbody></table></div>"""

def build_html_body(plain_text: str) -> str:
    """plain text 본문을 HTML로 변환하고 서명을 붙임."""
    html_body = plain_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
    return f'<div style="font-family:sans-serif;font-size:14px;">{html_body}</div><br>{SIGNATURE_HTML}'

POLL_START_HOUR   = 22   # 밤 10시부터 폴링 시작
POLL_END_HOUR     = 23   # 밤 11시까지만 폴링 (초과 시 종료)
POLL_INTERVAL_SEC = 600  # 10분 간격


# ══════════════════════════════════════════════════════
#  커스텀 예외
# ══════════════════════════════════════════════════════

class VideoUnavailableError(Exception):
    """YouTube 영상이 비공개/삭제되어 처리를 중단해야 할 때."""
    pass


# ══════════════════════════════════════════════════════
#  실행 추적기
# ══════════════════════════════════════════════════════

class ExecutionTracker:
    OK      = "✅"
    FAIL    = "❌"
    WARN    = "⚠️ "
    SKIP    = "⏭️ "

    def __init__(self):
        self.steps: list[dict] = []
        self.started_at = datetime.now()

    def ok(self, name: str, detail: str = ""):
        self._add(self.OK, name, detail)

    def fail(self, name: str, detail: str = ""):
        self._add(self.FAIL, name, detail)

    def warn(self, name: str, detail: str = ""):
        self._add(self.WARN, name, detail)

    def skip(self, name: str, detail: str = ""):
        self._add(self.SKIP, name, detail)

    def _add(self, icon: str, name: str, detail: str):
        entry = {
            "icon": icon,
            "step": name,
            "detail": detail,
            "at": datetime.now().strftime("%H:%M:%S"),
        }
        self.steps.append(entry)
        suffix = f" → {detail}" if detail else ""
        log.info(f"{icon}  [{name}]{suffix}")

    def summary(self) -> str:
        elapsed = int((datetime.now() - self.started_at).total_seconds())
        m, s    = divmod(elapsed, 60)
        w       = 52
        sep     = "─" * w
        lines   = [
            "",
            f"┌{sep}┐",
            f"│{'PLAVE 자막 자동화 실행 결과':^{w}}│",
            f"├{sep}┤",
        ]
        for st in self.steps:
            detail = f"  ({st['detail']})" if st["detail"] else ""
            row    = f"  {st['icon']}  {st['step']}{detail}"
            lines.append(f"│{row:<{w+2}}│")
        lines += [
            f"├{sep}┤",
            f"│  {'시작':4} {self.started_at.strftime('%Y-%m-%d %H:%M:%S')}{'':17}│",
            f"│  {'소요':4} {m}분 {s}초{'':25}│",
            f"└{sep}┘",
            "",
        ]
        return "\n".join(lines)

    def save(self):
        status_path = Path(BASE_DIR) / "status.json"
        data = {
            "run_at":  self.started_at.isoformat(),
            "elapsed": int((datetime.now() - self.started_at).total_seconds()),
            "steps":   self.steps,
        }
        status_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log.info(f"상태 저장 → {status_path}")


# ══════════════════════════════════════════════════════
#  발송 로그 (중복 발송 방지)
# ══════════════════════════════════════════════════════

def save_pending(new_vid: int, aicontents_url: str, srt_path: Path, title: str):
    """Gmail 실패 대비 — 업로드/다운로드 완료 상태를 pending.json에 저장."""
    data = {
        "date":           date.today().isoformat(),
        "video_id":       new_vid,
        "aicontents_url": aicontents_url,
        "srt_path":       str(srt_path),
        "title":          title,
    }
    PENDING_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"pending 저장 → {PENDING_FILE.name} (video_id={new_vid})")


def load_pending() -> dict | None:
    """오늘 날짜의 pending 항목이 있으면 반환, 없으면 None."""
    if not PENDING_FILE.exists():
        return None
    data = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
    if data.get("date") == date.today().isoformat():
        return data
    return None


def clear_pending():
    """Gmail 발송 완료 후 pending.json 삭제."""
    if PENDING_FILE.exists():
        PENDING_FILE.unlink()
        log.info("pending 삭제 완료")


def load_sent_log() -> list[dict]:
    """sent_log.json 로드. 없으면 빈 리스트 반환."""
    if SENT_LOG.exists():
        return json.loads(SENT_LOG.read_text(encoding="utf-8"))
    return []


def is_already_sent(aicontents_video_id: int) -> bool:
    """오늘 날짜 + 동일 video_id 조합이 이미 발송됐으면 True."""
    today = date.today().isoformat()
    for entry in load_sent_log():
        if entry.get("video_id") == aicontents_video_id and entry.get("date") == today:
            return True
    return False


def mark_as_sent(aicontents_video_id: int, aicontents_url: str, workers: list[dict]):
    """발송 완료 기록을 sent_log.json에 추가."""
    entries = load_sent_log()
    entries.append({
        "video_id":       aicontents_video_id,
        "aicontents_url": aicontents_url,
        "date":           date.today().isoformat(),
        "workers":        [w["name"] for w in workers],
    })
    SENT_LOG.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info(f"발송 기록 저장 → {SENT_LOG.name} (video_id={aicontents_video_id})")


# ══════════════════════════════════════════════════════
#  Gmail API  (monitor.py 동일 패턴)
# ══════════════════════════════════════════════════════

def get_gmail_service():
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, GMAIL_SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def send_worker_email(worker: dict, row: dict, title: str, aicontents_url: str):
    """작업자 1명에게 담당 구간 안내 메일 발송 (Gmail API)."""
    to = TEST_MAIL_TO if TEST_MODE else worker["email"]

    subject = f"[PLAVE LIVE] 자막 작업 배정 — {row['part']}"
    body_text = (
        f"안녕하세요 {worker['name']}님,\n"
        f"PLAVE LIVE 자막 작업이 배정되었습니다.\n\n"
        f"▶ 작업 영상과 파일\n"
        f"  {aicontents_url}\n\n"
        f"▶ 담당 구간\n"
        f"  {row['part']}: {row['start_idx']}~{row['end_idx']}\n"
        f"  {row['start_time']} → {row['end_time']}\n\n"
        f"작업 완료 후 카톡방으로 공유해 주세요.\n"
        f"감사합니다."
    )

    msg = MIMEMultipart()
    msg["to"]      = to
    msg["subject"] = subject
    msg.attach(MIMEText(build_html_body(body_text), "html", "utf-8"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service = get_gmail_service()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()

    mode = "[테스트]" if TEST_MODE else "[실제]"
    log.info(f"메일 발송 {mode} → {to} ({row['part']})")


# ══════════════════════════════════════════════════════
#  aicontents.team API  (monitor.py 동일 패턴)
# ══════════════════════════════════════════════════════

def site_login() -> requests.Session:
    """XSRF 토큰 기반 로그인 — monitor.py 동일."""
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})

    session.get(f"{SITE_URL}{LOGIN_PATH}")
    xsrf = session.cookies.get("XSRF-TOKEN")
    if not xsrf:
        raise RuntimeError("XSRF-TOKEN 쿠키를 찾을 수 없습니다.")
    xsrf = unquote(xsrf)

    # 준비 요청 (409 정상)
    session.post(
        f"{SITE_URL}{LOGIN_PATH}",
        headers={
            "X-XSRF-TOKEN": xsrf,
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Origin": SITE_URL,
            "Referer": f"{SITE_URL}{LOGIN_PATH}",
        },
        json={"user": {"email": SITE_EMAIL, "password": SITE_PASS}},
        allow_redirects=False,
    )

    # 실제 로그인
    r = session.post(
        f"{SITE_URL}{LOGIN_PATH}",
        headers={
            "X-XSRF-TOKEN": xsrf,
            "X-Inertia": "true",
            "X-Inertia-Version": "1.0",
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Referer": f"{SITE_URL}{LOGIN_PATH}",
            "Origin": SITE_URL,
        },
        json={"user": {"email": SITE_EMAIL, "password": SITE_PASS}},
        allow_redirects=False,
    )

    if r.status_code in (302, 303):
        loc = r.headers.get("Location", "/videos")
        session.get(loc if loc.startswith("http") else SITE_URL + loc)
    elif r.status_code not in (200, 201, 204):
        raise RuntimeError(f"로그인 실패 ({r.status_code})")

    log.info("aicontents.team 로그인 성공")
    return session


def get_video_list(session: requests.Session) -> list:
    r = session.get(
        f"{SITE_URL}/videos",
        headers={"X-Inertia": "true", "X-Inertia-Version": "1.0",
                 "Accept": "application/json, text/plain, */*"},
    )
    r.raise_for_status()
    return r.json()["props"]["videos"]


def get_video_files(session: requests.Session, video_id: int) -> list:
    r = session.get(
        f"{SITE_URL}/videos/{video_id}",
        headers={"X-Inertia": "true", "X-Inertia-Version": "1.0",
                 "Accept": "application/json, text/plain, */*"},
    )
    if r.status_code != 200:
        return []
    data  = r.json()
    video = data.get("props", {}).get("video") or data.get("video", {})
    return video.get("files", [])


def find_ko_draft_file(files: list) -> dict | None:
    """파일 목록에서 ko_draft.srt 파일 반환."""
    for f in files:
        name = f.get("filename", "")
        if "ko_draft" in name and f.get("status") == "available":
            return f
    return None


def is_youtube_accessible(video_id: str) -> bool:
    """YouTube Data API로 영상 공개 여부 확인. False = 비공개/삭제."""
    try:
        r = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"part": "status", "id": video_id, "key": YOUTUBE_API_KEY},
            timeout=10,
        )
        if r.status_code != 200:
            return True  # API 오류는 영상 없음으로 처리하지 않음
        items = r.json().get("items", [])
        if not items:
            return False  # 영상 없음 → 비공개 or 삭제
        privacy = items[0].get("status", {}).get("privacyStatus", "")
        return privacy == "public"
    except Exception as e:
        log.warning(f"YouTube 접근 확인 실패 (무시): {e}")
        return True  # 확인 불가 시 중단하지 않음


def download_file(session: requests.Session, file_obj: dict) -> bytes:
    url = SITE_URL + file_obj["downloadUrl"]
    r = session.get(url, allow_redirects=True)
    r.raise_for_status()
    return r.content


# ══════════════════════════════════════════════════════
#  업로드 — Playwright로 폼 제출 후 API로 전환
# ══════════════════════════════════════════════════════

def find_video_by_youtube_url(session: requests.Session, youtube_url: str) -> int | None:
    """
    aicontents.team 목록에서 같은 YouTube URL의 video_id 반환.
    없으면 None.
    """
    target_vid = extract_video_id(youtube_url)
    for v in get_video_list(session):
        existing_vid = extract_video_id(v.get("videoUrl", ""))
        if existing_vid and existing_vid == target_vid:
            log.info(f"기존 영상 감지 → video_id={v['id']} ({v.get('videoUrl', '')})")
            return v["id"]
    return None


def upload_and_get_video_id(session: requests.Session, youtube_url: str) -> int:
    """
    Playwright로 업로드 폼 제출 후,
    HTTP API로 새로 생긴 video_id를 찾아 반환.
    """
    # 업로드 전 기존 video ID 목록 기록
    existing_ids = {v["id"] for v in get_video_list(session)}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page    = context.new_page()

        # 쿠키 주입 (requests 세션 재사용)
        for cookie in session.cookies:
            try:
                page.context.add_cookies([{
                    "name": cookie.name, "value": cookie.value,
                    "domain": "www.aicontents.team", "path": "/",
                }])
            except Exception:
                pass

        page.goto(f"{SITE_URL}/videos", wait_until="networkidle")

        # 업로드 버튼 → 모달
        page.click('button:has-text("업로드")')
        page.wait_for_selector('input[placeholder*="youtube.com"]', timeout=8000)

        # URL + 단어장 입력 → 처리 시작
        page.fill('input[placeholder*="youtube.com"]', youtube_url)
        page.fill('textarea', AICONTENTS_VOCAB)
        page.click('button:has-text("처리 시작")')
        log.info("처리 시작 클릭 완료")

        # 모달이 닫힐 때까지 잠시 대기
        page.wait_for_timeout(3000)
        browser.close()

    # API로 새 video_id 확인 (최대 30초 재시도)
    for _ in range(6):
        time.sleep(5)
        videos    = get_video_list(session)
        new_ids   = [v["id"] for v in videos if v["id"] not in existing_ids]
        if new_ids:
            new_id = new_ids[0]
            log.info(f"새 영상 등록 확인: video_id={new_id}")
            return new_id

    raise RuntimeError("업로드 후 새 video_id를 찾지 못했습니다.")


def wait_for_ko_draft(session: requests.Session, video_id: int, youtube_video_id: str) -> Path:
    """
    ko_draft.srt 가 available 상태가 될 때까지 5분 간격 폴링.
    완료되면 파일을 다운로드하여 경로 반환.

    폴링 중 아래 상황 발생 시 VideoUnavailableError 발생:
      - aicontents.team에서 처리 실패(failed) 상태 감지
      - YouTube 영상이 비공개/삭제로 전환된 경우
    """
    log.info(f"ko_draft.srt 대기 중 (video_id={video_id}, 최대 3시간)...")

    for attempt in range(36):  # 5분 × 36 = 3시간
        files    = get_video_files(session, video_id)
        ko_draft = find_ko_draft_file(files)

        if ko_draft:
            log.info(f"ko_draft.srt 준비됨: {ko_draft['filename']}")
            content  = download_file(session, ko_draft)
            srt_path = DOWNLOAD_DIR / ko_draft["filename"]
            srt_path.write_bytes(content)
            log.info(f"다운로드 완료: {srt_path.name}")
            return srt_path

        # aicontents.team 처리 실패 감지
        failed = [f for f in files if f.get("status") in ("failed", "error")]
        if failed:
            raise VideoUnavailableError(
                f"aicontents.team 처리 실패 (status={failed[0].get('status')}) — "
                "영상이 비공개 처리되었을 수 있습니다."
            )

        # YouTube 비공개 전환 감지 (2회마다 1번 체크)
        if attempt % 2 == 1:
            if not is_youtube_accessible(youtube_video_id):
                raise VideoUnavailableError(
                    f"YouTube 영상({youtube_video_id})이 비공개/삭제 상태입니다. "
                    "채널 측 재공개 후 직접 실행해 주세요:\n"
                    f"  python plave_auto.py https://www.youtube.com/watch?v={youtube_video_id}"
                )

        log.info(f"아직 생성 중... ({(attempt + 1) * 5}분 경과)")
        time.sleep(300)

    raise TimeoutError("3시간 내에 ko_draft.srt가 생성되지 않았습니다.")


# ══════════════════════════════════════════════════════
#  Notion
# ══════════════════════════════════════════════════════

def get_plave_today() -> list[dict] | None:
    """
    오늘 업로드일 + ✳️PLAVE 항목에서 작업자(다중선택)를 읽어
    [{"part": "PART 1", "name": "...", "email": "..."}, ...] 반환.
    """
    today   = date.today().isoformat()
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }
    payload = {
        "filter": {
            "and": [
                {"property": "콘텐츠 유형", "select": {"equals": "✳️PLAVE"}},
                {"property": "업로드일", "date": {"equals": today}},
            ]
        }
    }
    resp = requests.post(
        f"https://api.notion.com/v1/databases/{NOTION_DB_ID}/query",
        headers=headers, json=payload, timeout=15,
    )
    resp.raise_for_status()
    results = resp.json().get("results", [])

    if not results:
        log.info(f"오늘({today}) PLAVE 항목 없음 → 종료")
        return None

    props      = results[0]["properties"]
    title_list = props.get("회차", {}).get("title", [])
    episode    = title_list[0].get("plain_text", "") if title_list else ""
    log.info(f"Notion 항목: {episode} ({today})")

    # 작업자 — 다중선택
    names = [item["name"] for item in props.get("작업자", {}).get("multi_select", [])]

    if not names:
        log.error("작업자 필드가 비어 있습니다. Notion에서 작업자를 선택해주세요.")
        return None

    workers = []
    for i, name in enumerate(names):
        email = WORKER_EMAIL_MAP.get(name)
        if not email:
            log.error(f"'{name}'에 매핑된 이메일 없음. .env의 WORKER*_NAME 확인 필요")
            return None
        workers.append({"part": f"PART {i + 1}", "name": name, "email": email})

    log.info(f"작업자 {len(workers)}명: {', '.join(w['name'] for w in workers)}")
    return workers


# ══════════════════════════════════════════════════════
#  YouTube 폴링
# ══════════════════════════════════════════════════════

def get_live_video_id() -> str | None:
    params = {
        "key": YOUTUBE_API_KEY, "channelId": PLAVE_CHANNEL_ID,
        "part": "id,snippet", "eventType": "live",
        "type": "video", "maxResults": 1,
    }
    items = requests.get(
        "https://www.googleapis.com/youtube/v3/search", params=params, timeout=15
    ).json().get("items", [])
    return items[0]["id"]["videoId"] if items else None


def get_video_status(video_id: str) -> dict:
    """videos.list로 특정 video_id의 snippet/liveStreamingDetails 조회."""
    r = requests.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={"key": YOUTUBE_API_KEY, "id": video_id, "part": "snippet,liveStreamingDetails"},
        timeout=15,
    )
    items = r.json().get("items", [])
    return items[0] if items else {}


def wait_for_live_end() -> tuple[str, str]:
    """
    밤 10시~11시 사이 10분 간격 폴링 → 방송 종료 시 (video_id, title) 반환.

    종료 감지는 search API의 eventType=completed가 아니라(인덱싱 지연이 커서
    1시간 폴링 윈도우 내에 반영되지 않을 수 있음), 방송 중 감지된 video_id를
    videos.list로 직접 조회해 liveStreamingDetails.actualEndTime 존재 여부로 판단한다.
    이 값은 방송 종료 즉시 채워지므로 검색 인덱싱 지연의 영향을 받지 않는다.
    """
    now   = datetime.now()
    start = now.replace(hour=POLL_START_HOUR, minute=0, second=0, microsecond=0)
    end   = now.replace(hour=POLL_END_HOUR,   minute=0, second=0, microsecond=0)

    if now < start:
        secs = int((start - now).total_seconds())
        log.info(f"밤 {POLL_START_HOUR}시까지 {secs // 60}분 대기...")
        time.sleep(secs)

    live_vid = None
    log.info(f"YouTube 폴링 시작 (10분 간격, {POLL_START_HOUR}시~{POLL_END_HOUR}시)")

    while True:
        if datetime.now() >= end:
            log.warning(f"밤 {POLL_END_HOUR}시 초과 — 방송 미감지로 종료합니다.")
            return None, None

        if not live_vid:
            live_vid = get_live_video_id()
            if live_vid:
                log.info("🔴 방송 중 감지")
            else:
                log.info("방송 아직 시작 안 함. 대기...")
        else:
            info     = get_video_status(live_vid)
            ended_at = info.get("liveStreamingDetails", {}).get("actualEndTime")
            if ended_at:
                title = info.get("snippet", {}).get("title", "")
                log.info(f"✅ 방송 종료: {title} ({live_vid})")
                return live_vid, title
            log.info("🔴 방송 진행 중...")

        time.sleep(POLL_INTERVAL_SEC)


# ══════════════════════════════════════════════════════
#  SRT 파싱 + PART 분배  (HTML allocateByDuration 동일)
# ══════════════════════════════════════════════════════

def tc_to_ms(tc: str) -> int:
    m = re.match(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})", tc)
    return (int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])) * 1000 + int(m[4]) if m else 0


def parse_srt(path: Path) -> list[dict]:
    blocks = re.split(r"\n\s*\n", path.read_text(encoding="utf-8-sig").strip())
    result = []
    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 2:
            continue
        try:
            idx = int(lines[0].strip())
        except ValueError:
            continue
        m = re.match(
            r"(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})",
            lines[1].strip(),
        )
        if not m:
            continue
        s, e = tc_to_ms(m[1]), tc_to_ms(m[2])
        result.append({
            "index": idx,
            "start_time": m[1].replace(".", ","),
            "end_time":   m[2].replace(".", ","),
            "duration_ms": max(0, e - s),
        })
    log.info(f"SRT 파싱: {len(result)}개 자막")
    return result


def allocate(subtitles: list[dict], n: int) -> list[dict]:
    total    = sum(s["duration_ms"] for s in subtitles)
    target   = total / n
    result, cursor = [], 0

    for i in range(n):
        if cursor >= len(subtitles):
            break
        if i == n - 1:
            result.append(_row(subtitles[cursor:], i))
            break
        acc, cut = 0, cursor
        for j in range(cursor, len(subtitles)):
            acc += subtitles[j]["duration_ms"]
            cut  = j
            if acc >= target:
                break
        if cut > cursor and abs(acc - subtitles[cut]["duration_ms"] - target) < abs(acc - target):
            cut -= 1
        result.append(_row(subtitles[cursor:cut + 1], i))
        cursor = cut + 1

    return result


def _row(chunk: list[dict], i: int) -> dict:
    return {
        "part":       f"PART {i + 1}",
        "start_idx":  chunk[0]["index"],
        "end_idx":    chunk[-1]["index"],
        "start_time": chunk[0]["start_time"][:8],
        "end_time":   chunk[-1]["end_time"][:8],
    }


def format_result(rows: list[dict]) -> str:
    return "\n".join(
        f"{r['part']}: {r['start_idx']}~{r['end_idx']} / {r['start_time']} → {r['end_time']}"
        for r in rows
    )


# ══════════════════════════════════════════════════════
#  메인
# ══════════════════════════════════════════════════════

def extract_video_id(url: str) -> str:
    """YouTube URL에서 video_id 추출 (watch?v=, /live/, /shorts/ 등 지원)."""
    parsed = urlparse(url)
    if parsed.hostname in ("youtu.be",):
        return parsed.path.lstrip("/").split("?")[0]
    qs = parse_qs(parsed.query)
    if "v" in qs:
        return qs["v"][0]
    # /live/<id> or /shorts/<id>
    parts = parsed.path.strip("/").split("/")
    if len(parts) >= 2 and parts[-2] in ("live", "shorts"):
        return parts[-1].split("?")[0]
    return url  # fallback


def main():
    """
    실행 방법:
      정상 모드:  python plave_auto.py
      테스트 모드: python plave_auto.py <youtube_url>
                   → YouTube 폴링 건너뛰고 지정 URL 바로 사용
    """
    log.info("=== PLAVE 자막 작업 분배 자동화 시작 ===")
    tracker = ExecutionTracker()

    # Notion 업로드일 매칭에 쓰인 날짜를 시작 시점에 고정.
    # YouTube 폴링/ko_draft 대기가 최대 수 시간 걸려 자정을 넘기는 경우에도
    # get_plave_today()가 조회했던 날짜(오늘 시작 시점)와 어긋나지 않게 한다.
    target_date = date.today().isoformat()

    # --hibernate 플래그: 작업 완료 후 Hibernate 진입 (launcher.py의 Hibernate 전략)
    HIBERNATE_AFTER = "--hibernate" in sys.argv
    # CLI 인수로 URL을 넘기면 YouTube 폴링 건너뜀 (테스트용)
    cli_url = next((a for a in sys.argv[1:] if not a.startswith("--")), None)

    # ── 1. Notion 확인 ────────────────────────────────
    workers = get_plave_today()
    if not workers:
        tracker.fail("Notion 확인", "작업자 없음 또는 오늘 항목 없음")
        log.info(tracker.summary())
        tracker.save()
        return

    tracker.ok("Notion 확인", f"작업자 {len(workers)}명")

    # ── 2. pending.json 확인 ──────────────────────────
    pending = load_pending()
    if pending:
        log.info(f"pending 감지 → video_id={pending['video_id']} 재시도 모드 (Gmail 재발송)")
        video_id       = pending["video_id"]
        aicontents_url = pending["aicontents_url"]
        srt_path       = Path(pending["srt_path"])
        title          = pending["title"]
        tracker.skip("YouTube 폴링", "pending 재시도")
        tracker.skip("aicontents 업로드", "pending 재시도")
        tracker.skip("ko_draft 대기", "pending 재시도")
    else:
        # ── 3. YouTube 폴링 ───────────────────────────
        if cli_url:
            yt_vid      = extract_video_id(cli_url)
            youtube_url = cli_url
            title       = f"테스트 ({yt_vid})"
            log.info(f"CLI URL 사용: {youtube_url}")
            tracker.skip("YouTube 폴링", "CLI URL 직접 사용")
        else:
            yt_vid, title = wait_for_live_end()
            if not yt_vid:
                tracker.fail("YouTube 폴링", "방송 미감지 (폴링 시간 초과)")
                log.info(tracker.summary())
                tracker.save()
                return
            youtube_url = f"https://www.youtube.com/watch?v={yt_vid}"
            tracker.ok("YouTube 폴링", title)

        # ── 4. aicontents.team 업로드 ─────────────────
        session = site_login()

        video_id = find_video_by_youtube_url(session, youtube_url)
        if video_id:
            log.info(f"기존 영상 재사용 → video_id={video_id}")
            tracker.skip("aicontents 업로드", f"기존 video_id={video_id}")
        else:
            video_id = upload_and_get_video_id(session, youtube_url)
            tracker.ok("aicontents 업로드", f"video_id={video_id}")

        aicontents_url = f"{SITE_URL}/videos/{video_id}"

        # ── 5. ko_draft.srt 대기 ──────────────────────
        try:
            srt_path = wait_for_ko_draft(session, video_id, yt_vid)
            tracker.ok("ko_draft 대기", srt_path.name)
        except VideoUnavailableError as e:
            tracker.fail("ko_draft 대기", str(e))
            log.error(str(e))
            log.info(tracker.summary())
            tracker.save()
            return

    # ── 6. SRT 파싱 + 분배 ────────────────────────────
    subtitles = parse_srt(srt_path)
    rows      = allocate(subtitles, len(workers))
    log.info(f"\n{format_result(rows)}")
    tracker.ok("SRT 분배", f"{len(workers)}명")

    # Gmail 실패 대비 저장
    save_pending(video_id, aicontents_url, srt_path, title)

    # ── 7. 메일 발송 ──────────────────────────────────
    for worker, row in zip(workers, rows):
        send_worker_email(worker, row, title, aicontents_url)
        tracker.ok("메일 발송", f"{worker['name']} ({row['part']})")

    # Notion 상태 업데이트: 작업 전 → 한국어 QC중 (ko_draft 파일 작업자 발송 완료)
    from notion_gate import update_status_by_workflow
    if update_status_by_workflow("ko_draft_sent", target_date):
        tracker.ok("Notion 상태 업데이트", f"작업 전 → 한국어 QC중 ({target_date})")
    else:
        tracker.warn("Notion 상태 업데이트", f"실패 (date={target_date})")

    clear_pending()

    log.info(tracker.summary())
    tracker.save()
    log.info("=== 완료 ===")

    # Hibernate 전략으로 실행된 경우: 임시 tasks 정리 후 Hibernate 진입
    if HIBERNATE_AFTER:
        for task_name in ["plave_wake_tonight", "plave_auto_tonight"]:
            subprocess.run(
                ["schtasks", "/delete", "/tn", task_name, "/f"],
                capture_output=True,
            )
        log.info("💤 임시 tasks 정리 완료. 30초 후 Hibernate 진입...")
        time.sleep(30)
        subprocess.run(["shutdown", "/h"], check=False)


if __name__ == "__main__":
    main()
