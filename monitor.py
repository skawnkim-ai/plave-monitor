"""
자막 납품 메일 자동화 모니터
────────────────────────────────────────────────
aicontents.team/videos 를 주기적으로 감시하여
1. 파일명에 PLAVE + ko_QC 가 포함된 파일 발견 시 → 클라이언트에 한국어 자막 납품 메일 발송
2. 납품 후 EN/JA/ZH 번역 자동 트리거
3. 번역 완료(en.srt/ja.srt/zh.srt) 시 → 번역 업체(Quokka Labs)에 작업 파일 전달 메일 발송

실행: python monitor.py
종료: Ctrl+C
"""

import base64
import json
import os
import re
import shutil
import sys
import time
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from notion_gate import is_delivery_active, update_status_by_workflow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from logger import get_logger

load_dotenv()
logger = get_logger(__name__)

# ══════════════════════════════════════════════════════
#  ★ 설정 — .env 파일에서 로드
# ══════════════════════════════════════════════════════

# 사이트 계정
SITE_EMAIL = os.getenv("AICONTENTS_EMAIL", "")
SITE_PASS  = os.getenv("AICONTENTS_PASSWORD", "")

# ★ 테스트 모드: True = 본인에게만 발송 / False = 실제 발송
TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"

# ── 이메일 서명 ──────────────────────────────────────
SIGNATURE_HTML = """<div style="font-size:14px;font-family:Gulim,굴림,sans-serif;"><table cellpadding="0" cellspacing="0" style="font-family:'Pretendard Variable',Pretendard,-apple-system,BlinkMacSystemFont,system-ui,Roboto,'Helvetica Neue','Segoe UI','Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic','Apple Color Emoji','Segoe UI Emoji','Segoe UI Symbol',sans-serif;color:rgb(99,102,241);"><tbody><tr><td style="vertical-align:middle;padding-right:15px;"><img src="https://i.imgur.com/JpADtyK.png" width="100" height="100" style="display:block;" loading="lazy"></td><td style="vertical-align:middle;"><strong style="font-size:16px;color:rgb(99,102,241);">김남주</strong><span style="color:rgb(99,102,241);margin-left:5px;">&nbsp;AI콘텐츠팀 PD</span><br><span style="color:rgb(156,163,175);">Kim nam ju / </span><span style="color:rgb(156,163,175);">AI-contents Team Producer</span><br><div style="height:5px;"></div><strong style="color:rgb(99,102,241);">M</strong> <span style="color:rgb(99,102,241);">010-2286-1893 </span><strong style="color:rgb(99,102,241);">E</strong> <span style="color:rgb(99,102,241);">skawn.kim@dost11.kr</span><br><div style="height:5px;"></div><span style="color:rgb(156,163,175);">255, Seongam-ro, Mapo-gu, Seoul, Republic of Korea </span><strong style="color:rgb(99,102,241);">11F</strong></td></tr></tbody></table></div>"""

def build_html_body(plain_text: str) -> str:
    """plain text 본문을 HTML로 변환하고 서명을 붙임."""
    html_body = plain_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
    return f'<div style="font-family:sans-serif;font-size:14px;">{html_body}</div><br>{SIGNATURE_HTML}'

# ── VLAST 납품 메일 (.env에서 로드) ───────────────────
MAIL_TO = os.getenv("MAIL_TO", "")
MAIL_CC = os.getenv("MAIL_CC", "")
TEST_MAIL_TO = os.getenv("SMTP_USER", "")
TEST_MAIL_CC = os.getenv("TEST_MAIL_CC", "")

# ── 번역 파일 전달 메일 (Quokka Labs, .env에서 로드) ──
TRANSLATION_MAIL_TO = os.getenv("TRANSLATION_MAIL_TO", "")
TRANSLATION_MAIL_CC = os.getenv("TRANSLATION_MAIL_CC", "")
TEST_TRANSLATION_MAIL_TO = os.getenv("SMTP_USER", "")
TEST_TRANSLATION_MAIL_CC = os.getenv("TEST_TRANSLATION_MAIL_CC", "")

# 번역 대상 언어
TARGET_LANGUAGES = ["en", "ja", "zh"]

