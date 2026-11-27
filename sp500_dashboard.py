#!/usr/bin/env python3
"""
S&P-500-Dashboard: zieht kostenlose Daten und bewertet sie als Ampel.
🟢 positiv | 🟡 neutral | 🔴 negativ

Installation:  pip install yfinance pandas requests
Start:         python sp500_dashboard.py            (schnell)
               python sp500_dashboard.py --breite   (inkl. Marktbreite, ca. 30-60 s)

Hinweis: Schwellenwerte sind Faustregeln, keine Handelssignale.
"""
import io
import sys
import time

import pandas as pd
import requests
import yfinance as yf

GRUEN, GELB, ROT = 1, 0, -1
SYMBOL = {GRUEN: "🟢", GELB: "🟡", ROT: "🔴"}
UA = {"User-Agent": "Mozilla/5.0"}

ergebnisse = []  # (Name, Wert-Text, Ampel, Hinweis)


def add(name, wert, ampel, hinweis=""):
    ergebnisse.append((name, wert, ampel, hinweis))


def kurs(ticker, period="2y"):
    return yf.Ticker(ticker).history(period=period)["Close"].dropna()


def fred(serie):
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={serie}"
    fehler = None
    for versuch in range(3):  # FRED antwortet von Servern teils langsam
        try:
            r = requests.get(url, headers=UA, timeout=(10, 45))
            r.raise_for_status()
            break
        except requests.RequestException as e:
            fehler = e
            time.sleep(3 * (versuch + 1))
    else:
        raise fehler
    df = pd.read_csv(io.StringIO(r.text), index_col=0, parse_dates=True)
    return pd.to_numeric(df.iloc[:, 0], errors="coerce").dropna()


NAMEN = {
    "trend": "Trend S&P 500", "volatilitaet": "VIX", "breite_etf": "RSP/SPY",
    "kreditspread": "High-Yield-Spread", "zinsen": "10y-Rendite (US)",
    "zinskurve": "Zinskurve", "zinserwartung": "Zinserwartung", "fear_greed": "CNN Fear & Greed",
    "marktbreite_200": "Aktien über 200T-Linie",
}


def sicher(func):
    """Ein Ausfall einer Quelle soll nicht das ganze Skript stoppen."""
    def wrapper(*a, **k):
        try:
            func(*a, **k)
        except Exception as e:
            add(NAMEN.get(func.__name__, func.__name__), "n/a", None,
                f"Datenquelle nicht erreichbar ({type(e).__name__})")
    return wrapper


@sicher
def trend():
    s = kurs("^GSPC")
    last, ma50, ma200 = s.iloc[-1], s.rolling(50).mean().iloc[-1], s.rolling(200).mean().iloc[-1]
    if last > ma50 and last > ma200:
        a = GRUEN
    elif last > ma200:
        a = GELB
    else:
        a = ROT
    add("Trend S&P 500", f"{last:,.0f} (50T {ma50:,.0f} / 200T {ma200:,.0f})", a,
        "über beiden Linien = intakt")


@sicher
def volatilitaet():
    vix = kurs("^VIX", "6mo").iloc[-1]
    a = GRUEN if vix < 20 else GELB if vix < 30 else ROT
    add("VIX", f"{vix:.1f}", a, "<20 ruhig, >30 Panik")

    vix3m = kurs("^VIX3M", "6mo").iloc[-1]
    q = vix / vix3m
    a = GRUEN if q < 0.9 else GELB if q <= 1.0 else ROT
    add("VIX-Terminstruktur", f"VIX/VIX3M = {q:.2f}", a, ">1 = Backwardation (Stress)")


@sicher
def breite_etf():
    ratio = (kurs("RSP") / kurs("SPY")).dropna()
    abw = ratio.iloc[-1] / ratio.rolling(50).mean().iloc[-1] - 1
    a = GRUEN if abw > 0 else GELB if abw > -0.01 else ROT
    add("RSP/SPY (Equal vs. Cap)", f"{abw:+.1%} ggü. 50T-Schnitt", a,
        "relativ zu SPY; im Abschwung kein Breitenbeleg")


