"""
Gmail OAuth 인증 토큰 발급 스크립트
최초 1회만 실행하면 token.json이 생성됩니다.
"""

import os
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# Gmail 발송에 필요한 권한
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.path.join(BASE_DIR, "credentials.json")
TOKEN_FILE = os.path.join(BASE_DIR, "token.json")


def get_token():
    creds = None

    # 기존 토큰이 있으면 불러오기
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    # 토큰이 없거나 만료됐으면 새로 인증
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        # 토큰 저장
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    print("✅ 인증 완료! token.json이 생성됐습니다.")
    print(f"   위치: {TOKEN_FILE}")


if __name__ == "__main__":
    get_token()
