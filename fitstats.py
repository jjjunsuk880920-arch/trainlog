"""FIT 파일 -> 운동 요약 (NP, 파워/심박 분포, 최고 파워 등).

설정(FTP, 심박 등)에 따라 달라지는 값(TSS 등)은 여기서 계산하지 않고, 설정과 무관한 원자료만 만든다.
화면(브라우저)에서 설정을 바꾸면 모든 기록의 TSS가 바로 다시 계산되도록 하기 위해서다.
"""
import io
from datetime import timezone

import fitdecode

POWER_BIN = 5          # 파워 분포 구간(W)
BEST_WINDOWS = (1, 5, 10, 30, 60, 120, 180, 240, 300, 420, 480, 600, 900, 1200, 1800, 2400, 3600)   # 최고 평균 파워 구간(초)
BEST_DISTS = (1000, 3000, 5000, 10000, 21097)   # 러닝 최고 기록 거리(m)
SUMMARY_VERSION = 3
MAX_VALID_POWER = 2500   # 이보다 큰 값은 센서 오류로 보고 버린다
MAX_DT = 5.0           # 기록 간격이 이보다 길면(정지·끊김) 5초만 센다


def _utc(ts):
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)


def read_fit(data: bytes):
    """FIT 바이트 -> (샘플 목록, 세션 정보). 샘플: dict(t, hr, p, cad, spd, alt, dist)"""
    samples, session = [], None
    with fitdecode.FitReader(io.BytesIO(data)) as reader:
        for frame in reader:
            if frame.frame_type != fitdecode.FIT_FRAME_DATA:
                continue
            if frame.name == "record":
                ts = frame.get_value("timestamp", fallback=None)
                if ts is None:
                    continue
                g = lambda k: frame.get_value(k, fallback=None)
                samples.append({
                    "t": _utc(ts), "hr": g("heart_rate"), "p": g("power"), "cad": g("cadence"),
                    "spd": g("enhanced_speed") if g("enhanced_speed") is not None else g("speed"),
                    "alt": g("enhanced_altitude") if g("enhanced_altitude") is not None else g("altitude"),
                    "dist": g("distance"),
                })
            elif frame.name == "session" and session is None:
                keys = ("sport", "sub_sport", "start_time", "total_timer_time", "total_elapsed_time",
                        "total_distance", "total_ascent", "avg_power", "max_power", "avg_heart_rate", "max_heart_rate")
                session = {k: frame.get_value(k, fallback=None) for k in keys}
    return samples, session or {}


def _cumsum(v):
    cs = [0.0]
    for x in v:
        cs.append(cs[-1] + x)
    return cs


def power_series_1hz(samples):
    """실제 경과 시간(초) 기준 1Hz 파워 배열 2개를 돌려준다.
    (true)   정지 구간을 0W로 채운 시간 그대로의 배열 -> 구간 최고 파워(5분·20분 등)용
    (active) 정지 구간을 뺀 배열 -> 평균/NP용
    같은 초에 기록이 여러 개면 평균내어 1초로 센다(시간이 늘어나는 오류 방지).
    10초 이하 공백은 직전 값으로 메우고, 더 길면 정지로 본다."""
    t0 = samples[0]["t"]
    by = {}
    for s in samples:
        p = s["p"]
        if p is None or p < 0 or p > MAX_VALID_POWER:
            p = 0.0 if p is None else None
        sec = int((s["t"] - t0).total_seconds())
        if p is None:
            continue
        by.setdefault(sec, []).append(float(p))
    if not by:
        return [], []
    true, active, prev, last = [], [], 0.0, None
    for sec in range(max(by) + 1):
        if sec in by:
            prev = sum(by[sec]) / len(by[sec])
            true.append(prev); active.append(prev); last = sec
        elif last is not None and sec - last <= 10:
            true.append(prev); active.append(prev)
        else:
            true.append(0.0)
    return true, active


def normalized_power(series):
    n = len(series)
    if n == 0:
        return 0.0
    if n < 30:
        return sum(series) / n
    cs = _cumsum(series)
    acc = 0.0
    for i in range(n - 29):
        acc += ((cs[i + 30] - cs[i]) / 30.0) ** 4
    return (acc / (n - 29)) ** 0.25


def best_powers(series):
    out, n = {}, len(series)
    if n == 0:
        return out
    cs = _cumsum(series)
    for w in BEST_WINDOWS:
        if n >= w:
            out[str(w)] = round(max((cs[i + w] - cs[i]) / w for i in range(n - w + 1)))
    return out


