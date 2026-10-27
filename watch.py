#!/usr/bin/env python3
"""Preis-Wächter für das Medallion of Memoria (läuft alle 5 Minuten in GitHub Actions).

- baut das Dashboard für GitHub Pages neu (site/index.html)
- schickt per ntfy einen Alarm, wenn ein Teil oder das ganze Medaillon deutlich unter dem 7-Tage-Median liegt
- schickt einmal am Tag (ab DAILY_HOUR Uhr, Berlin) eine Zusammenfassung mit Trend

Umgebungsvariablen:
  NTFY_TOPIC         geheimer ntfy-Kanal (ohne: Nachrichten nur auf der Konsole)
  ALERT_PCT          Alarm ab so viel Prozent unter dem 7-Tage-Median (Standard 5)
  DAILY_HOUR         Stunde der Tageszusammenfassung, Berlin-Zeit (Standard 20)
  EXPECTED_SNAPSHOT  vermuteter Snapshot-Termin (Standard 2026-12-01, wie im Vorjahr)
  PAGES_URL          Link zum Dashboard, wird in Nachrichten verlinkt
  FORCE_TEST         "true": Testnachricht + Tageszusammenfassung sofort senden
"""
import csv
import datetime as dt
import json
import os
import statistics
import sys
import time
import urllib.request

import tml_dashboard as tml

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state.json")
SITE = os.path.join(HERE, "site")
DAILY_CSV = os.path.join(HERE, "data", "daily.csv")

TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
ALERT_PCT = float(os.environ.get("ALERT_PCT") or 5)
DAILY_HOUR = int(os.environ.get("DAILY_HOUR") or 20)
SNAPSHOT = dt.date.fromisoformat(os.environ.get("EXPECTED_SNAPSHOT") or "2026-12-01")
PAGES_URL = os.environ.get("PAGES_URL", "")
FORCE_TEST = os.environ.get("FORCE_TEST", "").lower() == "true"
REALERT_PCT = 3      # erneuter Alarm erst, wenn der Preis noch einmal so viel tiefer liegt …
REALERT_HOURS = 12   # … oder diese Zeit vergangen ist


def de(v, d=0):
    s = f"{v:,.{d}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def pct(v):
    return ("+" if v > 0 else "−" if v < 0 else "±") + de(abs(v), 0) + " %"