@sicher
def kreditspread():
    try:
        s = fred("BAMLH0A0HYM2")
    except Exception:
        # Ersatz über Yahoo: HYG/IEF fällt, wenn sich Kreditspreads ausweiten
        r = (kurs("HYG", "6mo") / kurs("IEF", "6mo")).dropna()
        chg = r.iloc[-1] / r.iloc[-21] - 1
        a = GRUEN if chg >= 0 else GELB if chg > -0.01 else ROT
        add("High-Yield-Spread (Ersatz)", f"HYG/IEF {chg:+.1%} in 20 Tagen", a,
            "fällt = Spreads weiten sich aus (FRED nicht erreichbar)")
        return
    lvl, chg = s.iloc[-1], s.iloc[-1] - s.iloc[-21]
    if lvl > 5 or chg > 0.5:
        a = ROT
    elif chg <= 0:
        a = GRUEN
    else:
        a = GELB
    add("High-Yield-Spread", f"{lvl:.2f} % ({chg:+.2f} in 20 Tagen)", a, "steigend = Frühwarnsignal")


@sicher
def zinsen():
    y = kurs("^TNX", "6mo")
    lvl, chg = y.iloc[-1], y.iloc[-1] - y.iloc[-21]
    if chg >= 0.4 or lvl > 5.0:
        a = ROT
    elif chg >= 0.2 or lvl > 4.5:
        a = GELB
    else:
        a = GRUEN
    add("10y-Rendite (US)", f"{lvl:.2f} % ({chg:+.2f} in 20 Tagen)", a,
        "hohes Niveau (>4,5 %) oder schneller Anstieg belastet")


@sicher
def zinskurve():
    try:
        k = fred("T10Y2Y").iloc[-1]
        name, hint = "Zinskurve 10y-2y", "<0 = invertiert"
    except Exception:
        # Ersatz über Yahoo: 10y minus 5y
        k = kurs("^TNX", "1mo").iloc[-1] - kurs("^FVX", "1mo").iloc[-1]
        name, hint = "Zinskurve 10y-5y (Ersatz)", "<0 = invertiert (FRED nicht erreichbar)"
    a = GRUEN if k > 0 else GELB if k > -0.5 else ROT
    add(name, f"{k:+.2f} %", a, hint)


@sicher
def zinserwartung():
    """Neutrale Info-Kachel (keine Ampel): 2y-Rendite vs. effektiver Leitzins."""
    y2 = fred("DGS2").iloc[-1]
    ff = fred("DFF").iloc[-1]
    d = y2 - ff
    if d < -0.25:
        hint = "2y unter Leitzins: Markt preist Zinssenkungen ein"
    elif d > 0.25:
        hint = "2y über Leitzins: Markt preist Zinsanhebungen ein"
    else:
        hint = "2y nahe Leitzins: Markt erwartet stabile Zinsen"
    add("Zinserwartung", f"2y {y2:.2f} % vs. Leitzins {ff:.2f} % ({d:+.2f})", None, hint)


@sicher
def fear_greed():
    url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
    headers = {
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://edition.cnn.com",
        "Referer": "https://edition.cnn.com/",
    }
    for versuch in range(2):
        r = requests.get(url, headers=headers, timeout=20)
        if r.status_code == 200:
            break
        time.sleep(3)
    r.raise_for_status()
    fg = r.json()["fear_and_greed"]
    score = float(fg["score"])
    FG["score"], FG["daten"] = score, fg
    # Extreme Angst = oft Kontra-Chance, aber kein "grünes" Umfeld -> gelb
    a = GELB if score < 25 else GRUEN if score <= 75 else ROT
    add("CNN Fear & Greed", f"{score:.0f} ({fg['rating']})", a,
        "<25 Angst (Kontra-Chance), >75 Gier (Risiko)")


