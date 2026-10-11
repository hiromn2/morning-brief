"""Morning-brief collector. Runs in GitHub Actions at 06:15 JST (Mon–Fri) and writes data/latest.json.

Sections:
  markets    – Yahoo Finance closes, FRED series, MoF JGB 10Y
  headlines  – RSS headlines per region (only items from the last 3 days)
  disasters  – JMA earthquakes, tsunami notices, typhoons and Tokyo warnings
Every section is wrapped in safe(), so one broken source never stops the others.
"""
import json
import math
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import feedparser
import pandas as pd
import yfinance as yf

JST = timezone(timedelta(hours=9))
NOW = datetime.now(timezone.utc)
UA = {"User-Agent": "morning-brief-collector/1.0 (+https://github.com/hiromn2/morning-brief)"}

TICKERS = {
    "S&P 500": "^GSPC",
    "Nikkei 225": "^N225",
    "USD/JPY": "JPY=X",
    "BRL/JPY": "BRLJPY=X",
    "USD/BRL": "BRL=X",
    "Ibovespa": "^BVSP",
    "VIX": "^VIX",
    "VIX 3M": "^VIX3M",
    "US 10Y": "^TNX",
    "WTI": "CL=F",
}

FRED = {
    "US HY spread": "BAMLH0A0HYM2",
    "St. Louis stress index": "STLFSI4",
}

# NHK's old RSS stopped updating in Aug 2026, so Japan also pulls Google News (Japan edition)
# and The Japan Times. Items older than MAX_AGE_DAYS are dropped everywhere.
FEEDS = {
    "Japan": [
        "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/headlines/section/topic/NATION?hl=ja&gl=JP&ceid=JP:ja",
        "https://www.japantimes.co.jp/feed/",
        "https://www3.nhk.or.jp/rss/news/cat5.xml",
    ],
    "International": [
        "https://feeds.bbci.co.uk/news/business/rss.xml",
        "https://feeds.bbci.co.uk/news/world/rss.xml",
    ],
    "Brazil": [
        "https://feeds.folha.uol.com.br/mercado/rss091.xml",
        "https://feeds.folha.uol.com.br/poder/rss091.xml",
    ],
}
MAX_AGE_DAYS = 3
PER_REGION = 50

TOKYO = (35.68, 139.77)

# JMA warning codes (気象警報・注意報). 3x = special warning, 0x = warning, 1x/2x = advisory.
WARNING_NAMES = {
    "02": "暴風雪警報", "03": "大雨警報", "04": "洪水警報", "05": "暴風警報", "06": "大雪警報",
    "07": "波浪警報", "08": "高潮警報", "10": "大雨注意報", "12": "大雪注意報", "13": "風雪注意報",
    "14": "雷注意報", "15": "強風注意報", "16": "波浪注意報", "17": "融雪注意報", "18": "洪水注意報",
    "19": "高潮注意報", "20": "濃霧注意報", "21": "乾燥注意報", "22": "なだれ注意報", "23": "低温注意報",
    "24": "霜注意報", "25": "着氷注意報", "26": "着雪注意報", "32": "暴風雪特別警報", "33": "大雨特別警報",
    "35": "暴風特別警報", "36": "大雪特別警報", "37": "波浪特別警報", "38": "高潮特別警報",
}
TOKYO_AREAS = {"130010": "Tokyo (23 wards and Tama)", "130020": "Izu Islands north",
               "130030": "Izu Islands south", "130040": "Ogasawara"}