def send(title, message, priority=3, tags=(), click=""):
    print(f"\n[{title}]\n{message}")
    if not TOPIC:
        return
    body = json.dumps({"topic": TOPIC, "title": title, "message": message, "priority": priority,
                       "tags": list(tags), "click": click or PAGES_URL}).encode()
    req = urllib.request.Request("https://ntfy.sh/", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        r.read()


def row_on(history, day, idx):
    """Wert der Tageshistorie am Tag `day` oder am letzten Tag davor."""
    rows = [r for r in history if r[0] <= day and r[idx] is not None]
    return rows[-1][idx] if rows else None


def median_7d(history, today, idx):
    lo = (today - dt.timedelta(days=7)).isoformat()
    vals = [r[idx] for r in history if lo <= r[0] < today.isoformat() and r[idx] is not None]
    return statistics.median(vals) if vals else None


def sol_change(series, days):
    last_t, last = series[-1]
    prev = [p for t, p in series if t <= last_t - days * 864e5]
    return (last / prev[-1] - 1) * 100 if prev else 0.0


def check_alerts(data, state, today):
    rate = data["sol"]["eur"]
    hist = data["history"]
    alerts = state.setdefault("alerts", {})
    items = [(c["short"], c["floor"], median_7d(hist, today, i + 1), "SOL", c) for i, c in enumerate(data["collections"])]
    med_now = sum(c["floor"] for c in data["collections"]) * rate
    items.append(("Medaillon", med_now, median_7d(hist, today, 5), "€", None))

    for name, now, base, unit, col in items:
        if not base:
            continue
        drop = (now / base - 1) * 100
        if drop > -ALERT_PCT:
            continue
        last = alerts.get(name)
        if last and time.time() - last["ts"] < REALERT_HOURS * 3600 and now > last["price"] * (1 - REALERT_PCT / 100):
            continue
        if col:
            two = col["ladder"][:data["wanted"]]
            msg = (f"Floor {de(now, 2)} SOL ≈ {de(now * rate)} € · 7-Tage-Median {de(base, 2)} SOL\n"
                   f"Günstigste {len(two)}: {' + '.join(de(p, 2) for p in two)} SOL · {col['listed']} gelistet")
            click = col["url"]
        else:
            cost = data["costSol"]
            msg = (f"Medaillon {de(now)} € · 7-Tage-Median {de(base)} €\n"
                   f"{data['wanted']} Medaillons kaufen: {de(cost, 1)} SOL ≈ {de(cost * rate)} €")
            click = PAGES_URL
        send(f"↓ {name} {pct(drop)} unter 7-Tage-Mittel", msg, priority=4, tags=["chart_with_downwards_trend"], click=click)
        alerts[name] = {"price": now, "ts": time.time()}


def daily_summary(data, today):
    rate = data["sol"]["eur"]
    hist = data["history"]
    cols = data["collections"]
    y1 = (today - dt.timedelta(days=1)).isoformat()
    y7 = (today - dt.timedelta(days=7)).isoformat()
    med_sol = sum(c["floor"] for c in cols)
    med_eur = med_sol * rate
    ch1 = (med_eur / row_on(hist, y1, 5) - 1) * 100
    ch7 = (med_eur / row_on(hist, y7, 5) - 1) * 100
    trend = "steigend" if ch7 >= 5 else "fallend" if ch7 <= -5 else "seitwärts"

    lines = [f"Medaillon: {de(med_sol, 1)} SOL ≈ {de(med_eur)} € (24h {pct(ch1)} · 7T {pct(ch7)})",
             f"{data['wanted']} Medaillons kaufen: {de(data['costSol'], 1)} SOL ≈ {de(data['costSol'] * rate)} €"]
    for i, c in enumerate(cols):
        old = row_on(hist, y7, i + 1)
        lines.append(f"{c['short']} {de(c['floor'], 2)} SOL (7T {pct((c['floor'] / old - 1) * 100) if old else '–'}, {c['listed']} gelistet)")
    s = data["sol"]["eurSeries"]
    lines.append(f"SOL {de(rate, 2)} € (24h {pct(sol_change(s, 1))} · 7T {pct(sol_change(s, 7))})")

    past = [x["stats"] for x in data["seasons"] if x["stats"]]
    drops = [(p["trough"] / p["peak"] - 1) * 100 for p in past]
    days = (SNAPSHOT - today).days
    if days > 0:
        lines.append(f"Noch {days} Tage bis zum vermuteten Snapshot ({SNAPSHOT:%d.%m.}). "
                     f"Vorjahre: bis dahin steigend, danach {pct(max(drops))} bis {pct(min(drops))}.")
    elif today.month in (12, 1):
        lines.append("Nach-Snapshot-Phase: in den Vorjahren lag hier das Tief (Dez–Jan).")

    send(f"Medaillon-Update {today:%d.%m.} · Trend {trend}", "\n".join(lines), tags=["bar_chart"])
    return [today.isoformat(), round(med_sol, 3), round(med_eur), data["costSol"], round(data["costSol"] * rate),
            *[round(c["floor"], 4) for c in cols], round(rate, 2), *[c["listed"] for c in cols]]


def append_daily(row):
    os.makedirs(os.path.dirname(DAILY_CSV), exist_ok=True)
    new = not os.path.exists(DAILY_CSV)
    with open(DAILY_CSV, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["datum", "medallion_sol", "medallion_eur", "kosten_sol", "kosten_eur",
                        "letter_sol", "reflection_sol", "symbol_sol", "sol_eur",
                        "letter_gelistet", "reflection_gelistet", "symbol_gelistet"])
        w.writerow(row)


def set_output(ok):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as f:
            f.write(f"ok={'true' if ok else 'false'}\n")


def main():
    try:
        state = json.load(open(STATE))
    except (OSError, ValueError):
        state = {}
    cache = state.setdefault("cache", {})

    try:
        data = tml.collect(cache)
    except Exception as e:  # noqa: BLE001  – eine Quelle hakt: nächster Lauf versucht es wieder
        print(f"Daten nicht verfügbar: {e}", file=sys.stderr)
        json.dump(state, open(STATE, "w"))
        set_output(False)
        return

    os.makedirs(SITE, exist_ok=True)
    tml.render(data, os.path.join(SITE, "index.html"), live=True)
    json.dump({"generated": data["generated"]}, open(os.path.join(SITE, "data.json"), "w"))
    tml.write_csv(data, os.path.join(SITE, "medallion_history.csv"))

    now = dt.datetime.now(tml.BERLIN)
    today = now.date()
    if FORCE_TEST:
        send("Test: Medaillon-Wächter verbunden ✓",
             f"Alarm ab {de(ALERT_PCT)} % unter dem 7-Tage-Median, Tageszusammenfassung um {DAILY_HOUR}:00 Uhr.",
             tags=["white_check_mark"])
        daily_summary(data, today)
    check_alerts(data, state, today)
    if now.hour >= DAILY_HOUR and state.get("lastDaily") != today.isoformat():
        append_daily(daily_summary(data, today))
        state["lastDaily"] = today.isoformat()

    json.dump(state, open(STATE, "w"))
    set_output(True)


if __name__ == "__main__":
    main()
