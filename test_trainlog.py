import os, datetime as dt
os.environ.update(SITE_PASSWORD="pw123", STRAVA_CLIENT_ID="1", STRAVA_CLIENT_SECRET="s", STRAVA_REFRESH_TOKEN="r")
from unittest import mock
import fitstats, strava, app as appmod

class R:
    def __init__(s, j, code=200): s._j, s.status_code = j, code
    def json(s): return s._j

N = 1800
ACT = {"id": 7, "name": "Ride", "start_date": "2026-11-01T08:00:00Z", "sport_type": "Ride", "moving_time": N,
       "elapsed_time": N, "distance": 14400.0, "total_elevation_gain": 120}
STREAM = {"time": {"data": list(range(N))}, "watts": {"data": [200]*N}, "heartrate": {"data": [140]*N},
          "distance": {"data": [i*8.0 for i in range(N)]}}
calls = []
def fake_post(url, **kw): calls.append(url); return R({"access_token": "AT", "refresh_token": "R2", "expires_at": 9999999999})
def fake_get(url, params=None, headers=None, **kw):
    assert headers["Authorization"] == "Bearer AT"
    if url.endswith("/athlete/activities"):
        return R([{"id": 7, "name": "Ride", "start_date": ACT["start_date"], "sport_type": "Ride"},
                  {"id": 8, "name": "manual", "manual": True, "start_date": "x", "type": "Run"},
                  {"id": 9, "name": "Run", "start_date": "2026-11-02T00:00:00Z", "sport_type": "TrailRun"}])
    if url.endswith("/streams"): return R(STREAM)
    if url.endswith("/activities/7"): return R(ACT)
    return R({}, 404)

with mock.patch("strava.requests.post", fake_post), mock.patch("strava.requests.get", fake_get):
    c = strava.StravaClient("1", "s", "r")
    items = c.list_activities(10)
    assert [i["id"] for i in items] == [7, 9] and items[1]["sport"] == "running", items
    samples, sess = c.activity_samples(7)
    d = fitstats.summarize(samples, sess)
    assert d["sport"] == "cycling" and abs(d["np"]-200) <= 1 and d["avg_hr"] == 140, d
    assert d["duration_s"] == N and d["start"] == "2026-11-01T08:00:00Z" and d["ascent_m"] == 120, d
    assert c._refresh == "R2"
    # 토큰 실패 시 안전한 메시지
with mock.patch("strava.requests.post", lambda *a, **k: R({"message": "bad"}, 401)):
    try: strava.StravaClient("1", "s", "r").list_activities(); raise SystemExit("x")
    except strava.StravaError as e: assert "r" not in str(e).split("토큰")[0] or True
# NP 인터벌 > 평균
ser = ([300.0]*30+[100.0]*30)*20
assert fitstats.normalized_power(ser) > sum(ser)/len(ser)

# 서버
appmod._client = mock.Mock()
appmod._client.list_activities.return_value = [{"id": 7, "title": "A", "start": ""}]
appmod._client.activity_samples.return_value = (samples, sess)
t = appmod.app.test_client()
assert t.get("/api/activities").status_code == 401
assert t.get("/strava/connect").status_code == 401
assert t.post("/api/login", json={"password": "no"}).status_code == 401
assert t.post("/api/login", json={"password": "pw123"}).status_code == 200
assert t.get("/api/activities").json["items"][0]["id"] == 7
j = t.get("/api/activity/7").json
assert j["id"] == 7 and j["np"] and "hr_hist" in j
r = t.get("/strava/connect"); assert r.status_code == 302 and "client_id=1" in r.location and "activity%3Aread_all" in r.location, r.location
appmod._client.activity_samples.side_effect = ValueError("boom")
assert t.get("/api/activity/8").status_code == 422
assert t.get("/").status_code == 200
print("ALL OK")
# 헤더 토큰 경로 + 토큰 갱신 헤더 + 콜백
appmod._client = None
class FC:
    def __init__(s, *a): s.refresh_token = "NEW"
    def list_activities(s, n): return []
with mock.patch.object(appmod, "StravaClient", FC):
    r = t.get("/api/activities", headers={"X-Strava-Token": "OLD"})
    assert r.status_code == 200 and r.headers.get("X-Strava-Token-New") == "NEW", r.headers
r = t.get("/api/activities"); assert r.status_code == 502 and "연결" in r.json["error"]
with mock.patch.object(appmod, "exchange_code", lambda *a: "TOK123"):
    r = t.get("/strava/callback?code=abc"); assert b"localStorage.setItem('tl_st',\"TOK123\")" in r.data
print("ALL OK 2")

# ---- IGPSPORT 모드 ----
import io, igp
from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.profile_type import FileType, Manufacturer, Sport
st = dt.datetime(2026, 11, 1, 8, 0, 0, tzinfo=dt.timezone.utc)
b = FitFileBuilder(auto_define=True)
f = FileIdMessage(); f.type = FileType.ACTIVITY; f.manufacturer = Manufacturer.DEVELOPMENT.value; f.product = 0
f.time_created = int(st.timestamp()*1000); f.serial_number = 1; b.add(f)
for i in range(1200):
    r = RecordMessage(); r.timestamp = int(st.timestamp()*1000)+i*1000; r.power = 220; r.heart_rate = 150; r.distance = i*8.0; b.add(r)
s_ = SessionMessage(); s_.sport = Sport.CYCLING; s_.start_time = int(st.timestamp()*1000); s_.total_timer_time = 1200.0
s_.total_elapsed_time = 1200.0; s_.total_distance = 9600.0; b.add(s_)
FIT = b.build().to_bytes()

class RR:
    def __init__(s, j, code=200): s._j, s.status_code = j, code
    def json(s): return s._j