@sicher
def marktbreite_200():
    tab = pd.read_html(io.StringIO(requests.get(
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        headers=UA, timeout=20).text))[0]
    ticker = tab["Symbol"].str.replace(".", "-", regex=False).tolist()
    df = yf.download(ticker, period="1y", progress=False, auto_adjust=True)["Close"]
    ueber = (df.iloc[-1] > df.rolling(200).mean().iloc[-1]).dropna()
    anteil = ueber.mean() * 100
    a = GRUEN if anteil > 60 else GELB if anteil >= 40 else ROT
    add("Aktien über 200T-Linie", f"{anteil:.0f} %", a, ">60 % breit, <40 % schwach")


TREIBER = []  # (Titel, [(Text, Link oder None), ...])
SEKTOREN = {
    "XLK": "Technologie", "XLF": "Finanzen", "XLV": "Gesundheit", "XLY": "Zykl. Konsum",
    "XLP": "Basiskonsum", "XLE": "Energie", "XLI": "Industrie", "XLB": "Rohstoffe",
    "XLU": "Versorger", "XLRE": "Immobilien", "XLC": "Kommunikation",
}
SCHWERGEWICHTE = ["NVDA", "MSFT", "AAPL", "AMZN", "GOOGL", "META",
                  "AVGO", "TSLA", "BRK-B", "JPM", "LLY", "V"]
WT = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
FEEDS = {
    "CNBC": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "Yahoo Finance": "https://finance.yahoo.com/news/rssindex",
    "MarketWatch": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
}


def jetzt():
    return pd.Timestamp.now(tz="Europe/Berlin")


def prozent(x):
    return f"{x:+.2f} %"


def us_handel_offen():
    n = pd.Timestamp.now(tz="America/New_York")
    return n.weekday() < 5 and (9, 30) <= (n.hour, n.minute) < (16, 0)


def treiber_abschnitt(titel):
    """Fängt Fehler pro Abschnitt ab; ein Ausfall stoppt nicht den Rest."""
    def deko(func):
        def wrapper():
            try:
                zeilen = func()
                if zeilen:
                    TREIBER.append((titel, zeilen))
            except Exception as e:
                TREIBER.append((titel, [(f"nicht verfügbar ({type(e).__name__})", None)]))
        return wrapper
    return deko


@treiber_abschnitt("Marktbild")
def marktbild():
    s = kurs("^GSPC", "1mo")
    chg = (s.iloc[-1] / s.iloc[-2] - 1) * 100
    status = "US-Handel läuft" if us_handel_offen() else "US-Handel geschlossen (letzte Sitzung)"
    zeilen = [(f"S&P 500 {s.iloc[-1]:,.0f} ({prozent(chg)}) – {status}", None)]
    try:
        f = kurs("ES=F", "1mo")
        zeilen.append((f"S&P-Futures {prozent((f.iloc[-1] / f.iloc[-2] - 1) * 100)}", None))
    except Exception:
        pass
    return zeilen


@treiber_abschnitt("Sektoren & Schwergewichte")
def sektoren_schwergewichte():
    alle = list(SEKTOREN) + SCHWERGEWICHTE
    df = yf.download(alle, period="5d", progress=False, auto_adjust=True)["Close"].ffill()
    chg = (df.iloc[-1] / df.iloc[-2] - 1) * 100

    sek = chg[list(SEKTOREN)].dropna().sort_values(ascending=False)

    def namen(ser):
        return ", ".join(f"{SEKTOREN[k]} {prozent(v)}" for k, v in ser.items())

    zeilen = [("Stärkste Sektoren: " + namen(sek.head(3)), None),
              ("Schwächste Sektoren: " + namen(sek.tail(3).iloc[::-1]), None)]

    sg = chg[SCHWERGEWICHTE].dropna()
    caps = {}
    for k in sg.index:
        try:
            caps[k] = float(yf.Ticker(k).fast_info["marketCap"])
        except Exception:
            pass
    if len(caps) == len(sg):  # Beitrag grob nach Marktkapitalisierung gewichten
        summe = sum(caps.values())
        beitrag = pd.Series({k: sg[k] * caps[k] / summe for k in sg.index})
        hinweis = " (nach Gewicht)"
    else:
        beitrag, hinweis = sg, ""
    plus = beitrag[beitrag > 0].sort_values(ascending=False).head(3)
    minus = beitrag[beitrag < 0].sort_values().head(3)

    def fmt(ser):
        return ", ".join(f"{k} {prozent(sg[k])}" for k in ser.index)

    if len(plus):
        zeilen.append((f"Schwergewichte stützen{hinweis}: {fmt(plus)}", None))
    if len(minus):
        zeilen.append((f"Schwergewichte belasten{hinweis}: {fmt(minus)}", None))
    return zeilen