def get_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def parse_time(s):
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def km(a, b):
    """Great-circle distance in km between (lat, lon) pairs."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return round(6371 * 2 * math.asin(math.sqrt(h)))


# ---------------- markets ----------------
def prices():
    out = {}
    for name, ticker in TICKERS.items():
        close = yf.download(ticker, period="10d", progress=False)["Close"].squeeze().dropna()
        if len(close) < 2:
            continue
        out[name] = {
            "value": round(float(close.iloc[-1]), 4),
            "prev": round(float(close.iloc[-2]), 4),
            "date": close.index[-1].strftime("%Y-%m-%d"),
            "source": f"Yahoo Finance {ticker}",
        }
    return out


def fred():
    out = {}
    for name, series in FRED.items():
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
        df = pd.read_csv(url).dropna()
        df = df[df[series] != "."]
        out[name] = {
            "value": float(df[series].iloc[-1]),
            "prev": float(df[series].iloc[-2]),
            "date": df.iloc[-1, 0],
            "source": f"FRED {series}",
        }
    return out


def jgb10y():
    url = "https://www.mof.go.jp/jgbs/reference/interest_rate/jgbcm.csv"
    df = pd.read_csv(url, encoding="shift_jis", skiprows=1)
    # The file ends with footnote rows (e.g. "※最新のcsv…"); keep only rows with a numeric 10年 value.
    df["10年"] = pd.to_numeric(df["10年"], errors="coerce")
    df = df.dropna(subset=["10年"])
    return {
        "value": float(df["10年"].iloc[-1]),
        "prev": float(df["10年"].iloc[-2]),
        "date": str(df.iloc[-1, 0]),  # Japanese era date, e.g. R8.10.9
        "source": "Ministry of Finance jgbcm.csv",
    }


# ---------------- headlines ----------------
def headlines():
    cutoff = NOW - timedelta(days=MAX_AGE_DAYS)
    out, stale = {}, {}
    for region, urls in FEEDS.items():
        items, seen = [], set()
        for url in urls:
            try:
                entries = feedparser.parse(url, agent=UA["User-Agent"]).entries[:30]
            except Exception:
                continue
            for e in entries:
                t = e.get("published_parsed") or e.get("updated_parsed")
                when = datetime(*t[:6], tzinfo=timezone.utc) if t else None
                if when and when < cutoff:
                    continue
                title = e.get("title", "").strip()
                if not title or title in seen:
                    continue
                seen.add(title)
                items.append({"title": title, "link": e.get("link", ""),
                              "published": when.isoformat() if when else e.get("published", ""),
                              "feed": url.split("/")[2]})
        items.sort(key=lambda i: i["published"], reverse=True)
        out[region] = items[:PER_REGION]
        stale[region] = len(items) == 0
    out["_stale_regions"] = [r for r, s in stale.items() if s]
    return out


# ---------------- disasters (JMA) ----------------
def quakes():
    data = get_json("https://www.jma.go.jp/bosai/quake/data/list.json")
    cutoff = NOW - timedelta(hours=24)
    rows, seen = [], set()
    for q in data:
        at = parse_time(q.get("at", ""))
        if not at or at < cutoff:
            continue
        key = q.get("eid") or (q.get("at"), q.get("anm"))
        if key in seen:  # the list repeats an event once per report; keep the newest (first) one
            continue
        seen.add(key)
        rows.append({"at": q.get("at"), "place": q.get("anm"), "place_en": q.get("en_anm"),
                     "mag": q.get("mag"), "max_shindo": q.get("maxi") or None, "title": q.get("ttl")})
    shindo_rank = {"1": 1, "2": 2, "3": 3, "4": 4, "5-": 5, "5+": 6, "6-": 7, "6+": 8, "7": 9}
    rows.sort(key=lambda r: shindo_rank.get(r["max_shindo"] or "", 0), reverse=True)
    return {"last_24h_count": len(rows), "top": rows[:8],
            "link": "https://www.jma.go.jp/bosai/map.html#contents=earthquake_map"}


def tsunami():
    data = get_json("https://www.jma.go.jp/bosai/tsunami/data/list.json")
    cutoff = NOW - timedelta(hours=24)
    rows = []
    for t in data:
        at = parse_time(t.get("rdt") or t.get("at") or "")
        if at and at >= cutoff:
            rows.append({"at": t.get("rdt") or t.get("at"), "title": t.get("ttl"), "en_title": t.get("en_ttl")})
    return {"last_24h": rows[:5], "link": "https://www.jma.go.jp/bosai/map.html#contents=tsunami"}


def _find_centers(node, acc):
    """Collect every [lat, lon] pair stored under a 'center' key anywhere in the JSON."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "center" and isinstance(v, list) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v):
                acc.append((float(v[0]), float(v[1])))
            else:
                _find_centers(v, acc)
    elif isinstance(node, list):
        for v in node:
            _find_centers(v, acc)
    return acc