def best_times(samples):
    """누적거리 기준, 각 거리를 가장 빨리 달린 구간의 시간(초). 정지 시간은 포함된다."""
    pts = [((s["t"] - samples[0]["t"]).total_seconds(), s["dist"]) for s in samples if s["dist"] is not None]
    out = {}
    if len(pts) < 2:
        return out
    for D in BEST_DISTS:
        if pts[-1][1] - pts[0][1] < D:
            continue
        best, j = None, 0
        for i in range(len(pts)):
            if j < i:
                j = i
            while j < len(pts) and pts[j][1] - pts[i][1] < D:
                j += 1
            if j >= len(pts):
                break
            # 거리 D 지점을 j-1~j 사이에서 보간
            d0, d1 = pts[j - 1][1] - pts[i][1], pts[j][1] - pts[i][1]
            t0, t1 = pts[j - 1][0], pts[j][0]
            tt = t0 + (t1 - t0) * ((D - d0) / (d1 - d0) if d1 > d0 else 1) - pts[i][0]
            if best is None or tt < best:
                best = tt
        if best is not None and best > 0:
            out[str(D)] = round(best)
    return out


def elevation_gain(samples):
    alts = [s["alt"] for s in samples if s["alt"] is not None]
    if len(alts) < 5:
        return None
    sm = [sum(alts[max(0, i - 2):i + 3]) / len(alts[max(0, i - 2):i + 3]) for i in range(len(alts))]
    return round(sum(max(0.0, sm[i] - sm[i - 1]) for i in range(1, len(sm))))


def summarize(samples, session=None):
    session = session or {}
    if not samples:
        raise ValueError("운동 기록(점)이 없는 파일입니다.")
    samples = sorted(samples, key=lambda s: s["t"])

    # 시간 가중치(초): 다음 기록까지의 간격(최대 5초)
    dts = []
    for i, s in enumerate(samples):
        dt = (samples[i + 1]["t"] - s["t"]).total_seconds() if i + 1 < len(samples) else 1.0
        dts.append(min(max(dt, 0.0), MAX_DT))

    hr_hist, p_hist = {}, {}
    hr_sum = hr_w = 0.0
    hr_max = 0
    cad_sum = cad_w = 0.0
    for s, dt in zip(samples, dts):
        if s["hr"]:
            hr_hist[str(int(s["hr"]))] = hr_hist.get(str(int(s["hr"])), 0) + dt
            hr_sum += s["hr"] * dt; hr_w += dt; hr_max = max(hr_max, int(s["hr"]))
        if s["p"] is not None:
            b = str(int(s["p"] // POWER_BIN) * POWER_BIN)
            p_hist[b] = p_hist.get(b, 0) + dt
        if s["cad"]:
            cad_sum += s["cad"] * dt; cad_w += dt

    true_series, series = power_series_1hz(samples)
    nonzero = sum(1 for x in series if x > 0)
    has_power = len(series) > 60 and nonzero > len(series) * 0.2
    np_w = normalized_power(series) if has_power else 0.0
    avg_p = (sum(series) / len(series)) if (has_power and series) else 0.0

    start = session.get("start_time") or samples[0]["t"]
    elapsed = (samples[-1]["t"] - samples[0]["t"]).total_seconds()
    timer = session.get("total_timer_time")
    duration = float(timer) if timer else min(sum(dts), elapsed or sum(dts))
    dist = session.get("total_distance")
    if dist is None:
        ds = [s["dist"] for s in samples if s["dist"] is not None]
        dist = ds[-1] if ds else 0.0
    ascent = session.get("total_ascent")
    if ascent is None:
        ascent = elevation_gain(samples)

    sport = str(session.get("sport") or "").lower() or ("cycling" if has_power else "unknown")
    return {
        "start": _utc(start).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sport": sport,
        "sub_sport": str(session.get("sub_sport") or "").lower(),
        "duration_s": round(duration),
        "elapsed_s": round(elapsed),
        "distance_m": round(float(dist)),
        "ascent_m": int(ascent) if ascent is not None else None,
        "avg_hr": round(hr_sum / hr_w) if hr_w else None,
        "max_hr": hr_max or None,
        "has_power": bool(has_power),
        "avg_power": round(avg_p) if has_power else None,
        "np": round(np_w) if has_power else None,
        "max_power": (int(max(true_series)) if has_power and true_series else None),
        "avg_cad": round(cad_sum / cad_w) if cad_w else None,
        "best_power": best_powers(true_series) if has_power else {},
        "best_time": best_times(samples) if sport in ("running", "walking") or not has_power else {},
        "v": SUMMARY_VERSION,
        "hr_hist": {k: round(v) for k, v in hr_hist.items()},
        "power_hist": {k: round(v) for k, v in p_hist.items()} if has_power else {},
    }


def parse_fit(data: bytes) -> dict:
    samples, session = read_fit(data)
    return summarize(samples, session)