@treiber_abschnitt("Makro & Märkte")
def makro():
    zeilen = []
    for tk, name in {"^TNX": "10y-Rendite", "DX-Y.NYB": "US-Dollar-Index",
                     "CL=F": "Öl (WTI)", "GC=F": "Gold", "^VIX": "VIX"}.items():
        try:
            s = kurs(tk, "1mo")
            letzt, vor = s.iloc[-1], s.iloc[-2]
            if tk == "^TNX":
                zeilen.append((f"{name} {letzt:.2f} % ({(letzt - vor) * 100:+.0f} bp)", None))
            else:
                zeilen.append((f"{name} {letzt:,.2f} ({prozent((letzt / vor - 1) * 100)})", None))
        except Exception:
            continue
    return zeilen


@treiber_abschnitt("Termine: US-Konjunkturdaten (heute/morgen)")
def termine():
    r = requests.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json",
                     headers=UA, timeout=20)
    r.raise_for_status()
    heute, jz = jetzt().normalize(), jetzt()
    liste = []
    for e in r.json():
        if e.get("country") != "USD" or e.get("impact") not in ("High", "Medium"):
            continue
        z = pd.Timestamp(e["date"])
        z = z.tz_localize("America/New_York") if z.tzinfo is None else z
        z = z.tz_convert("Europe/Berlin")
        if 0 <= (z.normalize() - heute).days <= 1:
            liste.append((z, e))
    zeilen = []
    for z, e in sorted(liste, key=lambda x: x[0])[:12]:
        tag = "Heute" if z.normalize() == heute else "Morgen"
        punkt = "🔴" if e["impact"] == "High" else "🟠"
        text = f"{'✓ ' if z < jz else ''}{tag} {z:%H:%M} {punkt} {e['title']}"
        teile = []
        if e.get("forecast"):
            teile.append(f"Prognose {e['forecast']}")
        if e.get("previous"):
            teile.append(f"zuvor {e['previous']}")
        if teile:
            text += f" ({', '.join(teile)})"
        zeilen.append((text, None))
    return zeilen or [("keine wichtigen US-Termine heute/morgen", None)]


@treiber_abschnitt("Quartalszahlen der Schwergewichte (7 Tage)")
def earnings():
    heute = pd.Timestamp.now().normalize()
    funde, fehler = [], 0
    for k in SCHWERGEWICHTE:
        try:
            c = yf.Ticker(k).calendar
            ed = c.get("Earnings Date") if isinstance(c, dict) else None
            if not ed:
                continue
            d = pd.Timestamp(ed[0])
            if heute <= d <= heute + pd.Timedelta(days=7):
                funde.append((d, k))
        except Exception:
            fehler += 1
    if fehler == len(SCHWERGEWICHTE):
        raise RuntimeError("keine Daten")
    funde.sort()
    if not funde:
        return [("keine bekannten Termine in den nächsten 7 Tagen", None)]
    return [(f"{WT[d.weekday()]} {d:%d.%m.} {k}", None) for d, k in funde]