def _find_first(node, keys):
    if isinstance(node, dict):
        for k, v in node.items():
            if k in keys and isinstance(v, (str, int, float)):
                return v
            r = _find_first(v, keys)
            if r is not None:
                return r
    elif isinstance(node, list):
        for v in node:
            r = _find_first(v, keys)
            if r is not None:
                return r
    return None


def typhoons():
    targets = get_json("https://www.jma.go.jp/bosai/typhoon/data/targetTc.json")
    out = []
    for t in targets:
        tc = t.get("tropicalCyclone")
        if not tc:
            continue
        entry = {"id": tc, "number": t.get("typhoonNumber"), "category": t.get("category", {}).get("en")
                 if isinstance(t.get("category"), dict) else t.get("category")}
        try:
            fc = get_json(f"https://www.jma.go.jp/bosai/typhoon/data/{tc}/forecast.json")
            centers = _find_centers(fc, [])
            entry["name"] = _find_first(fc, {"en", "name", "typhoonNameEn"})
            if centers:
                dists = [km(TOKYO, c) for c in centers]
                entry["now"] = {"lat": centers[0][0], "lon": centers[0][1], "km_from_tokyo": dists[0]}
                entry["closest_km_from_tokyo"] = min(dists)
                entry["forecast_last"] = {"lat": centers[-1][0], "lon": centers[-1][1]}
        except Exception as err:
            entry["error"] = str(err)
        out.append(entry)
        time.sleep(0.5)
    return {"active": out, "link": "https://www.jma.go.jp/bosai/map.html#contents=typhoon"}


def tokyo_warnings():
    data = get_json("https://www.jma.go.jp/bosai/warning/data/warning/130000.json")
    rdt = data.get("reportDatetime")
    report_time = parse_time(rdt or "")
    areas = {}
    for at in data.get("areaTypes", [])[:1]:  # first areaType = the four Tokyo regions
        for a in at.get("areas", []):
            name = TOKYO_AREAS.get(a.get("code"), a.get("code"))
            active = []
            for w in a.get("warnings", []):
                code, status = w.get("code"), w.get("status", "")
                if not code or status in ("解除", "発表警報・注意報はなし"):
                    continue
                active.append({"code": code, "name": WARNING_NAMES.get(code, code), "status": status})
            areas[name] = active

    def level(ws):
        codes = [w["code"] for w in ws]
        if any(c.startswith("3") for c in codes):
            return "special warning"
        if any(c.startswith("0") for c in codes):
            return "warning"
        return "advisory" if codes else "none"

    return {
        "report_datetime": rdt,
        "stale": (report_time is None) or (NOW - report_time > timedelta(days=2)),
        "headline": data.get("headlineText", ""),
        "areas": areas,
        "tokyo_level": level(areas.get(TOKYO_AREAS["130010"], [])),
        "link": "https://www.jma.go.jp/bosai/warning/#area_type=offices&area_code=130000",
    }


def safe(fn):
    try:
        return fn()
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}"}


if __name__ == "__main__":
    markets = safe(prices)
    markets.update(safe(fred))
    markets["JGB 10Y"] = safe(jgb10y)

    snapshot = {
        "collected_at": NOW.isoformat(),
        "markets": markets,
        "headlines": safe(headlines),
        "disasters": {
            "quakes": safe(quakes),
            "tsunami": safe(tsunami),
            "typhoons": safe(typhoons),
            "tokyo_warnings": safe(tokyo_warnings),
        },
    }

    with open("data/latest.json", "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