# QC 파일 언어별 키워드
QC_KEYWORDS = {"en": "en_QC", "ja": "ja_QC", "zh": "zh_QC"}

# 2차 번역 대상 언어
SECONDARY_LANGUAGES = ["vi", "ind", "es", "th"]

# 확인 주기 (초)
CHECK_INTERVAL = 300

# ══════════════════════════════════════════════════════
#  내부 설정 — 수정 불필요
# ══════════════════════════════════════════════════════

BASE_DIR         = os.path.dirname(os.path.abspath(__file__))
SENT_LOG         = os.path.join(BASE_DIR, "sent_log.json")
SENT_LOG_BACKUP  = os.path.join(BASE_DIR, "sent_log.backup.json")
TOKEN_FILE       = os.path.join(BASE_DIR, "token.json")
CREDENTIALS_FILE = os.path.join(BASE_DIR, "credentials.json")

SITE_URL   = "https://www.aicontents.team"
LOGIN_PATH = "/v3/users/sign_in"

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

INITIAL_SENT_IDS = [2, 7, 8, 13, 17, 19, 24, 32, 39, 51, 61, 64, 67, 70, 72, 74, 78]

# 재시도 설정
RETRY_COUNT   = 3
RETRY_DELAYS  = [5, 10, 20]  # 초 단위, backoff


# ══════════════════════════════════════════════════════
#  재시도 데코레이터
# ══════════════════════════════════════════════════════

def with_retry(func):
    """
    3회 재시도 (5 → 10 → 20초 backoff).
    3회 모두 실패 시 → ERROR 로그 후 monitor 종료.
    """
    def wrapper(*args, **kwargs):
        last_exc = None
        for attempt in range(RETRY_COUNT):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_exc = e
                if attempt < RETRY_COUNT - 1:
                    delay = RETRY_DELAYS[attempt]
                    logger.warning(
                        f"{func.__name__} 실패 ({attempt + 1}/{RETRY_COUNT}) "
                        f"→ {e} — {delay}초 후 재시도"
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        f"{func.__name__} 3회 모두 실패 → {e} — monitor 종료"
                    )
                    sys.exit(1)
    return wrapper


# ══════════════════════════════════════════════════════
#  발송 로그
# ══════════════════════════════════════════════════════

def load_sent_log() -> dict:
    """
    sent_log.json 로드.
    파일 손상 시 백업(sent_log.backup.json)으로 자동 복구.
    구형 flat list 포맷이면 자동 마이그레이션.
    """
    # 백업 복구 시도 (sent_log.json 손상 시)
    if os.path.exists(SENT_LOG):
        try:
            with open(SENT_LOG, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"sent_log.json 손상 → 백업으로 복구 시도: {e}")
            if os.path.exists(SENT_LOG_BACKUP):
                shutil.copy2(SENT_LOG_BACKUP, SENT_LOG)
                logger.info("백업에서 sent_log.json 복구 완료")
                with open(SENT_LOG, encoding="utf-8") as f:
                    data = json.load(f)
            else:
                logger.error("sent_log.json 손상 + 백업 없음 → monitor 종료")
                sys.exit(1)

        # 구형 포맷 마이그레이션
        if isinstance(data, list):
            logger.info("sent_log.json 포맷 업그레이드 중...")
            data = {
                "ko_sent": data,
                "translation_triggered": list(data),
                "translation_sent": list(data),
                "qc_sent": list(data),
            }
            _save_sent_log_raw(data)

        # 누락된 키 자동 추가
        needs_save = False
        for key in ["qc_sent", "secondary_triggered", "secondary_sent"]:
            if key not in data:
                data[key] = list(data.get("ko_sent", []))
                needs_save = True
        if needs_save:
            _save_sent_log_raw(data)

        return {k: set(v) for k, v in data.items()}

    # 최초 실행
    ids = INITIAL_SENT_IDS
    data = {
        "ko_sent": set(ids),
        "translation_triggered": set(ids),
        "translation_sent": set(ids),
        "qc_sent": set(ids),
        "secondary_triggered": set(ids),
        "secondary_sent": set(ids),
    }
    save_sent_log(data)
    logger.info(f"sent_log.json 초기화 — {len(ids)}개 항목 등록")
    return data


