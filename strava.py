"""Strava 공식 API 클라이언트 (본인 운동 읽기 전용). 필요한 권한: activity:read_all"""
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import requests

API = "https://www.strava.com/api/v3"
OAUTH = "https://www.strava.com/oauth"
TIMEOUT = 30
CYCLING = {"ride", "virtualride", "ebikeride", "gravelride", "mountainbikeride", "emountainbikeride", "handcycle"}
RUNNING = {"run", "trailrun", "virtualrun"}


class StravaError(Exception):
    """화면에 보여줘도 되는 오류 메시지(토큰·비밀번호는 절대 넣지 않는다)."""


def authorize_url(client_id, redirect_uri):
    return f"{OAUTH}/authorize?" + urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
        "approval_prompt": "auto", "scope": "read,activity:read_all"})


def exchange_code(client_id, secret, code):
    try:
        r = requests.post(f"{OAUTH}/token", timeout=TIMEOUT, data={
            "client_id": client_id, "client_secret": secret, "code": code, "grant_type": "authorization_code"})
        j = r.json()
    except (requests.RequestException, ValueError):
        raise StravaError("스트라바 서버에 연결하지 못했어요.")
    if not j.get("refresh_token"):
        raise StravaError("스트라바 승인에 실패했어요. Client ID/Secret을 확인하세요.")
    return j["refresh_token"]


def sport_of(a):
    s = str(a.get("sport_type") or a.get("type") or "").lower()
    return "cycling" if s in CYCLING else "running" if s in RUNNING else (s or "unknown")


class StravaClient:
    def __init__(self, client_id, secret, refresh_token):
        self._id, self._secret, self._refresh = client_id, secret, refresh_token
        self._access, self._exp = None, 0.0
        self._lock = threading.Lock()

    def _token(self, force=False):
        with self._lock:
            if force or not self._access or time.time() > self._exp - 60:
                try:
                    r = requests.post(f"{OAUTH}/token", timeout=TIMEOUT, data={
                        "client_id": self._id, "client_secret": self._secret,
                        "grant_type": "refresh_token", "refresh_token": self._refresh})
                    j = r.json()
                except (requests.RequestException, ValueError):
                    raise StravaError("스트라바 서버에 연결하지 못했어요.")
                if not j.get("access_token"):
                    raise StravaError("스트라바 로그인 정보가 만료됐거나 잘못됐어요. /strava/connect 로 다시 승인하세요.")
                self._access, self._exp = j["access_token"], float(j.get("expires_at", time.time() + 3600))
                self._refresh = j.get("refresh_token") or self._refresh   # 최신 토큰을 메모리에 유지
            return self._access

    def _get(self, path, **params):
        for attempt in (0, 1):
            try:
                r = requests.get(API + path, params=params, timeout=TIMEOUT,
                                 headers={"Authorization": f"Bearer {self._token(force=attempt == 1)}"})
            except requests.RequestException:
                raise StravaError("스트라바 서버에 연결하지 못했어요.")
            if r.status_code == 401 and attempt == 0:
                continue
            break
        if r.status_code == 429:
            raise StravaError("스트라바 호출 한도에 걸렸어요. 15분 뒤 다시 시도하세요.")
        if r.status_code == 404:
            raise StravaError("데이터가 없는 운동이에요.")
        if r.status_code != 200:
            raise StravaError(f"스트라바 오류 (HTTP {r.status_code}).")
        try:
            return r.json()
        except ValueError:
            raise StravaError("스트라바 응답을 읽지 못했어요.")

    def list_activities(self, limit=20):
        out, page = [], 1
        while len(out) < limit:
            rows = self._get("/athlete/activities", per_page=min(limit, 100), page=page)
            if not rows:
                break
            for a in rows:
                if len(out) < limit and not a.get("manual"):
                    out.append({"id": int(a["id"]), "title": a.get("name") or f"Strava {a['id']}",
                                "start": a.get("start_date") or "", "sport": sport_of(a)})
            if len(rows) < min(limit, 100):
                break
            page += 1
        return out

    def activity_samples(self, rid):
        """(샘플 목록, 세션 정보) — fitstats.summarize 에 그대로 넣을 수 있는 형태"""
        a = self._get(f"/activities/{int(rid)}")
        st = self._get(f"/activities/{int(rid)}/streams", keys="time,watts,heartrate,cadence,altitude,distance",
                       key_by_type="true")
        t = (st.get("time") or {}).get("data") or []
        if not t:
            raise StravaError("시간별 데이터가 없는 운동이에요.")
        start = datetime.strptime(a["start_date"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        col = lambda k: (st.get(k) or {}).get("data") or []
        w, hr, cad, alt, dist = col("watts"), col("heartrate"), col("cadence"), col("altitude"), col("distance")
        pick = lambda arr, i: arr[i] if i < len(arr) else None
        samples = [{"t": start + timedelta(seconds=s), "hr": pick(hr, i), "p": pick(w, i), "cad": pick(cad, i),
                    "spd": None, "alt": pick(alt, i), "dist": pick(dist, i)} for i, s in enumerate(t)]
        session = {"sport": sport_of(a), "sub_sport": "", "start_time": start,
                   "total_timer_time": a.get("moving_time"), "total_elapsed_time": a.get("elapsed_time"),
                   "total_distance": a.get("distance"), "total_ascent": a.get("total_elevation_gain")}
        return samples, session
