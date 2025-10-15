from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta
from openai import OpenAI
import os, json, re, logging, sys, threading

app = FastAPI(title="Dooray Meeting Bot (file-local)")

# ---------- Logging ----------
for h in logging.root.handlers[:]:
    logging.root.removeHandler(h)
logging.basicConfig(
    level="INFO",
    handlers=[logging.StreamHandler(sys.stdout)],
    format="%(levelname)s %(asctime)s %(name)s : %(message)s",
)
log = logging.getLogger("meeting-bot")

def resp(payload: Dict[str, Any]) -> JSONResponse:
    try:
        log.info("[RESP] %s", json.dumps(payload, ensure_ascii=False)[:1500])
    except Exception:
        pass
    return JSONResponse(content=payload, media_type="application/json; charset=utf-8")

# ---------- OpenAI (optional) ----------
client = None
if os.getenv("OPENAI_API_KEY"):
    try:
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    except Exception:
        client = None

# ---------- Paths / DB (same dir as this file) ----------
CURRENT_DIR = os.path.dirname(__file__)
ROOMS_PATH = os.path.join(CURRENT_DIR, "rooms.json")
RESV_PATH  = os.path.join(CURRENT_DIR, "reservations.json")

# _db_lock = threading.Lock()

def load_rooms() -> List[Dict[str,Any]]:
    return [
  { "id": "R301", "name": "3층 대회의실", "floor": 3, "capacity": 12 },
  { "id": "R302", "name": "3층 소회의실 A", "floor": 3, "capacity": 6 },
  { "id": "R303", "name": "3층 소회의실 B", "floor": 3, "capacity": 6 },
  { "id": "R401", "name": "4층 라운지룸", "floor": 4, "capacity": 8 },
  { "id": "R402", "name": "4층 세미나룸", "floor": 4, "capacity": 20 }
    ]


def load_reservations() -> List[Dict[str,Any]]:
    if not os.path.exists(RESV_PATH):
        return []
    with open(RESV_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

# def save_reservations(resv: List[Dict[str,Any]]) -> Optional[str]:
#     """성공 시 None, 실패 시 에러 메시지 문자열 반환"""
#     try:
#         with _db_lock:
#             with open(RESV_PATH, "w", encoding="utf-8") as f:
#                 json.dump(resv, f, ensure_ascii=False, indent=2)
#         return None
#     except Exception as e:
#         log.warning("save_reservations failed: %s", e)
#         return f"⚠️ 예약 저장에 실패했습니다: {e}"

# ---------- Dooray helpers ----------
def parse_payload(req: Request, data: Dict[str,Any]) -> Dict[str,Any]:
    if not data.get("actionValue") and data.get("actions"):
        a0 = data["actions"][0]
        data["actionValue"] = a0.get("value")
        data["actionName"]  = a0.get("name") or data.get("actionName")
    return data

def msg(text: str, attachments=None, response_type="ephemeral",
        replace_original=False, delete_original=False) -> Dict[str,Any]:
    p = {"text": text, "responseType":response_type,
         "replaceOriginal": replace_original, "deleteOriginal": delete_original}
    if attachments: p["attachments"]=attachments
    return p

def mention(tenant_id: str, user_id: str, label="member") -> str:
    return f'(dooray://{tenant_id}/members/{user_id} "{label}")'

# ---------- NLP ----------
TIME_RANGE_RE = re.compile(
    r'(?P<s_h>\d{1,2})(?::?(?P<s_m>\d{2}))?\s*(?:~|부터)\s*(?P<e_h>\d{1,2})(?::?(?P<e_m>\d{2}))?\s*?(?:까지)?'
)
FLOOR_RE = re.compile(r'(?P<floor>\d{1,2})\s*층')
ROOM_NAME_TOKEN_RE = re.compile(r'(회의실|룸|방)')

def parse_natural(text: str) -> Dict[str,Any]:
    text = (text or "").strip()
    out: Dict[str,Any] = {"floor": None, "room_hint": None, "start": None, "end": None, "title": None}

    # 시간
    m = TIME_RANGE_RE.search(text.replace(" ", ""))
    if m:
        s_h = int(m.group("s_h")); s_m = int(m.group("s_m") or 0)
        e_h = int(m.group("e_h")); e_m = int(m.group("e_m") or 0)
        today = datetime.now().date()
        start = datetime(today.year, today.month, today.day, s_h, s_m)
        end   = datetime(today.year, today.month, today.day, e_h, e_m)
        if end <= start: end += timedelta(hours=1)
        out["start"] = start.strftime("%H:%M")
        out["end"]   = end.strftime("%H:%M")

    # 층
    fm = FLOOR_RE.search(text)
    if fm:
        out["floor"] = int(fm.group("floor"))


    # 방 이름 힌트
    if ROOM_NAME_TOKEN_RE.search(text):
        out["room_hint"] = text

    out["title"] = text

    # OpenAI 보정(있을 때만)
    if client:
        try:
            prompt = f"""
다음 한국어 문장에서 회의실 예약 의도를 추출해 JSON으로 줘.
- 키: floor(정수 또는 null), room_name(문자열 또는 null), start("HH:MM" 또는 null), end("HH:MM" 또는 null), title(문자열)
문장: "{text}"
JSON만 반환.(코드블록으로 주지말고.)
"""
            r = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role":"user","content":prompt}],
                temperature=0.2,
            )
            j = json.loads(r.choices[0].message.content.strip())
            out["floor"] = out["floor"] or j.get("floor")
            out["start"] = out["start"] or j.get("start")
            out["end"]   = out["end"] or j.get("end")
            if j.get("room_name"): out["room_hint"] = j["room_name"]
            out["title"] = out["title"] or j.get("title")
            log.info(out)
        except Exception as e:
            log.warning("OpenAI refine skipped: %s", e)

    return out

