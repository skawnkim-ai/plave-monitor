"""
메일 발송 테스트 스크립트
- 한국어 납품 메일 (ko_QC 첨부)
- 번역 파일 전달 메일 (en/ja/zh 첨부)

실행: python test_send.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitor import (
    site_login, get_video_files, find_qualifying_file, find_translation_files,
    find_qc_files, send_email, send_translation_email, send_qc_email,
    parse_date_from_filename, TEST_MODE
)

# 테스트할 비디오 ID
TEST_VIDEO_ID = 78
TEST_VIDEO    = {"id": TEST_VIDEO_ID, "title": "260608 PLAVE Yejun & Hamin Live"}

print(f"테스트 모드: {'✅ ON (본인에게 발송)' if TEST_MODE else '❌ OFF'}\n")

print("1) 사이트 로그인 중...")
session = site_login()

print(f"2) 비디오 [{TEST_VIDEO_ID}] 파일 목록 조회 중...")
files   = get_video_files(session, TEST_VIDEO_ID)
qfile   = find_qualifying_file(files)
tfiles  = find_translation_files(files)
qcfiles = find_qc_files(files)

if not qfile:
    print("❌ ko_QC 파일을 찾을 수 없습니다.")
    sys.exit(1)

date_str = parse_date_from_filename(qfile["filename"])
print(f"   ko_QC 파일: {qfile['filename']}  ({date_str})")
print(f"   번역 파일:  {list(tfiles.keys())}")
print(f"   QC 파일:    {list(qcfiles.keys())}\n")

print("어떤 메일을 테스트할까요?")
print("  1) 한국어 납품 메일 (ko_QC 첨부)")
print("  2) 번역 파일 전달 메일 — Quokka Labs (en/ja/zh 첨부)")
print("  3) EN/JA/ZH QC 납품 메일 — 클라이언트 (en_QC/ja_QC/zh_QC 첨부)")
print("  4) 전체 (1+2+3)")
choice = input("선택 (1/2/3/4): ").strip()

if choice in ("1", "4"):
    confirm = input(f"\n한국어 납품 메일을 {('본인' if TEST_MODE else '실제 클라이언트')}에게 발송할까요? (y/n): ").strip().lower()
    if confirm == "y":
        send_email(session, TEST_VIDEO, qfile)
        print("✅ 한국어 납품 메일 발송 완료\n")

if choice in ("2", "4"):
    if len(tfiles) < 3:
        print(f"❌ 번역 파일이 {len(tfiles)}/3개만 준비됨 — 번역 완료 후 재시도")
    else:
        confirm = input(f"\n번역 파일 전달 메일을 {('본인' if TEST_MODE else 'Quokka Labs')}에게 발송할까요? (y/n): ").strip().lower()
        if confirm == "y":
            send_translation_email(session, TEST_VIDEO, tfiles, qfile)
            print("✅ 번역 파일 전달 메일 발송 완료\n")

if choice in ("3", "4"):
    if len(qcfiles) < 3:
        print(f"❌ QC 파일이 {len(qcfiles)}/3개만 준비됨 — QC 완료 후 재시도")
    else:
        confirm = input(f"\nQC 납품 메일을 {('본인' if TEST_MODE else '실제 클라이언트')}에게 발송할까요? (y/n): ").strip().lower()
        if confirm == "y":
            send_qc_email(session, TEST_VIDEO, qcfiles)
            print("✅ QC 납품 메일 발송 완료\n")
