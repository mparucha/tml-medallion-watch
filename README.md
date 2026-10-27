# Medaillon-Wächter

Beobachtet die drei Tomorrowland-NFT-Kollektionen, aus denen das *Medallion of Memoria* besteht
(A Letter from the Universe, The Reflection of Love, The Symbol of Love and Unity), und

- aktualisiert alle ~5 Minuten ein Dashboard auf GitHub Pages,
- schickt per [ntfy](https://ntfy.sh) einen Alarm aufs Handy, wenn ein Teil oder das ganze Medaillon
  deutlich unter dem 7-Tage-Median liegt,
- schickt jeden Abend eine Zusammenfassung mit Trend.

Alles läuft kostenlos in GitHub Actions. Datenquellen: Magic Eden API, tmlunite.com (Tageshistorie),
CoinGecko (SOL-Kurs). Keine Finanz- oder Anlageberatung.

## Einstellungen

Unter *Settings → Secrets and variables → Actions*:

| Name | Art | Bedeutung | Standard |
|---|---|---|---|
| `NTFY_TOPIC` | Secret | geheimer ntfy-Kanal | – |
| `ALERT_PCT` | Variable | Alarm ab so viel % unter dem 7-Tage-Median | 5 |
| `DAILY_HOUR` | Variable | Stunde der Tageszusammenfassung (Berlin) | 20 |
| `EXPECTED_SNAPSHOT` | Variable | vermuteter Snapshot-Termin | 2026-12-01 |
| `MEDALLIONS_WANTED` | Variable | so viele Medaillons sollen gekauft werden | 2 |

Testnachricht: *Actions → Medaillon-Wächter → Run workflow* mit Haken bei „Testnachricht“.

## Lokal

```bash
python3 tml_dashboard.py   # Dashboard als tomorrowland_medallion.html + CSV
python3 watch.py           # ein Wächter-Lauf, Nachrichten nur auf der Konsole
```

Nur Python-Standardbibliothek, keine Installation nötig.
