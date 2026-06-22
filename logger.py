"""
로깅 모듈
────────────────────────────────────────────────
- 파일(monitor.log) + 콘솔 동시 출력
- 로그 레벨: DEBUG / INFO / WARNING / ERROR
- ERROR 발생 시 skawn.kim@dost11.kr 로 Gmail 알림
- 동일 에러는 첫 번째 발생 시 1회만 알림 발송

사용법:
    from logger import get_logger
    logger = get_logger(__name__)

    logger.debug("파일 다운로드 시작 → filename=xxx.srt")
    logger.info("메일 발송 완료 → video_id=78")
    logger.warning("Notion API 실패 → 감시 계속 유지")
    logger.error("로그인 실패 → status=401")
"""

import logging
import os
import base64
from email.mime.text import MIMEText

# ══════════════════════════════════════════════════════
#  설정
# ══════════════════════════════════════════════════════

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
LOG_FILE  = os.path.join(BASE_DIR, "monitor.log")

ALERT_TO  = "skawn.kim@dost11.kr"

# ══════════════════════════════════════════════════════
#  내부 상태 — 이미 알림 발송한 에러 추적
# ══════════════════════════════════════════════════════

_alerted_errors: set[str] = set()


# ══════════════════════════════════════════════════════
#  Gmail 알림 핸들러
# ══════════════════════════════════════════════════════

class GmailAlertHandler(logging.Handler):
    """
    ERROR 레벨 로그 발생 시 Gmail로 알림 발송.
    동일한 에러 메시지는 첫 번째 발생 시 1회만 발송.
    Gmail 서비스 초기화 실패 시 콘솔 경고만 출력하고 계속 진행.
    """

    def emit(self, record: logging.LogRecord):
        msg = self.format(record)
        key = record.getMessage()  # 에러 메시지를 중복 체크 키로 사용

        if key in _alerted_errors:
            return  # 이미 알림 발송한 에러 → 스킵

        _alerted_errors.add(key)

        try:
            self._send_alert(record, msg)
        except Exception as e:
            # 알림 발송 실패는 콘솔에만 출력 (무한 루프 방지)
            print(f"[ALERT 발송 실패] {e}")

    def _send_alert(self, record: logging.LogRecord, formatted_msg: str):
        """Gmail API로 에러 알림 메일 발송."""
        import json
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        token_file = os.path.join(BASE_DIR, "token.json")
        scopes     = ["https://www.googleapis.com/auth/gmail.send"]

        if not os.path.exists(token_file):
            print("[ALERT 발송 실패] token.json 없음")
            return

        creds = Credentials.from_authorized_user_file(token_file, scopes)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(token_file, "w") as f:
                f.write(creds.to_json())

        service = build("gmail", "v1", credentials=creds)

        subject = f"[자막 모니터] ERROR 발생 — {record.name}"
        body    = (
            f"자막 납품 자동화 모니터에서 오류가 발생했습니다.\n\n"
            f"{'─' * 50}\n"
            f"{formatted_msg}\n"
            f"{'─' * 50}\n\n"
            f"monitor.log 를 확인해주세요.\n"
            f"위치: {LOG_FILE}"
        )

        mime_msg = MIMEText(body, "plain", "utf-8")
        mime_msg["to"]      = ALERT_TO
        mime_msg["subject"] = subject

        raw = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode()
        service.users().messages().send(userId="me", body={"raw": raw}).execute()

        print(f"[ALERT] 에러 알림 발송 완료 → {ALERT_TO}")


# ══════════════════════════════════════════════════════
#  로거 초기화
# ══════════════════════════════════════════════════════

def _setup_root_logger():
    """
    루트 로거 최초 1회 설정.
    - 파일 핸들러: monitor.log (DEBUG 이상 전부 기록)
    - 콘솔 핸들러: INFO 이상 출력
    - Gmail 핸들러: ERROR 이상 알림
    """
    root = logging.getLogger()

    # 이미 핸들러가 설정됐으면 중복 등록 방지
    if root.handlers:
        return

    root.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        fmt="[%(asctime)s] [%(levelname)-7s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ── 파일 핸들러 (DEBUG 이상 전부) ──────────────────
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)

    # ── 콘솔 핸들러 (INFO 이상) ────────────────────────
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(fmt)

    # ── Gmail 알림 핸들러 (ERROR 이상) ─────────────────
    gmail_handler = GmailAlertHandler()
    gmail_handler.setLevel(logging.ERROR)
    gmail_handler.setFormatter(fmt)

    root.addHandler(file_handler)
    root.addHandler(console_handler)
    root.addHandler(gmail_handler)


def get_logger(name: str) -> logging.Logger:
    """
    모듈별 로거 반환.
    최초 호출 시 루트 로거 설정도 함께 수행.

    사용법:
        logger = get_logger(__name__)
        logger.info("메일 발송 완료 → video_id=78")
    """
    _setup_root_logger()
    return logging.getLogger(name)