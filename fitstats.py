"""FIT 파일 -> 운동 요약 (NP, 파워/심박 분포, 최고 파워 등).

설정(FTP, 심박 등)에 따라 달라지는 값(TSS 등)은 여기서 계산하지 않고, 설정과 무관한 원자료만 만든다.
화면(브라우저)에서 설정을 바꾸면 모든 기록의 TSS가 바로 다시 계산되도록 하기 위해서다.
"""
import io
from datetime import timezone

import fitdecode

POWER_BIN = 5          # 파워 분포 구간(W)
BEST_WINDOWS = (5, 60, 300, 1200, 3600)   # 최고 평균 파워 구간(초)
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
    """1초 간격 파워 배열. 10초 이하 공백은 직전 값으로 채우고, 그보다 길면(정지) 건너뛴다."""
    out, prev_t, prev_p = [], None, 0.0
    for s in samples:
        p = float(s["p"]) if s["p"] is not None else 0.0
        if prev_t is not None:
            gap = (s["t"] - prev_t).total_seconds()
            if 1 < gap <= 10:
                out.extend([prev_p] * int(gap - 1))
        out.append(p)
        prev_t, prev_p = s["t"], p
    return out


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

    series = power_series_1hz(samples)
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
        "max_power": (int(max(series)) if has_power and series else None),
        "avg_cad": round(cad_sum / cad_w) if cad_w else None,
        "best_power": best_powers(series) if has_power else {},
        "hr_hist": {k: round(v) for k, v in hr_hist.items()},
        "power_hist": {k: round(v) for k, v in p_hist.items()} if has_power else {},
    }


def parse_fit(data: bytes) -> dict:
    samples, session = read_fit(data)
    return summarize(samples, session)