@treiber_abschnitt("Schlagzeilen (englisch, Link öffnet Artikel)")
def news():
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime

    def zeitpunkt(text):
        try:
            z = pd.Timestamp(parsedate_to_datetime(text))
            return z.tz_localize("UTC") if z.tzinfo is None else z.tz_convert("UTC")
        except Exception:
            return pd.Timestamp(0, tz="UTC")

    items, ok = [], 0
    for quelle, url in FEEDS.items():
        try:
            r = requests.get(url, headers=UA, timeout=15)
            r.raise_for_status()
            for it in ET.fromstring(r.content).iter("item"):
                titel = (it.findtext("title") or "").strip()
                link = (it.findtext("link") or "").strip()
                if titel and link:
                    items.append((zeitpunkt(it.findtext("pubDate")), titel, link, quelle))
            ok += 1
        except Exception:
            continue
    if not ok:
        raise RuntimeError("keine Feeds erreichbar")
    gesehen, zeilen = set(), []
    for _, titel, link, quelle in sorted(items, key=lambda x: x[0], reverse=True):
        if titel.lower() in gesehen:
            continue
        gesehen.add(titel.lower())
        zeilen.append((f"{titel} ({quelle})", link))
        if len(zeilen) == 8:
            break
    return zeilen


FG = {}  # wird von fear_greed() befüllt
FG_SEGMENTE = [  # (von, bis, Name, Farbe) - Grenzen wie bei CNN
    (0, 25, "Extreme Fear", "#35bfa9"),
    (25, 45, "Fear", "#0a8f7e"),
    (45, 55, "Neutral", "#f5c400"),
    (55, 75, "Greed", "#d94545"),
    (75, 100, "Extreme Greed", "#8c1c20"),
]


def fear_greed_html():
    """Halbkreis-Tacho für den CNN Fear & Greed Index (Inline-SVG)."""
    import html as h
    import math
    if "score" not in FG:
        return ""
    score = max(0.0, min(100.0, FG["score"]))
    cx, cy, R, r = 200, 180, 170, 108

    def pt(v, rad):
        th = math.pi * (1 - v / 100)
        return cx + rad * math.cos(th), cy - rad * math.sin(th)

    svg = []
    name_aktiv, farbe_aktiv = "", "#777"
    for a, b, name, farbe in FG_SEGMENTE:
        x1, y1 = pt(a, R)
        x2, y2 = pt(b, R)
        x3, y3 = pt(b, r)
        x4, y4 = pt(a, r)
        svg.append(f'<path d="M{x1:.1f} {y1:.1f} A{R} {R} 0 0 1 {x2:.1f} {y2:.1f} '
                   f'L{x3:.1f} {y3:.1f} A{r} {r} 0 0 0 {x4:.1f} {y4:.1f} Z" '
                   f'fill="{farbe}" stroke="#fafafa" stroke-width="3"/>')
        m = (a + b) / 2
        tx, ty = pt(m, (R + r) / 2)
        rot = 90 - math.degrees(math.pi * (1 - m / 100))
        fs = 10 if b - a < 15 else 12
        svg.append(f'<text x="{tx:.1f}" y="{ty:.1f}" transform="rotate({rot:.1f} {tx:.1f} {ty:.1f})" '
                   f'text-anchor="middle" dominant-baseline="middle" font-size="{fs}" '
                   f'fill="#fff" font-family="Georgia,serif">{name}</text>')
        if a <= score <= b and not name_aktiv:
            name_aktiv, farbe_aktiv = name, farbe

    ax, ay = pt(0, r - 24)
    bx, by = pt(100, r - 24)
    svg.append(f'<path d="M{ax:.1f} {ay:.1f} A{r - 24} {r - 24} 0 0 1 {bx:.1f} {by:.1f}" '
               f'fill="none" stroke="#bbb" stroke-width="1" stroke-dasharray="3 4"/>')
    for v in range(0, 101, 10):
        tx, ty = pt(v, r - 12)
        svg.append(f'<text x="{tx:.1f}" y="{ty:.1f}" text-anchor="middle" '
                   f'dominant-baseline="middle" font-size="11" fill="#777">{v}</text>')

    th = math.pi * (1 - score / 100)
    ux, uy = math.cos(th), -math.sin(th)
    px, py = -uy, ux
    ox, oy = cx + ux * 80, cy + uy * 80
    sx, sy = cx + ux * 44, cy + uy * 44
    svg.append(f'<polygon points="{ox + px * 4.5:.1f},{oy + py * 4.5:.1f} '
               f'{ox - px * 4.5:.1f},{oy - py * 4.5:.1f} {sx:.1f},{sy:.1f}" fill="#222"/>')
    svg.append(f'<text x="{cx}" y="{cy - 16}" text-anchor="middle" font-size="30" '
               f'font-weight="700" fill="{farbe_aktiv}">{score:.0f}</text>')
    svg.append(f'<text x="{cx}" y="{cy - 2}" text-anchor="middle" font-size="13" '
               f'fill="#666">{name_aktiv}</text>')

    vergleich = []
    for key, label in (("previous_close", "Vortag"), ("previous_1_week", "1 Woche"),
                       ("previous_1_month", "1 Monat"), ("previous_1_year", "1 Jahr")):
        try:
            vergleich.append(f"{label} <b>{float(FG['daten'][key]):.0f}</b>")
        except Exception:
            continue
    zeile = f'<div class="fgh">{" · ".join(vergleich)}</div>' if vergleich else ""
    return (f'<div class="box gauge"><h2>CNN Fear &amp; Greed Index</h2>'
            f'<svg viewBox="0 0 400 192" role="img" aria-label="Fear and Greed Index {score:.0f}, '
            f'{h.escape(name_aktiv)}">{"".join(svg)}</svg>{zeile}</div>')


