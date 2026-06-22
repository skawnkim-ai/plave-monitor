"""aicontents.team 특정 video 삭제 스크립트."""
import sys
import requests
from urllib.parse import unquote
from dotenv import load_dotenv
import os

load_dotenv()

SITE_URL = "https://www.aicontents.team"
EMAIL    = os.getenv("AICONTENTS_EMAIL")
PASSWORD = os.getenv("AICONTENTS_PASSWORD")

def login():
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    session.get(f"{SITE_URL}/v3/users/sign_in")
    xsrf = unquote(session.cookies.get("XSRF-TOKEN", ""))

    session.post(
        f"{SITE_URL}/v3/users/sign_in",
        headers={"X-XSRF-TOKEN": xsrf, "Content-Type": "application/json", "Accept": "application/json"},
        json={},
    )
    xsrf = unquote(session.cookies.get("XSRF-TOKEN", xsrf))

    r = session.post(
        f"{SITE_URL}/v3/users/sign_in",
        headers={"X-XSRF-TOKEN": xsrf, "Content-Type": "application/json", "Accept": "application/json"},
        json={"user": {"email": EMAIL, "password": PASSWORD}},
    )
    r.raise_for_status()
    print(f"로그인 완료 ({r.status_code})")
    return session, unquote(session.cookies.get("XSRF-TOKEN", xsrf))

def delete_video(video_id: int):
    session, xsrf = login()
    r = session.delete(
        f"{SITE_URL}/videos/{video_id}",
        headers={
            "X-XSRF-TOKEN": xsrf,
            "X-Inertia": "true",
            "X-Inertia-Version": "1.0",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
        },
    )
    if r.status_code in (200, 204, 302):
        print(f"✅ video {video_id} 삭제 완료")
    else:
        print(f"❌ 삭제 실패: {r.status_code}\n{r.text[:300]}")

if __name__ == "__main__":
    vid = int(sys.argv[1]) if len(sys.argv) > 1 else 79
    delete_video(vid)
