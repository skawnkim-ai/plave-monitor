r"""
자막 납품 자동화 런처
────────────────────────────────────────────────
Windows 작업 스케줄러가 매일 아침 이 스크립트를 실행합니다.
Notion '자막 납품 현황' DB를 확인해 두 가지를 처리합니다.

  1. 업로드일 == 오늘  → 22:00에 plave_auto.py 자동 실행
     ※ delivery_today가 아닐 경우 Hibernate 전략 사용:
        - 21:50 wake task 등록 (작업 스케줄러가 Hibernate에서 깨움)
        - 22:00 plave_auto.py task 등록 (--hibernate 플래그)
        - 런처는 tasks 등록 후 바로 종료 (Hibernate는 사용자가 퇴근 시 직접 진입)
        - plave_auto.py 완료 후 Hibernate
  2. 총 작업 기간 안에 오늘 포함  → 13:00에 monitor.py 자동 실행

작업 스케줄러 설정:
  - 트리거: 매일 오전 09:00
  - 프로그램: C:\Users\User2\AppData\Local\Programs\Python\Python313\python.exe
  - 인수:     "C:\Users\User2\Documents\Claude\Projects\plave_monitor\launcher.py"
  - 시작 위치: C:\Users\User2\Documents\Claude\Projects\plave_monitor

사전 준비 (1회):
  - 관리자 권한 PowerShell: powercfg /hibernate on
"""

import os
import subprocess
import sys
import time
from datetime import datetime, date

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
PYTHON_EXE = sys.executable
MONITOR_PY = os.path.join(BASE_DIR, "monitor.py")
AUTO_PY    = os.path.join(BASE_DIR, "plave_auto.py")

sys.path.insert(0, BASE_DIR)
from logger import get_logger
from notion_gate import is_upload_today, is_delivery_today

logger = get_logger(__name__)

MONITOR_HOUR = 13   # monitor.py 시작 시각
AUTO_HOUR    = 22   # plave_auto.py 시작 시각


# ══════════════════════════════════════════════════════
#  유틸
# ══════════════════════════════════════════════════════

def seconds_until(hour: int) -> float:
    """오늘 `hour`시까지 남은 초. 이미 지났으면 0 반환."""
    now    = datetime.now()
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    return max((target - now).total_seconds(), 0)


def is_process_running(script_name: str) -> bool:
    """해당 스크립트가 이미 실행 중인지 확인."""
    try:
        result = subprocess.run(
            ["wmic", "process", "where", 'name="python.exe"', "get", "CommandLine"],
            capture_output=True, text=True,
        )
        return script_name in result.stdout
    except Exception:
        return False


def launch(script_path: str, label: str):
    """스크립트를 새 콘솔에서 실행."""
    name = os.path.basename(script_path)
    if is_process_running(name):
        logger.info(f"{label} 이미 실행 중 — 중복 실행 생략")
        return
    subprocess.Popen(
        [PYTHON_EXE, script_path],
        creationflags=subprocess.CREATE_NEW_CONSOLE,
        cwd=os.path.dirname(os.path.abspath(script_path)),
    )
    logger.info(f"{label} 실행 완료 → {script_path}")


def wait_and_launch(hour: int, script_path: str, label: str):
    """지정 시각까지 대기 후 실행. 이미 지났으면 즉시 실행."""
    secs = seconds_until(hour)
    if secs > 0:
        hms = time.strftime("%H:%M:%S", time.gmtime(secs))
        logger.info(f"{label} 대기 중 → {hour:02d}:00 실행 예정 (남은 시간: {hms})")
        time.sleep(secs)
    launch(script_path, label)


def _ps_run(script: str, label: str) -> bool:
    """PowerShell 스크립트 실행 후 성공 여부 반환."""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        logger.info(f"✅ {label} 완료")
        return True
    else:
        logger.error(f"❌ {label} 실패:\n{result.stderr.strip()}")
        return False


