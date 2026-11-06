"""TrainLog 서버: 스트라바 운동 기록을 읽어 요약(JSON)으로 돌려준다. 저장은 브라우저가 한다."""
import gzip
import hashlib
import hmac
import io
import os
import time
import zlib

from flask import Flask, jsonify, request, send_from_directory, make_response, redirect, g

import fitstats
from igp import IgpClient, IgpError
from strava import StravaClient, StravaError, authorize_url, exchange_code

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 40 * 1024 * 1024

SITE_PASSWORD = os.environ.get("SITE_PASSWORD", "")
SECRET = os.environ.get("SECRET_KEY") or hashlib.sha256(
    (SITE_PASSWORD + "|" + os.environ.get("STRAVA_CLIENT_SECRET", "")).encode()).hexdigest()
COOKIE = "tl_auth"
COOKIE_DAYS = 90

_client = None
_cache = {}                      # ride_id -> 요약 (서버가 잠들면 사라지고, 브라우저에 영구 저장됨)
_fails = {"n": 0, "until": 0.0}  # 비밀번호 무차별 대입 방지


_clients = {}
_igp = None


def igp_mode():
    return bool(os.environ.get("IGP_USER") and os.environ.get("IGP_PASSWORD"))


def client():
    """요청 헤더의 토큰(브라우저에 저장됨)을 우선 쓰고, 없으면 환경변수 토큰을 쓴다."""
    global _client, _igp
    if igp_mode():
        if _igp is None:
            _igp = IgpClient(os.environ["IGP_USER"], os.environ["IGP_PASSWORD"], os.environ.get("IGP_REGION", "international"))
        return _igp
    cid, sec = os.environ.get("STRAVA_CLIENT_ID"), os.environ.get("STRAVA_CLIENT_SECRET")
    hdr = request.headers.get("X-Strava-Token", "").strip()
    if hdr and cid and sec:
        if hdr not in _clients:
            if len(_clients) > 5:
                _clients.clear()
            _clients[hdr] = StravaClient(cid, sec, hdr)
        g.sclient, g.stoken = _clients[hdr], hdr
        return _clients[hdr]
    if _client is None:
        tok = os.environ.get("STRAVA_REFRESH_TOKEN")
        if not (cid and sec and tok):
            raise StravaError("스트라바가 아직 연결되지 않았어요. 위의 '스트라바 연결' 버튼을 눌러 주세요.")
        _client = StravaClient(cid, sec, tok)
    return _client


@app.after_request
def rotate_header(resp):
    c, old = getattr(g, "sclient", None), getattr(g, "stoken", None)
    if c is not None and c.refresh_token != old:
        resp.headers["X-Strava-Token-New"] = c.refresh_token
        resp.headers["Access-Control-Expose-Headers"] = "X-Strava-Token-New"
    return resp


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
    import json as _json
    return ("<meta charset=utf-8><meta name=viewport content='width=device-width'><body style='font:16px system-ui;padding:16px;background:#0f1318;color:#e8edf3'>"
            "연결 완료! 이동 중…<script>localStorage.setItem('tl_st'," + _json.dumps(tok) + ");location.replace('/')</script>")


@app.post("/api/upload")
def upload():
    """과거 운동 가져오기: .fit 또는 .fit.gz 파일(여러 개)을 올리면 요약을 돌려준다. 서버에는 저장하지 않는다."""
    items, errors = [], []
    for f in request.files.getlist("files")[:10]:
        name = (f.filename or "파일")[:60]
        try:
            data = f.read()
            if data[:2] == b"\x1f\x8b":
                d = zlib.decompressobj(16 + zlib.MAX_WBITS)
                data = d.decompress(data, 40 * 1024 * 1024)
            if data[8:12] != b".FIT":
                errors.append(f"{name}: FIT 파일이 아니에요(GPX/TCX는 지원하지 않아요).")
                continue
            summ = fitstats.parse_fit(data)
            summ["id"] = 10**9 + zlib.crc32((summ["start"] + str(summ["distance_m"])).encode())
            summ["title"] = name
            items.append(summ)
        except Exception:
            errors.append(f"{name}: 해석하지 못했어요.")
    return jsonify(items=items, errors=errors)


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
    return jsonify(ok=True, source="igpsport" if igp_mode() else "strava")


@app.get("/api/activities")
def activities():
    try:
        limit = max(1, min(int(request.args.get("limit", 20)), 200))
        return jsonify(items=client().list_activities(limit))
    except (StravaError, IgpError) as e:
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
    except (StravaError, IgpError) as e:
        return jsonify(error=str(e)), 502
    except Exception:             # 예상 못한 데이터: 내용은 숨기고 일반 메시지만
        return jsonify(error="운동 데이터를 해석하지 못했어요."), 422
    summ["id"] = rid
    _cache[rid] = summ
    return jsonify(summ)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
