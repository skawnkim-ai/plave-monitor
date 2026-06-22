"""
이메일 서명/발송 경로 점검 (기존 test_signature.py, test_send.py를 pytest로 변환).

⚠️ TEST_MODE=true 가 아니면 자동 스킵됨 — 실제 클라이언트/작업자에게 메일이 나가는 사고를 방지.
TEST_MODE=true 환경에서 --run-live 와 함께 실행하면 본인(SMTP_USER)에게만 테스트 메일이 발송됨.

실행: TEST_MODE=true pytest tests/test_email_signature.py --run-live -m email
"""
import pytest

import monitor


@pytest.mark.email
def test_test_mode_is_on():
    """안전장치 자체를 검증 — 이 파일의 다른 테스트가 절대 실제 발송으로 새지 않는지 확인."""
    assert monitor.TEST_MODE is True
    assert monitor.TEST_MAIL_TO  # SMTP_USER가 비어있지 않은지


@pytest.mark.email
def test_check_once_runs_without_error(aicontents_session):
    """check_once() 1회 실행 — 서명/발송 경로가 예외 없이 도는지 확인 (TEST_MODE라 본인에게만 발송됨)."""
    log = monitor.load_sent_log()
    session, log = monitor.check_once(aicontents_session, log)
    assert session is not None
    assert isinstance(log, dict)