def save_sent_log(log: dict):
    """
    sent_log.json 저장.
    저장 전 백업 생성 → 저장 실패 시 ERROR 로그 + monitor 종료.
    """
    data = {k: sorted(v) for k, v in log.items()}

    # 기존 파일이 있으면 백업 먼저 생성
    if os.path.exists(SENT_LOG):
        try:
            shutil.copy2(SENT_LOG, SENT_LOG_BACKUP)
            logger.debug("sent_log.backup.json 갱신 완료")
        except OSError as e:
            logger.warning(f"백업 생성 실패 → {e} (저장은 계속 시도)")

    # 실제 저장
    try:
        _save_sent_log_raw(data)
    except OSError as e:
        logger.error(f"sent_log.json 저장 실패 → {e} — monitor 종료")
        sys.exit(1)


def _save_sent_log_raw(data: dict):
    with open(SENT_LOG, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ══════════════════════════════════════════════════════
#  Gmail
# ══════════════════════════════════════════════════════

def get_gmail_service():
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, GMAIL_SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def parse_date_from_filename(filename: str) -> str:
    """파일명 앞 YYYYMMDD에서 'N월 N일' 형식으로 날짜 추출."""
    m = re.match(r'\d{2,4}(\d{2})(\d{2})', filename)
    if m:
        return f"{int(m.group(1))}월 {int(m.group(2))}일"
    return "N월 N일"


@with_retry
def send_email(session: requests.Session, video: dict, file_obj: dict):
    """클라이언트에 한국어 자막 납품 메일 발송."""
    filename = file_obj["filename"]
    date_str = parse_date_from_filename(filename)

    subject = f"[플레이브 라이브] {date_str}자 한국어 자막 전달의 건"
    body    = (
        f"안녕하세요. 도스트 일레븐 김남주입니다.\n"
        f"{date_str}에 진행된 플레이브 유튜브 라이브 한국어 SRT 자막 전달 드립니다.\n\n"
        f"첨부파일 확인해주시면 감사하겠습니다.\n"
        f"감사합니다."
    )

    to = TEST_MAIL_TO if TEST_MODE else MAIL_TO
    cc = TEST_MAIL_CC if TEST_MODE else MAIL_CC

    msg = MIMEMultipart()
    msg["to"]      = to
    msg["subject"] = subject
    if cc:
        msg["cc"] = cc
    msg.attach(MIMEText(build_html_body(body), "html", "utf-8"))

    logger.debug(f"파일 다운로드 중 → filename={filename}")
    file_content = download_file(session, file_obj)
    attachment = MIMEBase("application", "octet-stream")
    attachment.set_payload(file_content)
    encoders.encode_base64(attachment)
    attachment.add_header("Content-Disposition", "attachment", filename=filename)
    msg.attach(attachment)
    logger.debug(f"첨부 완료 → filename={filename}, size={len(file_content) // 1024}KB")

    raw     = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service = get_gmail_service()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()

    mode_label = "[테스트]" if TEST_MODE else "[실제]"
    logger.info(f"{mode_label} 납품 메일 발송 완료 → to={to}, subject={subject}")


@with_retry
def send_translation_email(session: requests.Session, video: dict, trans_files: dict, ko_file: dict):
    """번역 업체(Quokka Labs)에 EN/JA/ZH 작업 파일 전달 메일 발송."""
    date_str = parse_date_from_filename(ko_file["filename"])

    subject  = f"[플레이브 라이브] {date_str}자 영어/일본어/중국어 자막 작업 전달의 건"
    video_url = f"{SITE_URL}/videos/{video['id']}"
    body    = (
        f"안녕하세요. 도스트 일레븐 김남주입니다.\n"
        f"{date_str}에 진행된 플레이브 유튜브 라이브 작업 파일 전달 드립니다.\n"
        f"첨부파일 확인해주시면 감사하겠습니다.\n\n"
        f"한국어 자막 파일 및 특이사항은 홈페이지를 확인해주세요.\n"
        f"▶{video_url}\n\n"
        f"감사합니다."
    )

    to = TEST_TRANSLATION_MAIL_TO if TEST_MODE else TRANSLATION_MAIL_TO
    cc = TEST_TRANSLATION_MAIL_CC if TEST_MODE else TRANSLATION_MAIL_CC

    msg = MIMEMultipart()
    msg["to"]      = to
    msg["subject"] = subject
    if cc:
        msg["cc"] = cc
    msg.attach(MIMEText(build_html_body(body), "html", "utf-8"))

    for lang in TARGET_LANGUAGES:
        f = trans_files[lang]
        logger.debug(f"번역 파일 다운로드 중 → filename={f['filename']}")
        content = download_file(session, f)
        att = MIMEBase("application", "octet-stream")
        att.set_payload(content)
        encoders.encode_base64(att)
        att.add_header("Content-Disposition", "attachment", filename=f["filename"])
        msg.attach(att)
        logger.debug(f"첨부 완료 → filename={f['filename']}, size={len(content) // 1024}KB")

    raw     = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service = get_gmail_service()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()

    mode_label = "[테스트]" if TEST_MODE else "[실제]"
    logger.info(f"{mode_label} 번역 메일 발송 완료 → to={to}, subject={subject}")


@with_retry
def send_qc_email(session: requests.Session, video: dict, qc_files: dict):
    """클라이언트에 EN/JA/ZH QC 파일 전달 메일 발송."""
    first_file = qc_files.get("en") or qc_files.get("ja") or qc_files.get("zh")
    date_str = parse_date_from_filename(first_file["filename"])

    subject = f"[플레이브 라이브] {date_str} 영어/중국어/일본어 QC 전달의 건"
    body    = (
        f"안녕하세요. 도스트 일레븐 김남주입니다.\n"
        f"{date_str}에 진행된 플레이브 유튜브 라이브 영어/중국어/일본어 QC SRT 자막 전달 드립니다.\n\n"
        f"첨부파일 확인해주시면 감사하겠습니다.\n"
        f"감사합니다."
    )

    to = TEST_MAIL_TO if TEST_MODE else MAIL_TO
    cc = TEST_MAIL_CC if TEST_MODE else MAIL_CC

    msg = MIMEMultipart()
    msg["to"]      = to
    msg["subject"] = subject
    if cc:
        msg["cc"] = cc
    msg.attach(MIMEText(build_html_body(body), "html", "utf-8"))

    for lang in ["en", "ja", "zh"]:
        f = qc_files[lang]
        logger.debug(f"QC 파일 다운로드 중 → filename={f['filename']}")
        content = download_file(session, f)
        att = MIMEBase("application", "octet-stream")
        att.set_payload(content)
        encoders.encode_base64(att)
        att.add_header("Content-Disposition", "attachment", filename=f["filename"])
        msg.attach(att)
        logger.debug(f"첨부 완료 → filename={f['filename']}, size={len(content) // 1024}KB")

    raw     = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service = get_gmail_service()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()

    mode_label = "[테스트]" if TEST_MODE else "[실제]"
    logger.info(f"{mode_label} QC 메일 발송 완료 → to={to}, subject={subject}")


@with_retry
def send_secondary_email(session: requests.Session, video: dict, sec_files: dict, en_qc_file: dict):
    """클라이언트에 vi/id/es/th 4개국어 자막 납품 메일 발송."""
    date_str = parse_date_from_filename(en_qc_file["filename"])

    subject = f"[플레이브 라이브] {date_str}자 4개국어 자막 전달의 건"
    body    = (
        f"안녕하세요. 도스트 일레븐 김남주입니다.\n"
        f"{date_str}에 진행된 플레이브 유튜브 라이브 4개국어 자막 전달 드립니다.\n\n"
        f"첨부파일 확인해주시면 감사하겠습니다.\n"
        f"감사합니다."
    )

    to = TEST_MAIL_TO if TEST_MODE else MAIL_TO
    cc = TEST_MAIL_CC if TEST_MODE else MAIL_CC

    msg = MIMEMultipart()
    msg["to"]      = to
    msg["subject"] = subject
    if cc:
        msg["cc"] = cc
    msg.attach(MIMEText(build_html_body(body), "html", "utf-8"))

    for lang in SECONDARY_LANGUAGES:
        f = sec_files[lang]
        logger.debug(f"4개국어 파일 다운로드 중 → filename={f['filename']}")
        content = download_file(session, f)
        att = MIMEBase("application", "octet-stream")
        att.set_payload(content)
        encoders.encode_base64(att)
        att.add_header("Content-Disposition", "attachment", filename=f["filename"])
        msg.attach(att)
        logger.debug(f"첨부 완료 → filename={f['filename']}, size={len(content) // 1024}KB")

    raw     = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service = get_gmail_service()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()

    mode_label = "[테스트]" if TEST_MODE else "[실제]"
    logger.info(f"{mode_label} 4개국어 메일 발송 완료 → to={to}, subject={subject}")


# ══════════════════════════════════════════════════════
#  사이트
# ══════════════════════════════════════════════════════

@with_retry
def site_login() -> requests.Session:
    from urllib.parse import unquote
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})

    session.get(f"{SITE_URL}{LOGIN_PATH}")
    xsrf = session.cookies.get("XSRF-TOKEN")
    if not xsrf:
        raise RuntimeError("XSRF-TOKEN 쿠키를 찾을 수 없음")
    xsrf = unquote(xsrf)

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
        location = r.headers.get("Location", "/videos")
        if not location.startswith("http"):
            location = SITE_URL + location
        session.get(location)
    elif r.status_code not in (200, 201, 204):
        raise RuntimeError(f"로그인 실패 → status={r.status_code}, body={r.text[:200]}")

    logger.info("로그인 성공")
    return session