# ---------- Availability ----------
def overlaps(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
    return not (a_end <= b_start or b_end <= a_start)

def room_busy(room_id: str, start: str, end: str) -> bool:
    resv = load_reservations()
    today = datetime.now().strftime("%Y-%m-%d")
    for r in resv:
        if r["roomId"] == room_id and r.get("date") == today:
            if overlaps(start, end, r["start"], r["end"]):
                return True
    return False

def room_options(floor: Optional[int]=None, hint: Optional[str]=None) -> List[Dict[str,str]]:
    rooms = load_rooms()
    if floor:
        rooms = [r for r in rooms if r.get("floor")==floor]
    if hint:
        kw = str(hint)
        rooms = [r for r in rooms if any(t in kw for t in [r["id"], r["name"]])]
    rooms.sort(key=lambda x: (x.get("floor",0), x["name"]))
    return [{"text": f'{r["name"]} ({r["id"]})', "value": r["id"]} for r in rooms]

def time_options(pref: Optional[str]=None) -> List[Dict[str,str]]:
    slots = []
    t = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0)
    end = datetime.now().replace(hour=20, minute=0, second=0, microsecond=0)
    while t <= end:
        s = t.strftime("%H:%M")
        slots.append(s)
        t += timedelta(minutes=30)
    if pref and pref in slots:
        slots.remove(pref); slots.insert(0, pref)
    return [{"text": s, "value": s} for s in slots]

# ---------- UI ----------
def build_template_ui(nlu: Dict[str,Any]) -> Dict[str,Any]:
    try:
        room_opts = room_options(floor=nlu.get("floor"), hint=nlu.get("room_hint"))
    except FileNotFoundError:
        return msg("⚠️ rooms.json 파일이 api/index.py와 같은 폴더에 필요합니다.", response_type="ephemeral")

    start_opts = time_options(pref=nlu.get("start"))
    end_opts   = time_options(pref=nlu.get("end"))

    info = []
    if nlu.get("floor"): info.append(f"• 층 필터: {nlu['floor']}층")
    if nlu.get("start") and nlu.get("end"): info.append(f'• 시간 후보: {nlu["start"]} ~ {nlu["end"]}')
    if nlu.get("room_hint"): info.append(f'• 방 힌트: {nlu["room_hint"]}')
    info_text = "\n".join(info) if info else "원하는 값을 선택하고 제출을 눌러주세요."

    return msg(
        text="🗓️ 회의실 예약",
        response_type="inChannel",
        replace_original=False,
        attachments=[
            {"title":"회의실 선택","actions":[
                {"name":"room","type":"select","text":"회의실","options": room_opts or [{"text":"(회의실 없음)","value":"__none__"}]}
            ]},
            {"title":"시간 선택","text": info_text, "actions":[
                {"name":"start","type":"select","text":"시작","options": start_opts},
                {"name":"end","type":"select","text":"종료","options": end_opts},
            ]},
            {"callbackId":"meeting-submit","actions":[
                {"name":"submit","type":"button","text":"제출","value":"submit","style":"primary"}
            ]},
            {"title":"예약 현황","fields":[{"title":"아직 없음","value":"제출 시 여기에 표시됩니다.","short":False}]}
        ]
    )

def parse_status(original: Dict[str,Any]) -> Dict[str,List[str]]:
    out: Dict[str,List[str]] = {}
    for att in (original.get("attachments") or []):
        if att.get("title")=="예약 현황":
            for f in (att.get("fields") or []):
                k = f.get("title") or ""
                v = (f.get("value") or "").strip()
                if k: out[k] = [x for x in v.split(" ") if x]
    return out

