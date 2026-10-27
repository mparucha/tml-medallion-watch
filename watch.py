#!/usr/bin/env python3
"""Preis-Wächter für das Medallion of Memoria (läuft alle 5 Minuten in GitHub Actions).

- baut das Dashboard für GitHub Pages neu (site/index.html)
- schickt per ntfy einen Alarm, wenn ein Teil oder das ganze Medaillon deutlich unter dem 7-Tage-Median liegt
- schickt einmal am Tag (ab DAILY_HOUR Uhr, Berlin) eine kurze Lage mit Trend

Umgebungsvariablen (zusätzlich zu denen in tml_dashboard.py):
  NTFY_TOPIC   geheimer ntfy-Kanal (ohne: Nachrichten nur auf der Konsole)
  DAILY_HOUR   Stunde der Tageszusammenfassung, Berlin-Zeit (Standard 20)
  PAGES_URL    Link zum Dashboard, steckt in jeder Nachricht
  FORCE_TEST   "true": Testnachricht + Tageszusammenfassung sofort senden
"""
import csv
import datetime as dt
import json
import os
import shutil
import sys
import time
import urllib.request

import tml_dashboard as tml
from tml_dashboard import de, pct

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state.json")
SITE = os.path.join(HERE, "site")
DAILY_CSV = os.path.join(HERE, "data", "daily.csv")

TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
DAILY_HOUR = int(os.environ.get("DAILY_HOUR") or 20)
PAGES_URL = os.environ.get("PAGES_URL", "")
FORCE_TEST = os.environ.get("FORCE_TEST", "").lower() == "true"
REALERT_PCT = 3      # erneuter Alarm erst, wenn der Preis noch einmal so viel tiefer liegt …
REALERT_HOURS = 12   # … oder diese Zeit vergangen ist


def send(title, message, priority=3, tags=(), market_url=""):
    """ntfy-Nachricht: Tippen öffnet das Dashboard, Buttons für Dashboard und Magic Eden."""
    print(f"\n[{title}]\n{message}")
    if not TOPIC:
        return
    actions = []
    if PAGES_URL:
        actions.append({"action": "view", "label": "Dashboard", "url": PAGES_URL, "clear": True})
    if market_url:
        actions.append({"action": "view", "label": "Magic Eden", "url": market_url})
    body = json.dumps({"topic": TOPIC, "title": title, "message": message, "priority": priority,
                       "tags": list(tags), "click": PAGES_URL, "actions": actions}).encode()
    req = urllib.request.Request("https://ntfy.sh/", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        r.read()


def deadline(st):
    if st["daysSnap"] < 0:
        return "Snapshot-Termin vorbei"
    if 0 <= st["daysReg"] <= 7:
        return f"Nur noch {st['daysReg']} Tage bis Registrierungsschluss ({tml.REG_END:%d.%m.})"
    return f"Noch {st['daysSnap']} Tage bis Snapshot ({tml.SNAPSHOT:%d.%m.})"


def check_alerts(data, state):
    st = data["status"]
    rate = data["sol"]["eur"]
    alerts = state.setdefault("alerts", {})
    cols = {c["short"]: c for c in data["collections"]}
    checks = [(it["short"], it["floor"], it["vs7"], it) for it in st["items"]]
    checks.append(("Medaillon", st["medEur"], st["vs7"], None))

    for name, now, vs7, it in checks:
        if vs7 is None or vs7 > -st["alertPct"]:
            continue
        last = alerts.get(name)
        if last and time.time() - last["ts"] < REALERT_HOURS * 3600 and now > last["price"] * (1 - REALERT_PCT / 100):
            continue
        if it:
            n = len(it["buy"])
            title = f"↓ {name} {de(now, 2)} SOL · {de(abs(vs7))} % unter Schnitt"
            lines = [f"{n} Stück jetzt {de(it['buySol'], 2)} SOL ≈ {de(it['buySol'] * rate)} €"
                     + (f" · nur {it['listed']} gelistet" if it["scarce"] else "")]
            market = cols[name]["url"]
        else:
            title = f"↓ Medaillon {de(now)} € · {de(abs(vs7))} % unter Schnitt"
            lines = [f"{data['wanted']} Medaillons jetzt {de(st['costEur'])} € ({de(st['costSol'], 1)} SOL)"]
            market = ""
        lines.append(deadline(st))
        send(title, "\n".join(lines), priority=4, tags=["chart_with_downwards_trend"], market_url=market)
        alerts[name] = {"price": now, "ts": time.time()}


def daily_summary(data, today):
    st = data["status"]
    items = st["items"]
    title = f"{data['wanted']} Medaillons: {de(st['costEur'])} € · {st['headline']}"
    if st["urgent"]:
        title = "⚠ " + title
    lines = [
        f"7 T {pct(st['ch7'])} · 24 h {pct(st['ch1'])} · {deadline(st)}",
        " · ".join(f"{it['short']} {de(it['floor'], 2)}" for it in items) + " SOL",
        "Alarm unter " + " / ".join(de(it["threshold"], 2) for it in items if it["threshold"]) + " SOL",
        st["text"],
    ]
    send(title, "\n".join(lines), tags=["bar_chart"])
    rate = data["sol"]["eur"]
    return [today.isoformat(), round(st["medSol"], 3), round(st["medEur"]), round(st["costSol"], 2), round(st["costEur"]),
            *[round(it["floor"], 4) for it in items], round(rate, 2), *[it["listed"] for it in items]]


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

    shutil.copytree(os.path.join(HERE, "assets"), os.path.join(SITE, "assets"), dirs_exist_ok=True)
    tml.render(data, os.path.join(SITE, "index.html"), live=True)
    json.dump({"generated": data["generated"]}, open(os.path.join(SITE, "data.json"), "w"))
    tml.write_csv(data, os.path.join(SITE, "medallion_history.csv"))

    now = dt.datetime.now(tml.BERLIN)
    today = now.date()
    daily_due = now.hour >= DAILY_HOUR and state.get("lastDaily") != today.isoformat()
    if FORCE_TEST:
        st = data["status"]
        send("Test: Medaillon-Wächter verbunden ✓",
             f"Alarm ab {de(st['alertPct'])} % unter dem 7-Tage-Schnitt, Lage täglich um {DAILY_HOUR}:00 Uhr.",
             tags=["white_check_mark"])
        if not daily_due:
            daily_summary(data, today)
    check_alerts(data, state)
    if daily_due:
        append_daily(daily_summary(data, today))
        state["lastDaily"] = today.isoformat()

    json.dump(state, open(STATE, "w"))
    set_output(True)


if __name__ == "__main__":
    main()