def cleanup_stale_tasks():
    """
    이전 launcher.py 실행에서 등록된 임시 tasks가 남아있으면 정리.
    launcher.py가 하루에 여러 번 실행될 수 있고(예: delivery_today가 중간에
    False→True로 바뀌는 경우), 그 사이 분기가 바뀌면 이전에 schedule_tonight()이
    등록한 task가 정리되지 않고 남아 스레드 실행과 동시에 중복 발동하는 문제가
    있었다. 매 실행 시작 시 무조건 한 번 정리한다(없으면 조용히 무시).
    """
    for task_name in ["plave_wake_tonight", "plave_auto_tonight"]:
        subprocess.run(
            ["schtasks", "/delete", "/tn", task_name, "/f"],
            capture_output=True,
        )


def schedule_tonight():
    """
    오늘 밤 실행 tasks 등록 후 런처 종료.
    Hibernate는 사용자가 퇴근 시 직접 진입.

    등록 tasks:
      - plave_wake_tonight  : 21:50, WakeToRun=True (Hibernate에서 깨움)
      - plave_auto_tonight  : 22:00, plave_auto.py --hibernate 실행
    """
    logger.info("── 오늘 밤 tasks 등록 ──")

    # 21:50 wake task (WakeToRun=True)
    ps_wake = (
        "$s = New-ScheduledTaskSettingsSet -WakeToRun; "
        "$t = New-ScheduledTaskTrigger -Once -At '21:50'; "
        "$a = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument '/c echo plave_wake'; "
        "Register-ScheduledTask -TaskName 'plave_wake_tonight' "
        "-Trigger $t -Settings $s -Action $a -Force | Out-Null"
    )
    _ps_run(ps_wake, "21:50 wake task 등록")

    # 22:00 plave_auto.py task
    ps_auto = (
        f"$exe = '{PYTHON_EXE}'; "
        f"$arg = '{AUTO_PY} --hibernate'; "
        f"$dir = '{BASE_DIR}'; "
        "$s = New-ScheduledTaskSettingsSet; "
        "$t = New-ScheduledTaskTrigger -Once -At '22:00'; "
        "$a = New-ScheduledTaskAction -Execute $exe -Argument $arg -WorkingDirectory $dir; "
        "Register-ScheduledTask -TaskName 'plave_auto_tonight' "
        "-Trigger $t -Settings $s -Action $a -Force | Out-Null"
    )
    _ps_run(ps_auto, "22:00 plave_auto.py task 등록")

    logger.info("✅ tasks 등록 완료 — 퇴근 시 Hibernate(최대 절전)로 전환해 주세요.")


# ══════════════════════════════════════════════════════
#  메인
# ══════════════════════════════════════════════════════

def main():
    logger.info("=" * 50)
    logger.info(f"자막 납품 런처 시작 — {date.today().isoformat()}")
    logger.info("=" * 50)

    cleanup_stale_tasks()

    upload_today   = is_upload_today()
    delivery_today = is_delivery_today()

    logger.info(f"업로드일 == 오늘:     {'✅' if upload_today   else '💤'}")
    logger.info(f"총 작업 기간 == 오늘: {'✅' if delivery_today else '💤'}")

    if not upload_today and not delivery_today:
        logger.info("오늘 할 일 없음 — 런처 종료")
        return

    import threading
    threads = []

    if delivery_today:
        threads.append(threading.Thread(
            target=wait_and_launch,
            args=(MONITOR_HOUR, MONITOR_PY, "monitor.py"),
            daemon=False,
        ))

    if upload_today:
        if delivery_today:
            # monitor.py가 실행 중이므로 컴퓨터가 켜져 있음 → 기존 방식으로 22시 대기
            threads.append(threading.Thread(
                target=wait_and_launch,
                args=(AUTO_HOUR, AUTO_PY, "plave_auto.py"),
                daemon=False,
            ))
        else:
            # 오늘 납품 모니터링 없음 → 오늘 밤 tasks만 등록하고 런처 종료
            # (Hibernate는 사용자가 퇴근 시 직접 진입)
            schedule_tonight()
            return

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    logger.info("런처 종료")


if __name__ == "__main__":
    main()
