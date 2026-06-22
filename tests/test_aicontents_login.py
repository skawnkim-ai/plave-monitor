"""
aicontents.team 로그인 + 비디오 목록 조회 (기존 test_login.py를 pytest로 변환).
읽기 전용 — 발송/업로드 없음.

실행: pytest tests/test_aicontents_login.py --run-live
"""
import pytest

from monitor import SITE_URL


@pytest.mark.live
def test_login_succeeds(aicontents_session):
    assert aicontents_session is not None
    assert "sign_in" not in (aicontents_session.cookies.get("XSRF-TOKEN") or "")


@pytest.mark.live
def test_video_list_not_empty(aicontents_session):
    r = aicontents_session.get(
        f"{SITE_URL}/videos",
        headers={
            "X-Inertia": "true",
            "X-Inertia-Version": "1.0",
            "Accept": "application/json, text/plain, */*",
        },
    )
    assert r.status_code == 200

    videos = r.json()["props"]["videos"]
    assert isinstance(videos, list)
    assert len(videos) > 0
    assert "id" in videos[0] and "title" in videos[0]
