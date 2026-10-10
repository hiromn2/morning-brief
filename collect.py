import json
from datetime import datetime, timezone

import feedparser
import pandas as pd
import yfinance as yf

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

FEEDS = {
    "Japan": [
        "https://www3.nhk.or.jp/rss/news/cat5.xml",
        "https://www3.nhk.or.jp/rss/news/cat6.xml",
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
    df = df[df["10年"] != "-"]
    return {
        "value": float(df["10年"].iloc[-1]),
        "prev": float(df["10年"].iloc[-2]),
        "date": df.iloc[-1, 0],
        "source": "Ministry of Finance jgbcm.csv",
    }


def headlines():
    out = {}
    for region, urls in FEEDS.items():
        items = []
        for url in urls:
            for e in feedparser.parse(url).entries[:25]:
                items.append({"title": e.title, "link": e.link, "published": e.get("published", "")})
        out[region] = items
    return out


def safe(fn):
    try:
        return fn()
    except Exception as err:
        return {"error": str(err)}


markets = safe(prices)
markets.update(safe(fred))
markets["JGB 10Y"] = safe(jgb10y)

snapshot = {
    "collected_at": datetime.now(timezone.utc).isoformat(),
    "markets": markets,
    "headlines": safe(headlines),
}

with open("data/latest.json", "w", encoding="utf-8") as f:
    json.dump(snapshot, f, ensure_ascii=False, indent=2)