def status_fields(status: Dict[str,List[str]]) -> List[Dict[str,Any]]:
    if not status:
        return [{"title":"아직 없음","value":"제출 시 여기에 표시됩니다.","short":False}]
    return [{"title":k, "value":" ".join(v) if v else "-", "short":False} for k,v in status.items()]

# ---------- ephemeral state ----------
_state = {}
_state_lock = threading.Lock()
def set_state(chlog: str, uid: str, **kw):
    with _state_lock:
        st = _state.get((chlog, uid), {"_ts": datetime.now().timestamp()})
        st.update(kw)
        st["_ts"] = datetime.now().timestamp()
        _state[(chlog, uid)] = st

def get_state(chlog: str, uid: str) -> Dict[str,Any]:
    with _state_lock:
        return _state.get((chlog, uid), {})

# ---------- COMMAND ----------
@app.post("/dooray/meeting/command")
async def meeting_command(req: Request):
    raw = (await req.body()).decode("utf-8","ignore")
    log.info("[IN] /meeting/command RAW=%s", raw[:1200])

    try:
        data = await req.json()
    except Exception:
        form = await req.form()
        data = json.loads(form["payload"]) if "payload" in form else {k:v for k,v in form.items()}
    data = parse_payload(req, data)

    text = (data.get("text") or "").strip()
    if not text:
        return resp(build_template_ui({"floor":None,"start":None,"end":None,"room_hint":None}))
    nlu = parse_natural(text)
    return resp(build_template_ui(nlu))

# ---------- ACTIONS ----------
@app.post("/dooray/meeting/actions")
async def meeting_actions(req: Request):
    raw = (await req.body()).decode("utf-8","ignore")
    log.info("[IN] /meeting/actions RAW=%s", raw[:1200])

    try:
        data = await req.json()
    except Exception:
        form = await req.form()
        data = json.loads(form["payload"]) if "payload" in form else {}
    data = parse_payload(req, data)

    action_name  = data.get("actionName") or ""
    action_value = (data.get("actionValue") or "").strip()
    original     = data.get("originalMessage") or {}
    tenant_id    = (data.get("tenant") or {}).get("id","tenant")
    user         = data.get("user") or {}
    user_id      = user.get("id","user")
    chlog_id     = str(data.get("channelLogId") or original.get("id") or "")

    # 드롭다운 변경 → 상태만 저장
    if action_name in ("room","start","end"):
        set_state(chlog_id, user_id, **{action_name: action_value})
        return resp({})

    # 제출
    if action_value == "submit":
        st = get_state(chlog_id, user_id)
        room_id = st.get("room")
        start   = st.get("start")
        end     = st.get("end")

        if not room_id or not start or not end:
            return resp(msg("회의실/시작/종료를 모두 선택해 주세요.", response_type="ephemeral"))

        if room_busy(room_id, start, end):
            return resp(msg("⚠️ 선택한 시간에 해당 회의실은 이미 예약되어 있어요. 시간을 바꾸거나 다른 회의실을 선택해 주세요.",
                            response_type="ephemeral"))

        rooms = {r["id"]: r for r in load_rooms()}
        title = f'{rooms.get(room_id,{}).get("name",room_id)} 예약'
        today = datetime.now().strftime("%Y-%m-%d")
        new_rec = {
            "id": f"RV-{int(datetime.now().timestamp())}-{user_id}",
            "date": today,
            "roomId": room_id,
            "start": start,
            "end": end,
            "title": title,
            "reservedBy": user_id
        }
        # resv = load_reservations()
        # resv.append(new_rec)
        # err = save_reservations(resv)
        # if err:
        #     return resp(msg(err, response_type="ephemeral"))

        status = parse_status(original) or {}
        key = f'{rooms.get(room_id,{}).get("name",room_id)} {start}~{end}'
        tag = mention(tenant_id, user_id, "member")
        status.setdefault(key, [])
        status[key] = [u for u in status[key] if u != tag] + [tag]

        new_atts = []
        replaced = False
        for att in (original.get("attachments") or []):
            if att.get("title") == "예약 현황":
                new_atts.append({"title":"예약 현황","fields": status_fields(status)})
                replaced = True
            else:
                new_atts.append(att)
        if not replaced:
            new_atts.append({"title":"예약 현황","fields": status_fields(status)})

        return resp({
            "text": original.get("text") or "🗓️ 회의실 예약",
            "attachments": new_atts,
            "responseType": "inChannel",
            "replaceOriginal": True
        })

    return resp({})