@with_retry
def get_video_list(session: requests.Session) -> list:
    r = session.get(
        f"{SITE_URL}/videos",
        headers={
            "X-Inertia": "true",
            "X-Inertia-Version": "1.0",
            "Accept": "application/json, text/plain, */*",
        },
    )
    if r.status_code != 200:
        raise RuntimeError(f"비디오 목록 조회 실패 → status={r.status_code}")
    return r.json()["props"]["videos"]


@with_retry
def get_video_files(session: requests.Session, video_id: int) -> list:
    r = session.get(
        f"{SITE_URL}/videos/{video_id}",
        headers={
            "X-Inertia": "true",
            "X-Inertia-Version": "1.0",
            "Accept": "application/json, text/plain, */*",
        },
    )
    if r.status_code != 200:
        raise RuntimeError(f"비디오 파일 조회 실패 → video_id={video_id}, status={r.status_code}")
    data  = r.json()
    video = data.get("props", {}).get("video") or data.get("video", {})
    return video.get("files", [])


def find_qualifying_file(files: list) -> dict | None:
    for f in files:
        name = f.get("filename", "")
        if "PLAVE" in name and "ko_QC" in name and f.get("status") == "available":
            return f
    return None


def find_qc_files(files: list) -> dict:
    result = {}
    for f in files:
        name = f.get("filename", "")
        if "PLAVE" not in name or f.get("status") != "available":
            continue
        for lang, keyword in QC_KEYWORDS.items():
            if keyword in name:
                result[lang] = f
    return result


