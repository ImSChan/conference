# api/index.py
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="Coffee Poll – one-click buttons")

# ---------- 메뉴 ----------
MENU_SECTIONS = {
    "추천메뉴": [
        "더치커피","아메리카노","카페라떼","유자민트 릴렉서 티","ICE 케모리치 릴렉서 티"
    ],
    "스무디": [
        "딸기주스","바나나주스","레몬요거트 스무디","블루베리요거트 스무디","딸기 요거트 스무니","딸기 바나나 스무디"
    ],
    "커피": [
        "에스프레소","아메리카노","카페라떼","카푸치노","바닐라라떼","돌체라떼","시나몬라떼",
        "헤이즐넛라떼","카라멜마키야토","카페모카","피치프레소","더치커피"
    ],
    "음료": [
        "그린티 라떼","오곡라떼","고구마라떼","로얄밀크티라떼","초콜릿라떼","리얼자몽티","리얼레몬티","진저레몬티",
        "매실차","오미자차","자몽에이드","레몬에이드","진저레몬에이드","스팀우유","사과유자차","페퍼민트",
        "얼그레이","캐모마일","유자민트릴렉서티","ICE 케모리치 릴렉서티","배도라지모과차","헛개차",
        "복숭아 아이스티","딸기라떼"
    ],
    "병음료": [
        "분다버그 진저","분다버그 레몬에이드","분다버그 망고","분다버그 자몽"
    ],
}

# ---------- 스타일 ----------
SECTION_STYLE = {
    "추천메뉴": {"emoji": "✨", "color": "#7C3AED"},
    "스무디":   {"emoji": "🍓", "color": "#06B6D4"},
    "커피":     {"emoji": "☕", "color": "#F59E0B"},
    "음료":     {"emoji": "🥤", "color": "#10B981"},
    "병음료":   {"emoji": "🧃", "color": "#EF4444"},
}

# ---------- 유틸 ----------
def pack(payload: dict) -> JSONResponse:
    return JSONResponse(content=payload, media_type="application/json; charset=utf-8")

def mention_member(tenant_id: str, user_id: str, label: str = "member") -> str:
    # Dooray 멤버 태깅 링크 (현황 value에는 그대로 문자열로 넣으면 Dooray가 렌더링함)
    return f'(dooray://{tenant_id}/members/{user_id} "{label}")'
# 1) 현황 파싱: 줄바꿈 기준
def parse_status(original: dict) -> dict:
    """원본 메시지의 '선택 현황'을 dict로 파싱: { '메뉴 (TEMP)': [tag, ...] }"""
    result = {}
    for att in (original.get("attachments") or []):
        if att.get("title") == "선택 현황":
            for f in (att.get("fields") or []):
                k = (f.get("title") or "").strip()
                vraw = (f.get("value") or "").strip()
                if not k:
                    continue  # 빈 타이틀은 무시
                vals = [line for line in vraw.split("\n") if line.strip()]
                result[k] = vals
    return result

# 2) 현황 표시: 줄바꿈으로 join
def status_fields(status: dict):
    if not status:
        return [{"title": "아직 투표 없음", "value": "첫 투표를 기다리는 중!", "short": False}]
    return [{"title": k, "value": "\n".join(v) if v else "-", "short": False}
            for k, v in status.items()]

# 3) placeholder 제거
def status_attachment(fields=None):
    return {
        "title": "선택 현황",
        "fields": fields or [{"title": "아직 투표 없음", "value": "첫 투표를 기다리는 중!", "short": False}]
    }


# ---------- UI 빌더 (버튼) ----------
def section_block_buttons(section: str) -> list[dict]:
    """
    섹션 헤더 + 메뉴별 (ICE)/(HOT) 버튼 한 묶음 생성.
    버튼 value 형식: vote|{section}|{menu}|{temp}
    """
    s = SECTION_STYLE.get(section, {"emoji": "•", "color": "#4757C4"})
    blocks = []
    # 헤더
    blocks.append({
        "callbackId": "coffee-poll",
        "title": f"{s['emoji']}  {section}",
        "color": s["color"],
    })
    # 모든 메뉴 버튼(ICE/HOT) 한 블록에 나열
    actions = []
    for m in MENU_SECTIONS[section]:
        actions.append({
            "name": f"vote::{section}",
            "type": "button",
            "text": f"{m} (ICE)",
            "value": f"vote|{section}|{m}|ICE",
        })
        actions.append({
            "name": f"vote::{section}",
            "type": "button",
            "text": f"{m} (HOT)",
            "value": f"vote|{section}|{m}|HOT",
        })
    blocks.append({
        "callbackId": "coffee-poll",
        "actions": actions,
        "color": s["color"],
    })
    return blocks

# ---------- 커맨드 ----------
@app.post("/dooray/command")
async def coffee_command(req: Request):
    data = await req.json()
    text = (data.get("text") or "").strip()

    if text == "":
        return pack({
            "responseType": "ephemeral",
            "text": "☕ 커피 투표: 매장을 선택하세요",
            "attachments": [
                {"callbackId":"coffee-start","actions":[
                    {"name":"start","type":"button","text":"에뜨리에 시작","value":"start|에뜨리에","style":"primary"},
                    {"name":"start","type":"button","text":"에뜰 (미지원)","value":"start|에뜰"}
                ]}
            ]
        })

    if text == "에뜰":
        return pack({"responseType":"ephemeral","text":"🚫 아직 '에뜰'은 지원하지 않아요. '에뜨리에'로 시도해 주세요."})

    # 기본: 에뜨리에
    atts = []
    for s in ["추천메뉴","스무디","커피","음료","병음료"]:
        atts.extend(section_block_buttons(s))
    atts.append(status_attachment())  # 선택 현황

    return pack({
        "responseType": "inChannel",
        "replaceOriginal": False,
        "text": "☕ 커피 투표를 시작합니다!",
        "attachments": atts
    })
# ---------- 인터랙션 ----------
@app.post("/dooray/actions")
async def coffee_actions(req: Request):
    data = await req.json()
    action_value = (data.get("actionValue") or "").strip()
    original     = data.get("originalMessage") or {}
    user         = data.get("user") or {}
    user_id      = user.get("id", "user")
    tenant_id    = (data.get("tenant") or {}).get("id", "tenant")

    # vote|섹션|메뉴|TEMP
    if action_value.startswith("vote|"):
        parts = action_value.split("|", 4)
        if len(parts) != 4:
            return pack({})  # 포맷 오류 시 무시
        _, _section, menu, temp = parts

        key = f"{menu} ({temp})"
        
        status = parse_status(original) or {}

        
        # 내 이전 표 전부 제거(전역 1표)
        tag = mention_member(tenant_id, user_id, label="member")
        for k in list(status.keys()):
            voters = [u for u in (status.get(k) or []) if u != tag]
            if voters:
                status[k] = voters
            else:
                del status[k]

        # 새 표 추가
        key = f"{menu} ({temp})"
        status.setdefault(key, [])
        if tag not in status[key]:
            status[key].append(tag)

        # 현황만 교체 (helper 사용)
        fields = status_fields(status)
        new_atts, replaced = [], False
        for att in (original.get("attachments") or []):
            if att.get("title") == "선택 현황":
                new_atts.append(status_attachment(fields))
                replaced = True
            else:
                new_atts.append(att)
        if not replaced:
            new_atts.append(status_attachment(fields))

        return pack({
            "text": original.get("text") or "☕ 커피 투표",
            "attachments": new_atts,
            "responseType": "inChannel",
            "replaceOriginal": True
        })

    # 그 외는 무시
    return pack({})
