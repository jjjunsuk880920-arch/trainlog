"""TrainLog 서버: 스트라바 운동 기록을 읽어 요약(JSON)으로 돌려준다. 저장은 브라우저가 한다."""
import hashlib
import hmac
import os
import time

from flask import Flask, jsonify, request, send_from_directory, make_response, redirect

import fitstats
from strava import StravaClient, StravaError, authorize_url, exchange_code

app = Flask(__name__, static_folder="static", static_url_path="/static")

SITE_PASSWORD = os.environ.get("SITE_PASSWORD", "")
SECRET = os.environ.get("SECRET_KEY") or hashlib.sha256(
    (SITE_PASSWORD + "|" + os.environ.get("STRAVA_CLIENT_SECRET", "")).encode()).hexdigest()
COOKIE = "tl_auth"
COOKIE_DAYS = 90

_client = None
_cache = {}                      # ride_id -> 요약 (서버가 잠들면 사라지고, 브라우저에 영구 저장됨)
_fails = {"n": 0, "until": 0.0}  # 비밀번호 무차별 대입 방지


def client():
    global _client
    if _client is None:
        cid, sec, tok = (os.environ.get(k) for k in ("STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET", "STRAVA_REFRESH_TOKEN"))
        if not (cid and sec and tok):
            raise StravaError("서버에 STRAVA_CLIENT_ID / STRAVA_CLIENT_SECRET / STRAVA_REFRESH_TOKEN 이 설정되지 않았어요. "
                              "/strava/connect 에서 승인하면 토큰을 알려드려요.")
        _client = StravaClient(cid, sec, tok)
    return _client


def _token():
    return hmac.new(SECRET.encode(), b"trainlog-auth", hashlib.sha256).hexdigest()


def authed():
    if not SITE_PASSWORD:
        return False              # 비밀번호 없이는 열지 않는다(실수로 공개되는 것 방지)
    return hmac.compare_digest(request.cookies.get(COOKIE, ""), _token())


@app.before_request
def gate():
    if (request.path.startswith("/api/") or request.path.startswith("/strava/")) and request.path != "/api/login" and not authed():
        return jsonify(error="로그인이 필요해요.", auth=False), 401


@app.get("/")
def index():
    return send_from_directory("static", "index.html")


@app.get("/strava/connect")
def strava_connect():
    cid = os.environ.get("STRAVA_CLIENT_ID")
    if not cid:
        return "STRAVA_CLIENT_ID 환경변수를 먼저 설정하세요.", 500
    return redirect(authorize_url(cid, request.url_root.replace("http://", "https://") + "strava/callback"))


@app.get("/strava/callback")
def strava_callback():
    code = request.args.get("code")
    if not code:
        return "승인이 취소됐거나 실패했어요.", 400
    try:
        tok = exchange_code(os.environ.get("STRAVA_CLIENT_ID", ""), os.environ.get("STRAVA_CLIENT_SECRET", ""), code)
    except StravaError as e:
        return str(e), 502
    return ("<meta name=viewport content='width=device-width'><body style='font:16px system-ui;padding:16px'>"
            "<h3>승인 완료</h3><p>아래 값을 Render 환경변수 <b>STRAVA_REFRESH_TOKEN</b> 에 붙여넣고 저장하세요. "
            "남에게 보여주지 마세요.</p><textarea readonly style='width:100%;height:90px'>" + tok + "</textarea>")


@app.get("/healthz")
def healthz():
    return "ok"


@app.post("/api/login")
def login():
    if not SITE_PASSWORD:
        return jsonify(error="서버에 SITE_PASSWORD 환경변수를 먼저 설정해 주세요."), 500
    now = time.time()
    if _fails["until"] > now:
        return jsonify(error="잠시 뒤에 다시 시도해 주세요."), 429
    pw = (request.get_json(silent=True) or {}).get("password", "")
    if not hmac.compare_digest(str(pw).encode(), SITE_PASSWORD.encode()):
        _fails["n"] += 1
        if _fails["n"] >= 5:
            _fails["n"], _fails["until"] = 0, now + 60
        return jsonify(error="비밀번호가 맞지 않아요."), 401
    _fails["n"] = 0
    resp = make_response(jsonify(ok=True))
    resp.set_cookie(COOKIE, _token(), max_age=COOKIE_DAYS * 86400, httponly=True, secure=request.is_secure,
                    samesite="Lax")
    return resp


@app.get("/api/me")
def me():
    return jsonify(ok=True)


@app.get("/api/activities")
def activities():
    try:
        limit = max(1, min(int(request.args.get("limit", 20)), 200))
        return jsonify(items=client().list_activities(limit))
    except StravaError as e:
        return jsonify(error=str(e)), 502
    except ValueError:
        return jsonify(error="limit 값이 올바르지 않아요."), 400


@app.get("/api/activity/<int:rid>")
def activity(rid):
    if rid in _cache:
        return jsonify(_cache[rid])
    try:
        samples, session = client().activity_samples(rid)
        summ = fitstats.summarize(samples, session)
    except StravaError as e:
        return jsonify(error=str(e)), 502
    except Exception:             # 예상 못한 데이터: 내용은 숨기고 일반 메시지만
        return jsonify(error="운동 데이터를 해석하지 못했어요."), 422
    summ["id"] = rid
    _cache[rid] = summ
    return jsonify(summ)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