def find_translation_files(files: list) -> dict:
    result = {}
    for f in files:
        lang = f.get("languageCode", "")
        if lang in TARGET_LANGUAGES and f.get("status") == "available":
            result[lang] = f
    return result


def find_secondary_translation_files(files: list) -> dict:
    result = {}
    for f in files:
        lang = f.get("languageCode", "")
        if lang in SECONDARY_LANGUAGES and f.get("status") == "available":
            result[lang] = f
    return result


@with_retry
def download_file(session: requests.Session, file_obj: dict) -> bytes:
    url = SITE_URL + file_obj["downloadUrl"]
    r = session.get(url, allow_redirects=True)
    r.raise_for_status()
    return r.content


def trigger_translation(session: requests.Session, video_id: int, file_obj: dict,
                        languages: list = None) -> bool:
    """번역 트리거. 실패 시 WARNING만 기록하고 False 반환 (다음 주기에 재시도)."""
    if languages is None:
        languages = TARGET_LANGUAGES

    translate_path = file_obj.get("translatePath")
    if not translate_path:
        logger.warning(f"번역 트리거 불가 → translatePath 없음, video_id={video_id}")
        return False

    from urllib.parse import unquote
    xsrf = unquote(session.cookies.get("XSRF-TOKEN", ""))

    try:
        r = session.post(
            SITE_URL + translate_path,
            headers={
                "X-XSRF-TOKEN": xsrf,
                "X-Inertia": "true",
                "X-Inertia-Version": "1.0",
                "Content-Type": "application/json",
                "Accept": "application/json, text/plain, */*",
                "Origin": SITE_URL,
            },
            json={"target_languages": languages},
        )
    except Exception as e:
        logger.warning(f"번역 트리거 요청 실패 → video_id={video_id}, error={e}")
        return False

    if r.status_code in (200, 202):
        logger.info(f"번역 트리거 완료 → video_id={video_id}, languages={languages}")
        return True
    else:
        logger.warning(f"번역 트리거 실패 → video_id={video_id}, status={r.status_code}")
        return False