def treiber_html():
    import html as h
    teile = []
    for titel, zeilen in TREIBER:
        lis = ""
        for text, link in zeilen:
            t = h.escape(text)
            if link and link.startswith("http"):
                t = f'<a href="{h.escape(link, quote=True)}" target="_blank" rel="noopener">{t}</a>'
            lis += f"<li>{t}</li>"
        teile.append(f'<div class="tr"><h2>{h.escape(titel)}</h2><ul>{lis}</ul></div>')
    return "".join(teile)


def ausgabe():
    print(f"\n=== S&P-500-Dashboard | {jetzt():%d.%m.%Y %H:%M} ===\n")
    for titel, zeilen in TREIBER:
        print(f"--- {titel} ---")
        for text, _ in zeilen:
            print(f"  • {text}")
        print()
    for name, wert, ampel, hinweis in ergebnisse:
        sym = SYMBOL.get(ampel, "⚪")
        print(f"{sym} {name:<24} {wert:<42} {hinweis}")

    werte = [a for _, _, a, _ in ergebnisse if a is not None]
    if not werte:
        print("\nKeine Daten erhalten.")
        return
    schnitt = sum(werte) / len(werte)
    gesamt = GRUEN if schnitt > 0.33 else ROT if schnitt < -0.33 else GELB
    n = {g: werte.count(g) for g in (GRUEN, GELB, ROT)}
    print(f"\nGesamtbild: {SYMBOL[gesamt]}  (🟢 {n[GRUEN]} | 🟡 {n[GELB]} | 🔴 {n[ROT]})")
    print("Hinweis: Faustregeln, keine Anlageberatung. Auf Übereinstimmung achten, nicht auf Einzelwerte.\n")


