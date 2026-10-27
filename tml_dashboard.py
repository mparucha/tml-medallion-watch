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
MEDALLIONS_WANTED = int(os.environ.get("MEDALLIONS_WANTED") or 2)  # je Kollektion so viele NFTs kaufen
ALERT_PCT = float(os.environ.get("ALERT_PCT") or 5)  # Alarm ab so viel % unter dem 7-Tage-Median
# Termine wie im Vorjahr (Registrierung 1.–30.11.2025, Snapshot 1.12.2025), bis 2027 offiziell bestätigt ist
SNAPSHOT = dt.date.fromisoformat(os.environ.get("EXPECTED_SNAPSHOT") or "2026-12-01")
REG_START = dt.date.fromisoformat(os.environ.get("EXPECTED_REGISTRATION_START") or "2026-11-01")
REG_END = dt.date.fromisoformat(os.environ.get("EXPECTED_REGISTRATION_END") or "2026-11-30")

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


def de(v, d=0):
    """Zahl im deutschen Format."""
    s = f"{v:,.{d}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def pct(v):
    return "–" if v is None else ("+" if v >= 0.5 else "−" if v <= -0.5 else "±") + de(abs(v), 0) + " %"


def row_on(history, day, idx):
    """Wert der Tageshistorie am Tag `day` oder am letzten Tag davor."""
    rows = [r for r in history if r[0] <= day and r[idx] is not None]
    return rows[-1][idx] if rows else None


def median_7d(history, today, idx):
    lo = (today - dt.timedelta(days=7)).isoformat()
    vals = [r[idx] for r in history if lo <= r[0] < today.isoformat() and r[idx] is not None]
    return statistics.median(vals) if vals else None


def change(now, old):
    return (now / old - 1) * 100 if old else None