# ══════════════════════════════════════════════════════
#  메인 루프
# ══════════════════════════════════════════════════════

def check_once(session: requests.Session, log: dict):
    ko_sent               = log["ko_sent"]
    translation_triggered = log["translation_triggered"]
    translation_sent      = log["translation_sent"]
    qc_sent               = log["qc_sent"]
    secondary_triggered   = log["secondary_triggered"]
    secondary_sent        = log["secondary_sent"]

    # 세션 만료 시 재로그인 (with_retry가 내부에 있으므로 여기선 단순 호출)
    try:
        videos = get_video_list(session)
    except SystemExit:
        raise  # with_retry의 sys.exit()은 그대로 전파
    except Exception:
        logger.warning("세션 만료 → 재로그인 시도")
        session = site_login()
        videos  = get_video_list(session)

    # ── 1. ko_QC 납품 ───────────────────────────────────
    new_videos = [v for v in videos if v["id"] not in ko_sent]
    if not new_videos:
        logger.debug("새 ko_QC 항목 없음")

    for v in new_videos:
        vid = v["id"]
        logger.info(f"미처리 항목 발견 → video_id={vid}, title={v['title']}")

        files = get_video_files(session, vid)
        qfile = find_qualifying_file(files)

        if qfile:
            logger.info(f"ko_QC 파일 확인 → filename={qfile['filename']}")
            send_email(session, v, qfile)  # 실패 시 with_retry → 3회 후 종료
            ko_sent.add(vid)
            
            # Notion 상태 업데이트: 한국어 QC중 → En/Ja QC 중
            date_str = parse_date_from_filename(qfile['filename'])
            # YYYYMMDD 형식에서 YYYY-MM-DD로 변환
            date_match = re.match(r'(\d{2,4})(\d{2})(\d{2})', qfile['filename'])
            if date_match:
                year = date_match.group(1)
                if len(year) == 2:
                    year = "20" + year
                month = date_match.group(2) 
                day = date_match.group(3)
                target_date = f"{year}-{month}-{day}"
                update_status_by_workflow("ko_qc_completed", target_date)

            if trigger_translation(session, vid, qfile):
                translation_triggered.add(vid)

            save_sent_log(log)  # 실패 시 종료
        else:
            logger.debug(f"ko_QC 미등록 → 다음 주기에 재확인, video_id={vid}")

    video_map = {v["id"]: v for v in videos}

    # ── 2. 번역 완료 감시 ────────────────────────────────
    pending_translation = ko_sent - translation_sent
    if not pending_translation:
        logger.debug("번역 대기 항목 없음")
    else:
        for vid in list(pending_translation):
            v_info = video_map.get(vid, {"id": vid, "title": f"video {vid}"})
            logger.info(f"번역 완료 확인 중 → video_id={vid}, title={v_info.get('title', '')}")

            files       = get_video_files(session, vid)
            trans_files = find_translation_files(files)

            if len(trans_files) == len(TARGET_LANGUAGES):
                ko_file = find_qualifying_file(files)
                if ko_file:
                    logger.info(f"번역 3개 완료 → 번역 메일 발송, video_id={vid}")
                    send_translation_email(session, v_info, trans_files, ko_file)
                    translation_sent.add(vid)
                    save_sent_log(log)
            else:
                if vid not in translation_triggered:
                    ko_file = find_qualifying_file(files)
                    if ko_file and trigger_translation(session, vid, ko_file):
                        translation_triggered.add(vid)
                        save_sent_log(log)
                logger.debug(f"번역 대기 중 → video_id={vid}, {len(trans_files)}/{len(TARGET_LANGUAGES)}")

    # ── 3. EN/JA/ZH QC 완료 감시 ────────────────────────
    pending_qc = ko_sent - qc_sent
    if not pending_qc:
        logger.debug("QC 대기 항목 없음")
    else:
        for vid in list(pending_qc):
            v_info = video_map.get(vid, {"id": vid, "title": f"video {vid}"})
            logger.info(f"QC 파일 확인 중 → video_id={vid}, title={v_info.get('title', '')}")

            files    = get_video_files(session, vid)
            qc_files = find_qc_files(files)
            ko_file  = find_qualifying_file(files)

            if len(qc_files) == 3:
                if ko_file:
                    logger.info(f"QC 3개 완료 → QC 메일 발송, video_id={vid}")
                    send_qc_email(session, v_info, qc_files)
                    qc_sent.add(vid)
                    
                    # Notion 상태 업데이트: En/Ja QC 중 → En/Ja QC 완료
                    first_qc_file = qc_files.get("en") or qc_files.get("ja") or qc_files.get("zh")
                    date_match = re.match(r'(\d{2,4})(\d{2})(\d{2})', first_qc_file['filename'])
                    if date_match:
                        year = date_match.group(1)
                        if len(year) == 2:
                            year = "20" + year
                        month = date_match.group(2)
                        day = date_match.group(3) 
                        target_date = f"{year}-{month}-{day}"
                        update_status_by_workflow("en_ja_qc_completed", target_date)

                    en_qc = qc_files.get("en")
                    if en_qc and vid not in secondary_triggered:
                        if trigger_translation(session, vid, en_qc, SECONDARY_LANGUAGES):
                            secondary_triggered.add(vid)

                    save_sent_log(log)
            else:
                logger.debug(f"QC 대기 중 → video_id={vid}, {len(qc_files)}/3")

    # ── 4. 4개국어 번역 완료 감시 ────────────────────────
    pending_secondary = qc_sent - secondary_sent
    if not pending_secondary:
        logger.debug("4개국어 대기 항목 없음")
    else:
        for vid in list(pending_secondary):
            v_info = video_map.get(vid, {"id": vid, "title": f"video {vid}"})
            logger.info(f"4개국어 파일 확인 중 → video_id={vid}, title={v_info.get('title', '')}")

            files     = get_video_files(session, vid)
            sec_files = find_secondary_translation_files(files)
            en_qc     = find_qualifying_file(files)

            if len(sec_files) == len(SECONDARY_LANGUAGES) and en_qc:
                logger.info(f"4개국어 완료 → 4개국어 메일 발송, video_id={vid}")
                send_secondary_email(session, v_info, sec_files, en_qc)
                secondary_sent.add(vid)
                
                # Notion 상태 업데이트: En/Ja QC 완료 → 납품 완료
                date_match = re.match(r'(\d{2,4})(\d{2})(\d{2})', en_qc['filename'])
                if date_match:
                    year = date_match.group(1)
                    if len(year) == 2:
                        year = "20" + year
                    month = date_match.group(2)
                    day = date_match.group(3)
                    target_date = f"{year}-{month}-{day}"
                    update_status_by_workflow("final_delivery", target_date)
                    
                save_sent_log(log)
            else:
                logger.debug(
                    f"4개국어 대기 중 → video_id={vid}, "
                    f"{len(sec_files)}/{len(SECONDARY_LANGUAGES)}"
                )


# ══════════════════════════════════════════════════════
#  메인 루프
# ══════════════════════════════════════════════════════

def run_monitor():
    """5분 간격으로 check_once()를 반복 호출."""
    logger.info("=" * 50)
    logger.info("자막 납품 모니터 시작")
    logger.info("=" * 50)

    session = site_login()
    log     = load_sent_log()

    while True:
        if not is_delivery_active():
            logger.info("활성 납품 항목 없음 — 모니터 종료")
            break

        try:
            check_once(session, log)
        except SystemExit:
            raise
        except Exception as e:
            logger.error(f"check_once 오류: {e}", exc_info=True)

        logger.debug(f"{CHECK_INTERVAL // 60}분 후 재확인...")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    run_monitor()