def grafik():
    """Speichert sp500_dashboard.png: Kurs + 50/200T, VIX, Ampel-Übersicht."""
    import matplotlib.pyplot as plt

    farben = {GRUEN: "#2e9e5b", GELB: "#e0b000", ROT: "#d33a3a", None: "#999999"}
    s = kurs("^GSPC")
    vix = kurs("^VIX")
    fig, (a1, a2, a3) = plt.subplots(
        3, 1, figsize=(10, 12), gridspec_kw={"height_ratios": [3, 1.5, 2.5]})

    a1.plot(s.index, s, label="S&P 500", color="black", lw=1.2)
    a1.plot(s.index, s.rolling(50).mean(), label="50T", color="#1f77b4")
    a1.plot(s.index, s.rolling(200).mean(), label="200T", color="#ff7f0e")
    a1.set_title("S&P 500 mit 50-/200-Tage-Linie")
    a1.legend(loc="upper left")
    a1.grid(alpha=0.3)

    v = vix.iloc[-250:]
    a2.plot(v.index, v, color="purple")
    a2.axhline(20, color=farben[GELB], ls="--")
    a2.axhline(30, color=farben[ROT], ls="--")
    a2.set_title("VIX (20 = Vorsicht, 30 = Panik)")
    a2.grid(alpha=0.3)

    n = len(ergebnisse)
    a3.set_xlim(0, 1)
    a3.set_ylim(0, n)
    a3.axis("off")
    for i, (name, wert, ampel, _) in enumerate(ergebnisse):
        y = n - i - 0.5
        a3.scatter(0.02, y, s=250, color=farben[ampel])
        a3.text(0.06, y, name, va="center", fontweight="bold")
        a3.text(0.40, y, wert, va="center")
    a3.set_title("Ampel-Übersicht")

    fig.tight_layout()
    fig.savefig("sp500_dashboard.png", dpi=150)
    print("Grafik gespeichert: sp500_dashboard.png")
    plt.show()


HTML_VORLAGE = """<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>S&amp;P-500-Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
body{font-family:system-ui,sans-serif;max-width:1000px;margin:0 auto;padding:16px;color:#222;background:#fafafa}
h1{font-size:1.4rem;margin:0 0 4px}
.sub{color:#666;font-size:.85rem;margin-bottom:16px}
.gesamt{font-size:1.1rem;margin:12px 0 16px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:10px;margin-bottom:20px}
.card{background:#fff;border:1px solid #e3e3e3;border-radius:10px;padding:12px 14px;display:flex;gap:12px;align-items:flex-start}
.dot{width:16px;height:16px;border-radius:50%;flex:none;margin-top:3px}
.name{font-weight:600}.wert{font-size:.95rem}.hint{color:#777;font-size:.8rem;margin-top:2px}
.box{background:#fff;border:1px solid #e3e3e3;border-radius:10px;padding:12px;margin-bottom:16px}
.foot{color:#888;font-size:.78rem;margin-top:8px}
.haupt{font-size:1.1rem;margin:10px 0 8px}
.tr{background:#fff;border:1px solid #e3e3e3;border-radius:10px;padding:10px 14px;margin-bottom:10px}
.tr h2{font-size:.95rem;margin:0 0 6px}
.tr ul{margin:0;padding-left:18px}
.tr li{font-size:.9rem;margin:3px 0}
.tr a{color:#1a56c4;text-decoration:none}
.gauge{text-align:center}
.gauge h2{font-size:.95rem;margin:0 0 4px;text-align:left}
.gauge svg{width:100%;max-width:420px;height:auto}
.fgh{color:#555;font-size:.85rem;margin-top:2px}
</style>
<meta http-equiv="refresh" content="900">
</head><body>
<h1>S&amp;P-500-Dashboard</h1>
<div class="sub">Stand: __STAND__</div>
__GAUGE__
<h2 class="haupt">Was bewegt den Markt heute?</h2>
__TREIBER__
<div class="gesamt">Gesamtbild: __GESAMT__</div>
<div class="grid">__KARTEN__</div>
<div class="box"><canvas id="c1" height="110"></canvas></div>
<div class="box"><canvas id="c2" height="60"></canvas></div>
<div class="foot">Faustregeln, keine Anlageberatung. Auf Übereinstimmung mehrerer Signale achten.</div>
<script>
const D = __DATA__;
const opt = {responsive:true, interaction:{mode:'index',intersect:false},
  scales:{x:{ticks:{maxTicksLimit:8}}}, plugins:{legend:{position:'bottom'}}};
const ds = (label,data,color,w=1.5,dash)=>({label,data,borderColor:color,borderWidth:w,
  borderDash:dash||[],pointRadius:0,tension:0});
new Chart(document.getElementById('c1'),{type:'line',
  data:{labels:D.dates,datasets:[ds('S&P 500',D.close,'#222'),ds('50T',D.ma50,'#1f77b4'),ds('200T',D.ma200,'#ff7f0e')]},
  options:{...opt,plugins:{...opt.plugins,title:{display:true,text:'S&P 500 mit 50-/200-Tage-Linie'}}}});
new Chart(document.getElementById('c2'),{type:'line',
  data:{labels:D.dates,datasets:[ds('VIX',D.vix,'purple'),
    ds('20 (Vorsicht)',D.dates.map(_=>20),'#e0b000',1,[6,4]),
    ds('30 (Panik)',D.dates.map(_=>30),'#d33a3a',1,[6,4])]},
  options:{...opt,plugins:{...opt.plugins,title:{display:true,text:'VIX'}}}});
</script></body></html>
"""


