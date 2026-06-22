"""
pytest 공통 설정.

이 프로젝트의 테스트는 실제 외부 서비스(aicontents.team, Gmail)를 건드릴 수 있으므로
기본적으로는 안전한 것만 돌고, 네트워크/메일 발송이 필요한 테스트는 명시적으로 opt-in해야 함.

마커:
    @pytest.mark.live   — 실제 aicontents.team 로그인 등 네트워크 호출 필요 (자격증명 필요)
    @pytest.mark.email  — 실제로 이메일을 발송할 수 있는 테스트. TEST_MODE=true 가 아니면 자동 스킵.

실행:
    pytest                  # live/email 제외하고 빠르게 (기본값)
    pytest --run-live       # 네트워크 테스트도 포함
    TEST_MODE=true pytest --run-live -m email   # 이메일 발송 테스트까지 포함 (본인에게만 발송됨)
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def pytest_addoption(parser):
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="aicontents.team 등 실제 외부 서비스를 호출하는 테스트도 실행",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "live: 실제 aicontents.team 네트워크 호출이 필요한 테스트")
    config.addinivalue_line("markers", "email: 실제로 이메일을 발송할 수 있는 테스트")


def pytest_collection_modifyitems(config, items):
    run_live = config.getoption("--run-live")
    skip_no_live = pytest.mark.skip(reason="--run-live 옵션 없이는 실행 안 함 (네트워크/자격증명 필요)")
    skip_not_test_mode = pytest.mark.skip(
        reason="TEST_MODE=true 가 아니면 실제 이메일 발송 테스트는 스킵 (클라이언트 오발송 방지)"
    )

    for item in items:
        if "email" in item.keywords and os.getenv("TEST_MODE", "false").lower() != "true":
            item.add_marker(skip_not_test_mode)
            continue
        if ("live" in item.keywords or "email" in item.keywords) and not run_live:
            item.add_marker(skip_no_live)


@pytest.fixture(scope="session")
def aicontents_session():
    """실제 aicontents.team 로그인 세션 (live 테스트 전용)."""
    from monitor import site_login
    return site_login()
