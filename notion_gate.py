"""
Notion 납품 일정 게이트
────────────────────────────────────────────────
'자막 납품 현황' DB에서 PLAVE 항목을 확인해
launcher.py의 실행 여부를 제어합니다.

is_upload_today()   : 업로드일 == 오늘 → plave_auto.py 실행 여부
is_delivery_today() : 총 작업 기간 안에 오늘 포함 → monitor.py 실행 여부
is_delivery_active(): 활성 상태 항목 존재 → monitor.py 계속 실행 여부
"""

import os
import requests
from datetime import date
from dotenv import load_dotenv

load_dotenv()

# ══════════════════════════════════════════════════════
#  설정 — .env에서 로드
# ══════════════════════════════════════════════════════

NOTION_TOKEN = os.getenv("NOTION_API_KEY", "")
DATABASE_ID  = os.getenv("NOTION_DB_ID", "31a4320ee27f803bb33fcf5e31937024")

ACTIVE_STATUSES = ["작업 전", "한국어 QC 중", "En&Ja QC 중", "En&Ja QC 완료"]

# 상태 매핑 (Notion '상태' status 속성의 실제 옵션명과 정확히 일치해야 함)
STATUS_MAPPING = {
    "작업 전": "한국어 QC 중",
    "한국어 QC 중": "En&Ja QC 중",
    "En&Ja QC 중": "En&Ja QC 완료",
    "En&Ja QC 완료": "납품 완료"
}


def _query_notion(payload: dict) -> list | None:
    """Notion DB 쿼리 공통 함수. 오류 시 None 반환."""
    if not NOTION_TOKEN:
        return None
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }
    try:
        r = requests.post(
            f"https://api.notion.com/v1/databases/{DATABASE_ID}/query",
            headers=headers,
            json=payload,
            timeout=10,
        )
        if r.status_code != 200:
            print(f"  ⚠️  Notion API 오류 ({r.status_code}) — 감시 계속 유지")
            return None
        return r.json().get("results", [])
    except Exception as e:
        print(f"  ⚠️  Notion 연결 실패 ({e}) — 감시 계속 유지")
        return None


def is_upload_today() -> bool:
    """
    PLAVE 항목의 '업로드일' == 오늘이면 True.
    → plave_auto.py 실행 여부 판단 (22시 실행)
    API 오류 시 False (fail-safe — 잘못 실행 방지).
    """
    if not NOTION_TOKEN:
        return False

    today = date.today().isoformat()
    payload = {
        "filter": {
            "and": [
                {"property": "콘텐츠 유형", "select": {"equals": "✳️PLAVE"}},
                {"property": "업로드일", "date": {"equals": today}},
            ]
        },
        "page_size": 1,
    }
    results = _query_notion(payload)
    if results is None:
        return False  # fail-safe
    return len(results) > 0


def is_delivery_today() -> bool:
    """
    오늘 날짜가 PLAVE 항목의 '총 작업 기간' 범위 안에 있으면 True.
    launcher.py에서 monitor.py 실행 여부 판단에 사용.
    NOTION_TOKEN 미설정 또는 API 오류 시 True (fail-open).

    주의: Notion API의 date 필터(on_or_before / on_or_after)는 range 속성일 때
    'start' 값만 비교 대상으로 삼는다 (end는 무시됨). 그래서 on_or_before/
    on_or_after를 동시에 걸어 "오늘이 기간에 포함되는지"를 판단하려 하면,
    기간이 어제 이전에 시작된 경우 on_or_after(today) 조건에서 start가 today보다
    이전이라는 이유로 걸러져 항상 False가 된다 (예: 총 작업 기간이 06-18~06-19이고
    오늘이 06-19여도 매치되지 않음). 따라서 콘텐츠 유형/상태만 API로 필터링하고,
    실제 범위 포함 여부는 start/end를 받아와 로컬에서 직접 계산한다.
    """
    if not NOTION_TOKEN:
        return True

    today = date.today().isoformat()
    payload = {
        "filter": {
            "and": [
                {"property": "콘텐츠 유형", "select": {"equals": "✳️PLAVE"}},
                {
                    "or": [
                        {"property": "상태", "status": {"equals": s}}
                        for s in ACTIVE_STATUSES
                    ]
                },
            ]
        },
        "page_size": 50,
    }
    results = _query_notion(payload)
    if results is None:
        return True  # fail-open

    for page in results:
        date_prop = page.get("properties", {}).get("총 작업 기간", {}).get("date")
        if not date_prop or not date_prop.get("start"):
            continue
        start = date_prop["start"][:10]
        end = (date_prop.get("end") or date_prop["start"])[:10]
        if start <= today <= end:
            return True
    return False


def is_delivery_active() -> bool:
    """
    활성 상태인 PLAVE 항목이 있으면 True.
    monitor.py 주기 체크에서 스킵 여부 판단에 사용.
    NOTION_TOKEN 미설정 또는 API 오류 시 True (fail-open).
    """
    if not NOTION_TOKEN:
        return True

    payload = {
        "filter": {
            "and": [
                {"property": "콘텐츠 유형", "select": {"equals": "✳️PLAVE"}},
                {
                    "or": [
                        {"property": "상태", "status": {"equals": s}}
                        for s in ACTIVE_STATUSES
                    ]
                },
            ]
        },
        "page_size": 1,
    }
    results = _query_notion(payload)
    if results is None:
        return True  # fail-open
    return len(results) > 0


