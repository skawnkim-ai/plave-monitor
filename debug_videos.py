"""
aicontents.team 비디오 목록 필드 확인용
실행: python debug_videos.py
"""
import json
import sys
sys.path.insert(0, ".")
from plave_auto import site_login, get_video_list

session = site_login()
videos  = get_video_list(session)

print(f"총 {len(videos)}개 영상\n")
print("첫 번째 영상 전체 필드:")
print(json.dumps(videos[0], ensure_ascii=False, indent=2))
