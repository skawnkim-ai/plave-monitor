"""
로그인 + 비디오 목록 조회 테스트
실행: python test_login.py
"""
import re, requests, json

SITE_URL   = "https://www.aicontents.team"
LOGIN_PATH = "/v3/users/sign_in"
SITE_EMAIL = "skawn.kim@dost11.kr"
SITE_PASS  = "rlaskawn20!"

print("1) CSRF 토큰 획득 중...")
session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
r = session.get(f"{SITE_URL}{LOGIN_PATH}")
m = re.search(r'name="csrf-token"\s+content="([^"]+)"', r.text)
assert m, "❌ CSRF 토큰을 찾을 수 없습니다."
csrf = m.group(1)
print(f"   ✅ CSRF 획득 완료")

print("2) 로그인 중...")
r = session.post(
    f"{SITE_URL}{LOGIN_PATH}",
    headers={"X-CSRF-Token": csrf, "Content-Type": "application/json",
             "Accept": "application/json, text/plain, */*"},
    json={"user": {"email": SITE_EMAIL, "password": SITE_PASS}},
    allow_redirects=True,
)
print(f"   상태: {r.status_code}, URL: {r.url}")
if "sign_in" in r.url and r.status_code not in (200, 201):
    print(f"❌ 로그인 실패: {r.text[:300]}")
    exit(1)
print("   ✅ 로그인 성공")

print("3) 비디오 목록 조회 중...")
r = session.get(f"{SITE_URL}/videos",
    headers={"X-Inertia": "true", "X-Inertia-Version": "1.0",
             "Accept": "application/json, text/plain, */*"})
print(f"   상태: {r.status_code}")
videos = r.json()["props"]["videos"]
print(f"   ✅ 비디오 {len(videos)}개 확인")
for v in videos[:3]:
    print(f"      [{v['id']}] {v['title']}")
print("   ...")

print("\n✅ 테스트 완료 — monitor.py 실행 가능")
