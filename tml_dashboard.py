#!/usr/bin/env python3
"""Tomorrowland Medallion of Memoria – Kauf-Timing-Dashboard.

Holt kostenlose Live-Daten (kein API-Key nötig) und schreibt:
  - tomorrowland_medallion.html  (Dashboard mit Charts, im Browser öffnen)
  - medallion_history.csv        (Tagesdaten für Excel / Google Sheets)

Aufruf:  python3 tml_dashboard.py
Nur Python-Standardbibliothek, keine Installation nötig.

Quellen:
  Magic Eden  https://api-mainnet.magiceden.dev/v2   (Floor, Listings, Verkäufe ~90 Tage)
  tmlunite    https://tmlunite.com/api/nft/history    (tägliche Floor-Historie seit 2022, Fan-Seite)
  CoinGecko   https://api.coingecko.com/api/v3        (SOL-Kurs, 365 Tage)
"""
import csv
import datetime as dt
import json
import os
import statistics
import sys
import time
import urllib.request
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
BERLIN = ZoneInfo("Europe/Berlin")
ME = "https://api-mainnet.magiceden.dev/v2"
TML = "https://tmlunite.com/api"
CG = "https://api.coingecko.com/api/v3"

# Die drei Teile des Medallion of Memoria (Magic-Eden-Symbol -> Kurzname in der tmlunite-Historie)
COLLECTIONS = [
    {"symbol": "tomorrowland_winter", "key": "letter-from-the-universe",
     "name": "A Letter from the Universe", "short": "Letter"},
    {"symbol": "the_reflection_of_love", "key": "the-reflection-of-love",
     "name": "The Reflection of Love", "short": "Reflection"},
    {"symbol": "tomorrowland_love_unity", "key": "symbol-of-love-and-unity",
     "name": "The Symbol of Love and Unity", "short": "Symbol"},
]
MEDALLIONS_WANTED = int(os.environ.get("MEDALLIONS_WANTED", "2"))  # je Kollektion so viele NFTs kaufen

# Wie lange zwischengespeicherte Daten gelten, wenn ein Cache übergeben wird (Sekunden)
MAX_AGE = {"history": 3600, "sol": 3600, "sales": 1800}


def get(url, tries=5, pause=1.3):
    """GET JSON mit Retry; Magic Eden erlaubt ca. 2 Anfragen pro Sekunde."""
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read())
            time.sleep(pause)
            return data
        except Exception as e:  # noqa: BLE001
            print(f"  Versuch {i + 1} fehlgeschlagen: {url} ({e})", file=sys.stderr)
            time.sleep(4 * (i + 1))
    raise RuntimeError(f"Konnte {url} nicht laden.")


def fresh(cache, name):
    entry = cache.get(name)
    return entry if entry and time.time() - entry["ts"] < MAX_AGE[name] else None


def fetch_sales(sym):
    sales = []
    for off in (0, 500):  # API liefert max. 500 pro Seite, Verkäufe reichen ca. 90 Tage zurück
        page = get(f"{ME}/collections/{sym}/activities?offset={off}&limit=500&type=buyNow")
        sales += [s for s in page if s.get("type") == "buyNow"]
        if len(page) < 500:
            break
    return sorted([[s["blockTime"] * 1000, round(s["price"], 4)] for s in sales])


def fetch_collection(c, cache):
    sym = c["symbol"]
    print(f"Magic Eden: {c['name']} …", file=sys.stderr)
    stats = get(f"{ME}/collections/{sym}/stats")
    listings = get(f"{ME}/collections/{sym}/listings?offset=0&limit=20&sort=listPrice&sort_direction=asc")
    cached = fresh(cache, "sales")
    sales = cached["data"][sym] if cached and sym in cached["data"] else fetch_sales(sym)
    prices = sorted(l["price"] for l in listings)
    floor = stats.get("floorPrice", 0) / 1e9
    return {
        "symbol": sym, "key": c["key"], "name": c["name"], "short": c["short"],
        "floor": floor,
        "listed": stats.get("listedCount", len(listings)),
        "avg24h": (stats.get("avgPrice24hr") or 0) / 1e9,
        "volume7d": (stats.get("volume7d") or 0) / 1e9,
        "ladder": [round(p, 4) for p in prices[:12]] or [floor],
        "sales": sales,
        "url": f"https://magiceden.io/marketplace/{sym}",
    }


def smooth(series, half=3):
    """Zentrierter 7-Tage-Median gegen Ausreißer einzelner Tage."""
    out = {}
    for k in series:
        t = dt.date.fromisoformat(k)
        w = [series[d] for d in ((t + dt.timedelta(days=i)).isoformat() for i in range(-half, half + 1)) if d in series]
        out[k] = statistics.median(w)
    return out


def value_at(series, day):
    keys = [k for k in series if k <= day]
    return series[max(keys)] if keys else None


