"""IGPSPORT 비공식 웹 API 클라이언트 (본인 계정의 운동 기록을 읽기만 한다).

공식 공개 API가 아니라 IGPSPORT 웹/앱이 쓰는 주소를 따라 만든 것이라, 회사가 바꾸면 언제든 동작이 멈출 수 있다.
아이디/비밀번호는 서버 환경변수(IGP_USER, IGP_PASSWORD)로만 받고 코드·화면·로그 어디에도 남기지 않는다.
"""
import threading
import time
from urllib.parse import urlparse

import requests

import fitstats

REGIONS = {
    "international": "https://prod.en.igpsport.com/service",
    "china": "https://prod.zh.igpsport.com/service",
}
TOKEN_TTL = 20 * 60
MAX_FIT_BYTES = 40 * 1024 * 1024
TIMEOUT = 30


class IgpError(Exception):
    """화면에 그대로 보여줘도 되는 오류(비밀번호 등 민감정보는 절대 넣지 않는다)."""


class IgpClient:
    def __init__(self, user, password, region="international"):
        if region not in REGIONS:
            raise IgpError("IGP_REGION 은 international 또는 china 여야 합니다.")
        self._user, self._password, self.region = user, password, region
        self._base = REGIONS[region]
        self._token, self._token_at = None, 0.0
        self._lock = threading.Lock()
        self._s = requests.Session()
        self._s.headers.update({"User-Agent": "trainlog/1.0 (personal use)"})

    def _login(self):
        try:
            r = self._s.post(f"{self._base}/auth/account/login", timeout=TIMEOUT,
                             json={"appId": "igpsport-web", "username": self._user, "password": self._password})
            body = r.json()
        except (requests.RequestException, ValueError):
            raise IgpError("IGPSPORT 서버에 연결하지 못했어요. 잠시 뒤 다시 시도해 주세요.")
        data = body.get("data") if isinstance(body, dict) else None
        token = data.get("access_token") if isinstance(data, dict) else None
        if not token:
            raise IgpError("IGPSPORT 로그인에 실패했어요. 이메일/비밀번호와 지역(IGP_REGION)을 확인하세요. "
                           "구글·애플 로그인 계정은 IGPSPORT에서 비밀번호를 따로 설정해야 할 수 있어요.")
        self._token, self._token_at = token, time.time()

    def _headers(self, force=False):
        with self._lock:
            if force or not self._token or time.time() - self._token_at > TOKEN_TTL:
                self._login()
            return {"Authorization": f"Bearer {self._token}"}

    def _get(self, url, **kw):
        for attempt in (0, 1):
            try:
                r = self._s.get(url, headers=self._headers(force=attempt == 1), timeout=TIMEOUT, **kw)
            except requests.RequestException:
                raise IgpError("IGPSPORT 서버에 연결하지 못했어요. 잠시 뒤 다시 시도해 주세요.")
            if r.status_code in (401, 403) and attempt == 0:
                continue
            return r
        return r

    def list_activities(self, limit=20):
        base = f"{self._base}/web-gateway/web-analyze/activity"
        out, page = [], 1
        while len(out) < limit:
            r = self._get(f"{base}/queryMyActivity",
                          params={"pageNo": str(page), "pageSize": str(min(limit, 20)), "sort": "1", "reqType": "0"})
            if r.status_code != 200:
                raise IgpError(f"활동 목록을 가져오지 못했어요 (HTTP {r.status_code}).")
            try:
                rows = ((r.json().get("data") or {}).get("rows")) or []
            except ValueError:
                raise IgpError("활동 목록 응답을 읽지 못했어요.")
            if not rows:
                break
            for it in rows:
                rid = it.get("rideId", it.get("RideId"))
                if rid is None or len(out) >= limit:
                    continue
                out.append({"id": int(rid), "title": it.get("title") or it.get("Title") or f"IGPSPORT {rid}",
                            "start": it.get("startTime") or it.get("StartTime") or ""})
            if len(rows) < min(limit, 20):
                break
            page += 1
        return out

    def fit_url(self, ride_id):
        base = f"{self._base}/web-gateway/web-analyze/activity"
        r = self._get(f"{base}/queryActivityDetail/{int(ride_id)}")
        if r.status_code == 200:
            try:
                url = (r.json().get("data") or {}).get("fitUrl")
            except ValueError:
                url = None
            if url:
                return url
        r = self._get(f"{base}/getDownloadUrl/{int(ride_id)}")
        if r.status_code == 200:
            try:
                return r.json().get("data")
            except ValueError:
                return None
        return None

    def download_fit(self, ride_id):
        url = self.fit_url(ride_id)
        if not url or urlparse(url).scheme != "https":
            raise IgpError("이 운동의 FIT 파일 주소를 받지 못했어요.")
        try:
            r = requests.get(url, timeout=60, stream=True)
            r.raise_for_status()
            data = r.raw.read(MAX_FIT_BYTES + 1, decode_content=True)
        except requests.RequestException:
            raise IgpError("FIT 파일을 내려받지 못했어요.")
        if len(data) > MAX_FIT_BYTES:
            raise IgpError("FIT 파일이 너무 커요.")
        return data

    def activity_samples(self, ride_id):
        return fitstats.read_fit(self.download_fit(ride_id))
