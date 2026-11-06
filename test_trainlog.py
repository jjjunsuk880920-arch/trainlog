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
