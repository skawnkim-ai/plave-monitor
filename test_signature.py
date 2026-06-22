"""서명 테스트 — check_once 1회 실행 후 종료"""
import monitor

print(f"=== TEST_MODE: {monitor.TEST_MODE} ===")
print(f"발송 대상: {monitor.TEST_MAIL_TO}")
print()

log     = monitor.load_sent_log()
session = monitor.site_login()
session, log = monitor.check_once(session, log)

print()
print("=== 테스트 완료 ===")