def get_plave_page_by_date(target_date: str = None) -> dict | None:
    """
    특정 날짜 또는 오늘 날짜의 PLAVE 항목 페이지 정보를 가져옴.
    target_date가 None이면 오늘 날짜 사용.
    반환: {"page_id": str, "current_status": str} 또는 None
    """
    if not NOTION_TOKEN:
        return None
    
    if target_date is None:
        target_date = date.today().isoformat()
    
    payload = {
        "filter": {
            "and": [
                {"property": "콘텐츠 유형", "select": {"equals": "✳️PLAVE"}},
                {"property": "업로드일", "date": {"equals": target_date}},
            ]
        },
        "page_size": 1,
    }
    
    results = _query_notion(payload)
    if results is None or len(results) == 0:
        return None
    
    page = results[0]
    status_prop = page.get("properties", {}).get("상태", {})
    current_status = status_prop.get("status", {}).get("name", "")
    
    return {
        "page_id": page["id"],
        "current_status": current_status
    }


def update_status(page_id: str, new_status: str) -> bool:
    """
    Notion 페이지의 '상태' 속성을 업데이트.
    성공 시 True, 실패 시 False 반환.
    """
    if not NOTION_TOKEN:
        print("⚠️ NOTION_TOKEN이 설정되지 않음")
        return False
    
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28", 
        "Content-Type": "application/json",
    }
    
    payload = {
        "properties": {
            "상태": {
                "status": {
                    "name": new_status
                }
            }
        }
    }
    
    try:
        r = requests.patch(
            f"https://api.notion.com/v1/pages/{page_id}",
            headers=headers,
            json=payload,
            timeout=10,
        )
        
        if r.status_code == 200:
            print(f"✅ Notion 상태 업데이트 성공: {new_status}")
            return True
        else:
            print(f"⚠️ Notion 상태 업데이트 실패 ({r.status_code}): {r.text}")
            return False
            
    except Exception as e:
        print(f"⚠️ Notion 상태 업데이트 오류: {e}")
        return False


def update_status_by_workflow(workflow_step: str, target_date: str = None) -> bool:
    """
    워크플로우 단계에 따라 Notion DB 상태를 자동 업데이트.
    
    workflow_step 옵션:
    - "ko_draft_sent": ko_draft 파일이 작업자에게 발송됨 (작업 전 → 한국어 QC중)
    - "ko_qc_completed": ko_QC 파일 감지되어 클라이언트 발송 + 번역 트리거 (한국어 QC중 → En/Ja QC 중)  
    - "en_ja_qc_completed": en_QC, ja_QC, zh_QC 파일이 클라이언트 발송됨 (En/Ja QC 중 → En/Ja QC 완료)
    - "final_delivery": es, vi, ind, th 파일이 클라이언트 발송됨 (En/Ja QC 완료 → 납품 완료)
    
    성공 시 True, 실패 시 False 반환.
    """
    page_info = get_plave_page_by_date(target_date)
    if not page_info:
        print(f"⚠️ {target_date or 'today'} 날짜의 PLAVE 항목을 찾을 수 없음")
        return False
    
    current_status = page_info["current_status"]
    page_id = page_info["page_id"]
    
    # 워크플로우 단계별 상태 변경 매핑 (Notion '상태' status 속성의 실제 옵션명과 정확히 일치해야 함)
    workflow_mapping = {
        "ko_draft_sent": {"from": "작업 전", "to": "한국어 QC 중"},
        "ko_qc_completed": {"from": "한국어 QC 중", "to": "En&Ja QC 중"},
        "en_ja_qc_completed": {"from": "En&Ja QC 중", "to": "En&Ja QC 완료"},
        "final_delivery": {"from": "En&Ja QC 완료", "to": "납품 완료"}
    }
    
    if workflow_step not in workflow_mapping:
        print(f"⚠️ 알 수 없는 워크플로우 단계: {workflow_step}")
        return False
    
    expected_from = workflow_mapping[workflow_step]["from"]
    new_status = workflow_mapping[workflow_step]["to"]
    
    # 현재 상태가 예상 상태와 일치하지 않으면 경고만 출력하고 계속 진행
    if current_status != expected_from:
        print(f"⚠️ 예상 상태({expected_from})와 현재 상태({current_status})가 다름 - 그래도 {new_status}로 업데이트 시도")
    
    return update_status(page_id, new_status)


if __name__ == "__main__":
    today_active = is_delivery_today()
    status_active = is_delivery_active()
    print(f"오늘 날짜 납품일 여부: {'✅ 예' if today_active else '💤 아니오'}")
    print(f"활성 상태 항목 여부:   {'✅ 있음' if status_active else '💤 없음'}")
    if today_active:
        print("\n→ monitor.py 실행 필요")
    else:
        print("\n→ 오늘은 납품일 아님, monitor.py 불필요")