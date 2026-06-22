"""
로그인 디버그 스크립트 — 각 단계 상세 출력
"""
import re, requests
from urllib.parse import unquote

SITE_URL   = "https://www.aicontents.team"
LOGIN_PATH = "/v3/users/sign_in"
SITE_EMAIL = "skawn.kim@dost11.kr"
SITE_PASS  = "rlaskawn20!"

session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})

# ── Step 1: GET 로그인 페이지 ──────────────────────────
print("=== Step 1: GET", LOGIN_PATH)
r = session.get(f"{SITE_URL}{LOGIN_PATH}", allow_redirects=True)
print(f"  Status: {r.status_code}")
print(f"  Final URL: {r.url}")
print(f"  Cookies received: {dict(session.cookies)}")
print()

xsrf_raw = session.cookies.get("XSRF-TOKEN")
xsrf = unquote(xsrf_raw) if xsrf_raw else None
print(f"  XSRF-TOKEN (raw): {xsrf_raw}")
print(f"  XSRF-TOKEN (decoded): {xsrf}")
print()

# ── Step 2: POST 시도 1 — X-Inertia 없음 ─────────────
print("=== Step 2a: POST (X-Inertia 없음)")
r2a = session.post(
    f"{SITE_URL}{LOGIN_PATH}",
    headers={
        "X-XSRF-TOKEN": xsrf or "",
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Origin": SITE_URL,
        "Referer": f"{SITE_URL}{LOGIN_PATH}",
    },
    json={"user": {"email": SITE_EMAIL, "password": SITE_PASS}},
    allow_redirects=False,
)
print(f"  Status: {r2a.status_code}")
print(f"  Location: {r2a.headers.get('Location', 'none')}")
print(f"  Body: {r2a.text[:200]}")
print()

# ── Step 3: POST 시도 2 — X-Inertia 있음 ─────────────
print("=== Step 2b: POST (X-Inertia 있음)")
r2b = session.post(
    f"{SITE_URL}{LOGIN_PATH}",
    headers={
        "X-XSRF-TOKEN": xsrf or "",
        "X-Inertia": "true",
        "X-Inertia-Version": "1.0",
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Origin": SITE_URL,
        "Referer": f"{SITE_URL}{LOGIN_PATH}",
    },
    json={"user": {"email": SITE_EMAIL, "password": SITE_PASS}},
    allow_redirects=False,
)
print(f"  Status: {r2b.status_code}")
print(f"  Location: {r2b.headers.get('Location', 'none')}")
print(f"  Body: {r2b.text[:200]}")
print()

# ── Step 4: POST 시도 3 — 폼 데이터 방식 ─────────────
# CSRF from meta tag
csrf_m = re.search(r'name="csrf-token"\s+content="([^"]+)"', r.text)
csrf = csrf_m.group(1) if csrf_m else None
print(f"  meta csrf-token: {csrf}")
print()

print("=== Step 2c: POST (form data, authenticity_token)")
r2c = session.post(
    f"{SITE_URL}{LOGIN_PATH}",
    data={
        "authenticity_token": csrf or "",
        "user[email]": SITE_EMAIL,
        "user[password]": SITE_PASS,
    },
    headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "text/html,application/xhtml+xml,*/*",
        "Origin": SITE_URL,
        "Referer": f"{SITE_URL}{LOGIN_PATH}",
    },
    allow_redirects=False,
)
print(f"  Status: {r2c.status_code}")
print(f"  Location: {r2c.headers.get('Location', 'none')}")
print(f"  Body: {r2c.text[:200]}")