def html_export():
    """Schreibt sp500_dashboard.html und öffnet sie im Browser."""
    import html as h
    import json
    import webbrowser
    from pathlib import Path

    farben = {GRUEN: "#2e9e5b", GELB: "#e0b000", ROT: "#d33a3a", None: "#999999"}

    s = kurs("^GSPC")
    ma50, ma200 = s.rolling(50).mean(), s.rolling(200).mean()
    vix = kurs("^VIX").reindex(s.index).ffill()
    n = 250  # ca. 1 Handelsjahr anzeigen

    def liste(x):
        return [None if pd.isna(v) else round(float(v), 2) for v in x.iloc[-n:]]

    daten = {
        "dates": [d.strftime("%Y-%m-%d") for d in s.index[-n:]],
        "close": liste(s), "ma50": liste(ma50), "ma200": liste(ma200), "vix": liste(vix),
    }

    karten = "".join(
        f'<div class="card"><div class="dot" style="background:{farben[a]}"></div><div>'
        f'<div class="name">{h.escape(name)}</div><div class="wert">{h.escape(wert)}</div>'
        f'<div class="hint">{h.escape(hint)}</div></div></div>'
        for name, wert, a, hint in ergebnisse
    )

    werte = [a for _, _, a, _ in ergebnisse if a is not None]
    schnitt = sum(werte) / len(werte) if werte else 0
    gesamt = GRUEN if schnitt > 0.33 else ROT if schnitt < -0.33 else GELB
    gesamt_txt = (f"{SYMBOL[gesamt]} &nbsp;(🟢 {werte.count(GRUEN)} | 🟡 {werte.count(GELB)} "
                  f"| 🔴 {werte.count(ROT)})")

    seite = (HTML_VORLAGE
             .replace("__STAND__", f"{jetzt():%d.%m.%Y %H:%M} Uhr (Kurse ggf. ca. 15 Min. verzögert)")
             .replace("__GESAMT__", gesamt_txt)
             .replace("__KARTEN__", karten)
             .replace("__DATA__", json.dumps(daten))
             .replace("__GAUGE__", fear_greed_html())
             .replace("__TREIBER__", treiber_html()))
    # --out=public/index.html -> an dieser Stelle speichern, ohne Browser (für GitHub Actions)
    out = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--out=")), None)
    pfad = Path(out or "sp500_dashboard.html").resolve()
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(seite, encoding="utf-8")
    print(f"HTML gespeichert: {pfad}")
    if not out:
        webbrowser.open(pfad.as_uri())


if __name__ == "__main__":
    trend()
    volatilitaet()
    breite_etf()
    kreditspread()
    zinsen()
    zinskurve()
    zinserwartung()
    fear_greed()
    if "--breite" in sys.argv:
        marktbreite_200()
    marktbild()
    sektoren_schwergewichte()
    makro()
    termine()
    earnings()
    news()
    ausgabe()
    if "--grafik" in sys.argv:
        grafik()
    if "--html" in sys.argv:
        html_export()