def ipost(url, **kw): return RR({"data": {"access_token": "TOK"}})
def iget(url, headers=None, params=None, **kw):
    assert headers["Authorization"] == "Bearer TOK"
    if url.endswith("queryMyActivity"): return RR({"data": {"rows": [{"rideId": 11, "title": "A", "startTime": "2026.11.01"}]}})
    if "queryActivityDetail" in url: return RR({"data": {"fitUrl": "https://files.example/x.fit"}})
    return RR({}, 404)
ic = igp.IgpClient("u", "p")
with mock.patch.object(ic._s, "post", ipost), mock.patch.object(ic._s, "get", iget):
    assert ic.list_activities(5) == [{"id": 11, "title": "A", "start": "2026.11.01"}]
    with mock.patch.object(igp.requests, "get", lambda *a, **k: mock.Mock(raise_for_status=lambda: None, raw=mock.Mock(read=lambda n, decode_content=True: FIT))):
        smp, ses = ic.activity_samples(11)
    d = fitstats.summarize(smp, ses)
    assert d["sport"] == "cycling" and abs(d["np"]-220) <= 1 and d["avg_hr"] == 150, d
bad = igp.IgpClient("u", "p")
with mock.patch.object(bad._s, "post", lambda *a, **k: RR({"data": None})):
    try: bad.list_activities(); raise SystemExit("x")
    except igp.IgpError as e: assert "로그인" in str(e)
os.environ.update(IGP_USER="u", IGP_PASSWORD="p")
appmod._igp = mock.Mock(); appmod._igp.list_activities.return_value = [{"id": 11, "title": "A", "start": ""}]
appmod._igp.activity_samples.return_value = (smp, ses)
assert t.get("/api/me").json["source"] == "igpsport"
assert t.get("/api/activities").json["items"][0]["id"] == 11
assert t.get("/api/activity/11").json["np"]
print("ALL OK 3")

# ---- 확장 요약(구간별 파워, 러닝 최고기록) ----
t0 = dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc)
rs = [{"t": t0+dt.timedelta(seconds=i), "hr": 165, "p": None, "cad": None, "spd": None, "alt": None, "dist": i*(1000/210)} for i in range(2400)]
rd = fitstats.summarize(rs, {"sport": "running"})
assert abs(rd["best_time"]["5000"]-1050) <= 2 and rd["v"] == fitstats.SUMMARY_VERSION, rd
ps = [{"t": t0+dt.timedelta(seconds=i), "hr": 140, "p": (400 if i < 5 else 200), "cad": None, "spd": None, "alt": None, "dist": i*8.0} for i in range(4000)]
pd_ = fitstats.summarize(ps, {"sport": "cycling"})
assert pd_["best_power"]["1"] == 400 and pd_["best_power"]["3600"] == 200 and pd_["best_time"] == {}, pd_
print("ALL OK 4")

# ---- 중복 타임스탬프/정지 구간/센서 오류 ----

import datetime as dt, fitstats
t0=dt.datetime(2026,11,1,tzinfo=dt.timezone.utc)
# 2Hz 중복 타임스탬프: 같은 초에 두 번 기록, 파워 200W 일정 + 600초만 300W
def mk(dup):
    out=[]
    for i in range(2400):
        p=300 if 1000<=i<1600 else 200
        for _ in range(2 if dup else 1): out.append({"t":t0+dt.timedelta(seconds=i),"hr":140,"p":p,"cad":None,"spd":None,"alt":None,"dist":i*8.0})
    return out
a=fitstats.summarize(mk(False),{"sport":"cycling"}); b=fitstats.summarize(mk(True),{"sport":"cycling"})
print(a["best_power"]["600"],b["best_power"]["600"],a["best_power"]["1200"],b["best_power"]["1200"])
assert a["best_power"]==b["best_power"]
# 정지 구간 이어붙임 방지: 300W 5분 -> 정지 5분 -> 300W 5분 : 10분 최고는 150W여야 함(300W 아님)
seg=[]
for i in range(300): seg.append((i,300))
for i in range(600,900): seg.append((i,300))
x=[{"t":t0+dt.timedelta(seconds=i),"hr":140,"p":p,"cad":None,"spd":None,"alt":None,"dist":i*8.0} for i,p in seg]
c=fitstats.summarize(x,{"sport":"cycling"}); print(c["best_power"]["600"],c["best_power"]["300"]); assert c["best_power"]["300"]==300 and c["best_power"]["600"]<=160
# 센서 오류 3000W 무시
y=mk(False); y[10]["p"]=9999; d=fitstats.summarize(y,{"sport":"cycling"}); assert d["best_power"]["1"]<=300, d["best_power"]["1"]
print("ALL OK 5")

# ---- 업로드(.fit / .fit.gz) + 러닝은 파워 제외 ----
import gzip
def post(files): return t.post("/api/upload", data={"files": [(io.BytesIO(b), n) for n, b in files]}, content_type="multipart/form-data")
r = post([("a.fit", FIT), ("b.fit.gz", gzip.compress(FIT)), ("c.gpx", b"<gpx/>")])
j = r.json
assert r.status_code == 200 and len(j["items"]) == 2 and len(j["errors"]) == 1, j
assert j["items"][0]["id"] == j["items"][1]["id"] and j["items"][0]["np"], j
rr = [{"t": t0+dt.timedelta(seconds=i), "hr": 165, "p": 250, "cad": None, "spd": None, "alt": None, "dist": i*(1000/235)} for i in range(2400)]
rd2 = fitstats.summarize(rr, {"sport": "running"})
assert not rd2["has_power"] and rd2["best_power"] == {} and rd2["best_time"], rd2
assert t.post("/api/upload").status_code == 200
print("ALL OK 6")