def status(cols, history, season_list, rate, wanted, today):
    """Die Lage auf einen Blick – dieselben Aussagen für Dashboard und Benachrichtigungen."""
    y1 = (today - dt.timedelta(days=1)).isoformat()
    y7 = (today - dt.timedelta(days=7)).isoformat()
    items = []
    for i, c in enumerate(cols):
        base = median_7d(history, today, i + 1)
        buy = c["ladder"][:wanted]
        items.append({
            "short": c["short"], "floor": c["floor"], "base7": base,
            "vs7": change(c["floor"], base), "ch7": change(c["floor"], row_on(history, y7, i + 1)),
            "threshold": base * (1 - ALERT_PCT / 100) if base else None,
            "buy": buy, "buySol": sum(buy), "listed": c["listed"], "scarce": c["listed"] < 30,
        })
    med_sol = sum(c["floor"] for c in cols)
    med_eur = med_sol * rate
    med_base = median_7d(history, today, 5)
    cost_sol = sum(it["buySol"] for it in items)
    ch1 = change(med_eur, row_on(history, y1, 5))
    ch7 = change(med_eur, row_on(history, y7, 5))
    vs7 = change(med_eur, med_base)
    trend = "steigend" if (ch7 or 0) >= 5 else "fallend" if (ch7 or 0) <= -5 else "stabil"

    past = [s["stats"] for s in season_list if s["stats"]]
    run_ups = [(p["peak"] / p["base"] - 1) * 100 for p in past]
    drops = [(p["trough"] / p["peak"] - 1) * 100 for p in past]
    days_snap = (SNAPSHOT - today).days
    days_reg = (REG_END - today).days

    # Die letzten 7 Tage (Tagesstände) plus heute live
    by_day = {r[0]: r for r in history}
    week = []
    for k in range(7, 0, -1):
        d = (today - dt.timedelta(days=k)).isoformat()
        if d in by_day and by_day[d][5]:
            r = by_day[d]
            week.append({"date": d, "floors": r[1:4], "medSol": r[4], "medEur": r[5], "live": False})
    week.append({"date": today.isoformat(), "floors": [c["floor"] for c in cols],
                 "medSol": med_sol, "medEur": med_eur, "live": True})
    lo = min(week, key=lambda w: w["medEur"])
    hi = max(week, key=lambda w: w["medEur"])
    if hi["live"]:
        rank = "heute teuerster Tag der Woche"
    elif lo["live"]:
        rank = "heute günstigster Tag der Woche"
    else:
        rank = f"heute {de(abs(change(med_eur, hi['medEur'])))} % unter dem Wochenhoch"
    mover = max(items, key=lambda it: abs(it["ch7"] or 0))

    cheap = [it for it in items if it["vs7"] is not None and it["vs7"] <= -ALERT_PCT]
    if days_snap < 0:
        level, head = "neutral", "Snapshot vorbei"
        text = "Der vermutete Snapshot-Termin ist vorbei. Trage den nächsten Termin in den Einstellungen ein."
    elif cheap:
        level, head = "good", "Günstiger Moment"
        text = ", ".join(f"{it['short']} {pct(it['vs7'])}" for it in cheap) + " unter dem 7-Tage-Schnitt."
    elif trend == "steigend":
        level, head = "warn", "Preise steigen"
        text = (f"Medaillon {pct(ch7)} in 7 Tagen. In den Vorjahren stieg der Preis bis zum Snapshot weiter "
                f"({pct(min(run_ups))} bis {pct(max(run_ups))}).") if run_ups else f"Medaillon {pct(ch7)} in 7 Tagen."
    elif trend == "fallend":
        level, head = "good", "Preise fallen"
        text = f"Medaillon {pct(ch7)} in 7 Tagen."
    else:
        level, head = "neutral", "Preise stabil"
        text = f"Medaillon {pct(ch7)} in 7 Tagen, kaum Bewegung."

    return {
        "level": level, "headline": head, "text": text, "trend": trend,
        "medSol": med_sol, "medEur": med_eur, "medBase7": med_base,
        "medThreshold": med_base * (1 - ALERT_PCT / 100) if med_base else None,
        "costSol": cost_sol, "costEur": cost_sol * rate,
        "ch1": ch1, "ch7": ch7, "vs7": vs7, "items": items,
        "alertPct": ALERT_PCT, "runUps": run_ups, "drops": drops,
        "snapshot": SNAPSHOT.isoformat(), "regStart": REG_START.isoformat(), "regEnd": REG_END.isoformat(),
        "daysSnap": days_snap, "daysReg": days_reg, "urgent": 0 <= days_reg <= 7,
        "week": {"days": week, "low": {"date": lo["date"], "eur": lo["medEur"]},
                 "high": {"date": hi["date"], "eur": hi["medEur"]}, "rank": rank,
                 "mover": {"short": mover["short"], "ch7": mover["ch7"]}},
    }


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
    season_list = seasons(med_eur, today)
    st = status(cols, history, season_list, eur_rate, MEDALLIONS_WANTED, today)

    return {
        "generated": now.isoformat(timespec="minutes"),
        "today": today.isoformat(),
        "wanted": MEDALLIONS_WANTED,
        "sol": {"eur": eur_rate, "usd": usd_rate,
                "eurSeries": [[int(t), round(p, 2)] for t, p in sol["eur"]],
                "usdSeries": [[int(t), round(p, 2)] for t, p in sol["usd"]]},
        "collections": cols,
        "costSol": round(st["costSol"], 2),
        "history": history,  # Datum, Letter, Reflection, Symbol, Medallion (SOL), Medallion (EUR)
        "seasons": season_list,
        "status": st,
        "pagesUrl": os.environ.get("PAGES_URL", ""),
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


# Öffentliche Version: Schriften und Chart.js aus assets/, damit Besucher keine Anfragen an Dritte schicken
LOCAL_FONTS = '<link rel="stylesheet" href="assets/fonts.css">\n<meta name="robots" content="noindex, nofollow">'
LOCAL_CHARTJS = '<script src="assets/chart.umd.min.js"></script>'
LEGAL = ('<span>Privates, nicht-kommerzielles Projekt. Keine Cookies, kein Tracking, keine Anfragen an Dritte. '
         'Gehostet bei GitHub Pages; GitHub protokolliert dabei IP-Adressen, siehe '
         '<a href="https://docs.github.com/de/site-policy/privacy-policies/github-general-privacy-statement" '
         'target="_blank" rel="noopener">GitHub-Datenschutzerklärung</a>.</span>')


def swap(html, marker, replacement):
    start, end = f"<!--{marker}-->", f"<!--/{marker}-->"
    a, b = html.index(start), html.index(end) + len(end)
    return html[:a] + replacement + html[b:]


def render(data, path, live=False):
    with open(os.path.join(HERE, "dashboard_template.html"), encoding="utf-8") as f:
        html = f.read().replace("/*__DATA__*/null", json.dumps(data, separators=(",", ":")))
    if live:
        html = swap(html, "FONTS", LOCAL_FONTS)
        html = swap(html, "CHARTJS", LOCAL_CHARTJS)
        html = html.replace("<!--LEGAL-->", LEGAL)
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