def seasons(med_eur, today):
    """Jede Presale-Saison ab 1. Okt, indexiert auf den heutigen Kalendertag (= 100)."""
    sm = smooth(med_eur)
    base_md = today.strftime("%m-%d")
    out = []
    first = min(int(k[:4]) for k in sm)
    season_year = today.year if today.month >= 10 else today.year - 1
    for y in range(max(first, 2023), season_year + 1):
        start = dt.date(y, 10, 1)
        end = dt.date(y + 1, 1, 31)
        base_day = f"{y}-{base_md}" if base_md >= "10-01" else f"{y + 1}-{base_md}"
        base = value_at(sm, base_day)
        if not base:
            continue
        pts = []
        d = start
        while d <= min(end, today):
            v = sm.get(d.isoformat())
            if v:
                pts.append([(d - start).days, round(v / base * 100, 1), round(v)])
            d += dt.timedelta(days=1)
        pre = {k: v for k, v in sm.items() if base_day <= k <= f"{y}-12-03"}
        stats = None
        if y < season_year and pre:
            pk = max(pre, key=pre.get)
            post = {k: v for k, v in sm.items() if pk <= k <= f"{y + 1}-01-31"}
            lo = min(post, key=post.get)
            stats = {
                "base": round(base), "peak": round(pre[pk]), "peakDate": pk,
                "trough": round(post[lo]), "troughDate": lo,
                "jan31": round(value_at(sm, f"{y + 1}-01-31")),
            }
        out.append({"label": f"{y}/{str(y + 1)[2:]}", "year": y, "current": y == season_year,
                    "points": pts, "stats": stats})
    return out


def collect(cache=None):
    """Lädt alle Daten. `cache` (dict) wird gelesen und aktualisiert, damit häufige Läufe
    Historie, SOL-Kurs und Verkäufe nur stündlich bzw. halbstündlich neu laden."""
    cache = cache if cache is not None else {}
    now = dt.datetime.now(BERLIN)
    today = now.date()

    cols = [fetch_collection(c, cache) for c in COLLECTIONS]
    if not fresh(cache, "sales"):
        cache["sales"] = {"ts": time.time(), "data": {c["symbol"]: c["sales"] for c in cols}}

    hist = fresh(cache, "history")
    if not hist:
        print("tmlunite Historie …", file=sys.stderr)
        hist = {"ts": time.time(),
                "sol": get(f"{TML}/nft/history?range=all&currency=sol"),
                "eur": get(f"{TML}/nft/history?range=all&currency=eur")}
        cache["history"] = hist
    h_sol, h_eur = hist["sol"], hist["eur"]
    sol_price = h_eur.get("currentSolPrice") or {}

    sol = fresh(cache, "sol")
    if not sol:
        print("CoinGecko SOL-Kurs …", file=sys.stderr)
        try:
            sol = {"ts": time.time(),
                   "eur": get(f"{CG}/coins/solana/market_chart?vs_currency=eur&days=365&interval=daily")["prices"],
                   "usd": get(f"{CG}/coins/solana/market_chart?vs_currency=usd&days=365&interval=daily")["prices"]}
            cache["sol"] = sol
        except RuntimeError:
            if not cache.get("sol"):
                raise
            sol = cache["sol"]  # CoinGecko gedrosselt: letzten Stand weiterverwenden

    eur_by_day = {r["date"]: r for r in h_eur["history"]}
    history = []
    for r in h_sol["history"]:
        e = eur_by_day.get(r["date"], {})
        history.append([r["date"]] + [r.get(c["key"]) for c in cols] + [r.get("medallion"), e.get("medallion")])

    med_eur = {r[0]: r[5] for r in history if r[5]}
    eur_rate = sol_price.get("eur") or sol["eur"][-1][1]
    usd_rate = sol_price.get("usd") or sol["usd"][-1][1]
    cost_sol = sum(sum(c["ladder"][:MEDALLIONS_WANTED]) for c in cols)

    return {
        "generated": now.isoformat(timespec="minutes"),
        "today": today.isoformat(),
        "wanted": MEDALLIONS_WANTED,
        "sol": {"eur": eur_rate, "usd": usd_rate,
                "eurSeries": [[int(t), round(p, 2)] for t, p in sol["eur"]],
                "usdSeries": [[int(t), round(p, 2)] for t, p in sol["usd"]]},
        "collections": cols,
        "costSol": round(cost_sol, 2),
        "history": history,  # Datum, Letter, Reflection, Symbol, Medallion (SOL), Medallion (EUR)
        "seasons": seasons(med_eur, today),
    }


def write_csv(data, path):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["datum"] + [c["short"].lower() + "_sol" for c in data["collections"]] + ["medallion_sol", "medallion_eur"])
        w.writerows(data["history"])


# Lädt die Seite neu, sobald ein neuer Datenstand veröffentlicht ist (nur für die GitHub-Pages-Version).
LIVE_RELOAD = """
<script>
(() => {
  const mine = DATA.generated;
  setInterval(async () => {
    if (document.visibilityState !== "visible") return;
    try {
      const r = await fetch("data.json?t=" + Date.now(), { cache: "no-store" });
      if (r.ok && (await r.json()).generated !== mine) location.reload();
    } catch (e) { /* offline: beim nächsten Mal */ }
  }, 120000);
})();
</script>
"""


def render(data, path, live=False):
    with open(os.path.join(HERE, "dashboard_template.html"), encoding="utf-8") as f:
        html = f.read().replace("/*__DATA__*/null", json.dumps(data, separators=(",", ":")))
    if live:
        html = "<!doctype html>\n" + html + LIVE_RELOAD
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def main():
    data = collect()
    write_csv(data, os.path.join(HERE, "medallion_history.csv"))
    out = os.path.join(HERE, "tomorrowland_medallion.html")
    render(data, out)
    med = sum(c["floor"] for c in data["collections"])
    print(f"\nMedallion-Floor: {med:.2f} SOL  ·  {data['wanted']} Medaillons (günstigste Listings): "
          f"{data['costSol']:.2f} SOL ≈ {data['costSol'] * data['sol']['eur']:,.0f} €")
    print(f"Dashboard: {out}")


if __name__ == "__main__":
    main()
