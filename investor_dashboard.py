#!/usr/bin/env python3
"""S&P-500-Investor-Dashboard nach PDF-Spezifikation.
pip install yfinance pandas numpy requests lxml
python investor_dashboard.py [--out=public/investor.html]   (CSV landet daneben)"""
import io, json, os, sys, threading, time
import html as H
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import pandas as pd
import requests
import yfinance as yf

UA = {"User-Agent": "Mozilla/5.0"}
JETZT = pd.Timestamp.now(tz="Europe/Berlin")
FARBE = {"G": "#2e9e5b", "Y": "#e0b000", "R": "#d33a3a", "E": "#1f6fd1", None: "#999"}
K, ALERTS, FEHLER, CH, S, AREA = [], [], [], {}, {}, {}
SEK = {"XLK": "Technologie", "XLF": "Finanzen", "XLV": "Gesundheit", "XLY": "Zykl. Konsum",
       "XLP": "Basiskonsum", "XLE": "Energie", "XLI": "Industrie", "XLB": "Rohstoffe",
       "XLU": "Versorger", "XLRE": "Immobilien", "XLC": "Kommunikation"}


def kz(kachel, name, wert, amp=None, info="", quelle="", zeit=""):
    K.append(dict(kachel=kachel, name=name, wert=wert, amp=amp, info=info, quelle=quelle,
                  zeit=zeit, stand=f"{JETZT:%d.%m.%Y}"))


def schritt(f):
    def w():
        S["laeuft"] = f.__name__
        try:
            f()
        except Exception as e:
            FEHLER.append(f"{f.__name__}: {type(e).__name__} {str(e)[:90]}")
    return w


def ser(x):
    return [None if pd.isna(v) else round(float(v), 2) for v in x]


def hist(t, per="max"):
    s = yf.Ticker(t).history(period=per, auto_adjust=True)["Close"].dropna()
    s.index = s.index.tz_localize(None)
    if s.index[-1] < pd.Timestamp.now() - pd.Timedelta(days=6):
        FEHLER.append(f"{t}: Daten veraltet (letzter Wert {s.index[-1]:%d.%m.%Y})")
    return s


def fred(c):
    for i in range(2):
        try:
            r = requests.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={c}",
                             headers=UA, timeout=(5, 20))
            r.raise_for_status()
            break
        except requests.RequestException:
            if i == 1:
                raise
            time.sleep(2)
    d = pd.read_csv(io.StringIO(r.text), index_col=0, parse_dates=True)
    return pd.to_numeric(d.iloc[:, 0], errors="coerce").dropna()


FR = {}
CODES = ["DGS2", "DGS10", "DGS30", "T10Y2Y", "DFII10", "DBAA", "DFF", "DFEDTARU", "DFEDTARL",
         "BAMLH0A0HYM2", "BAMLC0A0CM"]
TRS = {"DGS2": "2 Yr", "DGS10": "10 Yr", "DGS30": "30 Yr"}


def treasury(art="daily_treasury_yield_curve"):
    y = pd.Timestamp.now().year
    r = requests.get("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
                     f"daily-treasury-rates.csv/{y}/all?type={art}"
                     f"&field_tdr_date_value={y}&page&_format=csv", headers=UA, timeout=(5, 20))
    r.raise_for_status()
    return pd.read_csv(io.StringIO(r.text), index_col=0, parse_dates=True).sort_index()


def hole(c):
    try:
        return fred(c)
    except Exception as e:
        err = type(e).__name__
    if c in TRS or c == "T10Y2Y":  # Ersatzquelle für Renditen
        try:
            d = treasury()
            s = d["10 Yr"] - d["2 Yr"] if c == "T10Y2Y" else d[TRS[c]]
            FEHLER.append(f"FRED {c}: {err} – Ersatz Treasury.gov")
            return pd.to_numeric(s, errors="coerce").dropna()
        except Exception:
            pass
    try:
        if c in ("DFF", "DFEDTARU", "DFEDTARL"):  # Ersatz: NY Fed (EFFR + Zielkorridor)
            r = requests.get("https://markets.newyorkfed.org/api/rates/unsecured/effr/last/1.json",
                             headers=UA, timeout=(5, 20))
            r.raise_for_status()
            x = r.json()["refRates"][0]
            v = {"DFF": x["percentRate"], "DFEDTARL": x["targetRateFrom"], "DFEDTARU": x["targetRateTo"]}[c]
            FEHLER.append(f"FRED {c}: {err} – Ersatz NY Fed")
            return pd.Series([float(v)])
        if c == "DFII10":  # Ersatz: Realrendite 10J von Treasury.gov
            d = treasury("daily_treasury_real_yield_curve")
            FEHLER.append(f"FRED {c}: {err} – Ersatz Treasury.gov")
            return pd.to_numeric(d["10 YR"], errors="coerce").dropna()
    except Exception:
        pass
    FEHLER.append(f"FRED {c}: {err}")


def vorladen():
    with ThreadPoolExecutor(max_workers=len(CODES)) as ex:
        for c, s in zip(CODES, ex.map(hole, CODES)):
            FR[c] = s


def fr(c):
    return FR.get(c)


def shiller():
    try:
        return (multpl("shiller-pe"), multpl("s-p-500-pe-ratio"), multpl("s-p-500-dividend-yield"),
                "multpl.com (Shiller-Daten)")
    except Exception as e:
        FEHLER.append(f"multpl.com: {type(e).__name__} – Ersatz github.com/datasets/s-and-p-500 (Shiller-Daten)")
    r = requests.get("https://raw.githubusercontent.com/datasets/s-and-p-500/main/data/data.csv",
                     headers=UA, timeout=(5, 20))
    r.raise_for_status()
    d = pd.read_csv(io.StringIO(r.text), parse_dates=["Date"], index_col="Date").sort_index()
    cape = d["PE10"].dropna()
    if cape.index[-1] < pd.Timestamp.now() - pd.Timedelta(days=120):
        FEHLER.append(f"Bewertungsdaten veraltet (letzter Wert {cape.index[-1]:%m/%Y})")
    return (cape, (d["SP500"] / d["Earnings"]).dropna(), (d["Dividend"] / d["SP500"] * 100).dropna(),
            "github.com/datasets/s-and-p-500 (Shiller-Daten)")


def multpl(slug):
    r = requests.get(f"https://www.multpl.com/{slug}/table/by-month", headers=UA, timeout=(5, 15))
    r.raise_for_status()
    d = pd.read_html(io.StringIO(r.text))[0].iloc[:, :2]
    v = d.iloc[:, 1].astype(str).str.replace(r"[^\d.\-]", "", regex=True)
    return pd.Series(pd.to_numeric(v, errors="coerce").values,
                     index=pd.to_datetime(d.iloc[:, 0])).dropna().sort_index()


def stat(s, y):
    w = s[s.index >= s.index[-1] - pd.DateOffset(years=y)]
    return dict(m=w.mean(), md=w.median(), q1=w.quantile(.25), q3=w.quantile(.75),
                pr=(w <= s.iloc[-1]).mean() * 100)


def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


@schritt
def trend():
    s = hist("^GSPC")
    S["spx"] = s
    c = s.iloc[-1]
    m = {n: s.rolling(n).mean() for n in (50, 100, 200)}
    pr = lambda n: (c / s.iloc[-1 - n] - 1) * 100
    hi, lo, ath = s.iloc[-252:].max(), s.iloc[-252:].min(), s.max()
    dd, d52 = (c / ath - 1) * 100, (c / hi - 1) * 100
    sl20, sl50 = [(m[200].iloc[-1] / m[200].iloc[-1 - n] - 1) * 100 for n in (20, 50)]
    dist = {n: (c / m[n].iloc[-1] - 1) * 100 for n in m}
    if c > m[50].iloc[-1] and c > m[200].iloc[-1] and sl20 > 0 and d52 >= -10:
        a = "G"
    elif c < m[200].iloc[-1] and sl20 < 0:
        a = "R"
    else:
        a = "Y"
    below = 0
    for v in (s < m[200])[::-1]:
        if not v:
            break
        below += 1
    S.update(trend=a, dist200=dist[200], slope20=sl20, dd=dd, below=below, lo52=lo, close=c)
    AREA["Trend"] = a
    q = f"Yahoo ^GSPC, Schluss {s.index[-1]:%d.%m.%Y}"
    kz("Trend", "S&P 500 Schlusskurs", f"{c:,.0f}", a,
       f"Tag {pr(1):+.2f} % · Woche {pr(5):+.2f} % · Monat {pr(21):+.2f} %", q, "1/5/21 Handelstage")
    for n in (50, 100, 200):
        kz("Trend", f"SMA {n}", f"{m[n].iloc[-1]:,.0f}", None, f"Abstand Index {dist[n]:+.1f} %", q, f"{n} Handelstage")
    kz("Trend", "Steigung SMA 200", f"{sl20:+.2f} % / 20 T", None, f"über 50 T: {sl50:+.2f} %", q, "20/50 Handelstage")
    kz("Trend", "52-Wochen-Hoch / -Tief", f"{hi:,.0f} / {lo:,.0f}", None, f"Abstand zum Hoch {d52:+.1f} %", q, "252 Handelstage")
    kz("Trend", "Allzeithoch / Drawdown", f"{ath:,.0f}", None, f"Drawdown {dd:.1f} %", q, "seit Beginn der Reihe")
    kz("Trend", "Tage unter SMA 200 (in Folge)", str(below), None, "", q, "")
    b = (s > m[200]).iloc[-6:]
    if b.iloc[0] != b.iloc[-1]:
        ALERTS.append("200-Tage-Linie " + ("zurückerobert" if b.iloc[-1] else "gebrochen") + " (letzte 5 Tage)")
    for t in (20, 10, 5):
        if dd <= -t:
            ALERTS.append(f"Drawdown-Schwelle {t} % erreicht (aktuell {dd:.1f} %)")
            break
    z = s.iloc[-2520:]
    CH["spx"] = dict(d=[f"{x:%Y-%m-%d}" for x in z.index], c=ser(z), m50=ser(m[50].loc[z.index]),
                     m200=ser(m[200].loc[z.index]))


@schritt
def breite():
    h = requests.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", headers=UA, timeout=30).text
    tab = pd.read_html(io.StringIO(h))[0]
    tick = tab["Symbol"].str.replace(".", "-", regex=False).tolist()
    sm = pd.Series(tab["GICS Sector"].values, index=tick)
    teile, t0 = [], time.time()
    for i in range(0, len(tick), 100):
        if time.time() - t0 > 300:
            FEHLER.append(f"Marktbreite: Zeitlimit, nur {i} von {len(tick)} Aktien geladen")
            break
        try:
            teile.append(yf.download(tick[i:i + 100], period="5y", progress=False, auto_adjust=True, timeout=30)["Close"])
        except Exception as e:
            FEHLER.append(f"Marktbreite Block {i}: {type(e).__name__}")
    px = pd.concat(teile, axis=1).dropna(axis=1, how="all")
    m50, m200 = px.rolling(50).mean(), px.rolling(200).mean()
    b50 = (px > m50).sum(axis=1) / m50.notna().sum(axis=1) * 100
    b200 = (px > m200).sum(axis=1) / m200.notna().sum(axis=1) * 100
    ch = px.diff()
    adv, dec = (ch > 0).sum(axis=1), (ch < 0).sum(axis=1)
    ad = (adv - dec).cumsum()
    nh = (px >= px.rolling(252).max()).sum(axis=1)
    nl = (px <= px.rolling(252).min()).sum(axis=1)
    x50, x200 = b50.iloc[-1], b200.iloc[-1]
    c200, c50 = x200 - b200.iloc[-21], x50 - b50.iloc[-6]
    sp = S["spx"].reindex(px.index).ffill()
    i60, a60 = sp.iloc[-1] / sp.iloc[-61] - 1, ad.iloc[-1] - ad.iloc[-61]
    i20 = (sp.iloc[-1] / sp.iloc[-21] - 1) * 100
    a = "G" if x200 >= 70 else "R" if x200 < 40 else "Y"
    div = ""
    if i60 > 0.01 and a60 < 0:
        div = "Divergenz: Index steigt, A/D-Line fällt – enge Rallye (Warnung)"
        a = "Y" if a == "G" else a
        ALERTS.append(div)
    elif i60 < -0.01 and a60 > 0:
        div = "Index fällt, A/D-Line steigt – tendenziell konstruktiv"
    if i20 > 0 and c200 < 0:
        div += " · Index steigt bei sinkender Breite (konzentrierte Rallye)"
    if abs(x200 - b200.iloc[-6]) >= 10:
        ALERTS.append(f"Starke Veränderung der Marktbreite (200T-Anteil {x200 - b200.iloc[-6]:+.0f} PP in 5 Tagen)")
    S.update(b200=x200, b200c20=c200, b50c5=c50)
    AREA["Breite"] = a
    q = f"Yahoo, aktuelle S&P-500-Mitglieder (Wikipedia), Schluss {px.index[-1]:%d.%m.%Y}"
    kz("Breite", "Aktien über SMA 200", f"{x200:.0f} %", a, f"20 T: {c200:+.0f} PP · >70 % breit, <40 % schwach. {div}", q, "200 Handelstage")
    kz("Breite", "Aktien über SMA 50", f"{x50:.0f} %", None, f"5 T: {c50:+.0f} PP", q, "50 Handelstage")
    kz("Breite", "Advance/Decline heute", f"{adv.iloc[-1]} / {dec.iloc[-1]}", None, f"A/D-Linie 60 T: {a60:+.0f}", q, "Tagesschluss")
    kz("Breite", "Neue 52W-Hochs / -Tiefs", f"{nh.iloc[-1]} / {nl.iloc[-1]}", None, f"Netto {nh.iloc[-1] - nl.iloc[-1]:+d}", q, "252 Handelstage")
    kz("Breite", "Aktien mit steigendem SMA 50 / SMA 200",
       f"{(m50.iloc[-1] > m50.iloc[-21]).mean() * 100:.0f} % / {(m200.iloc[-1] > m200.iloc[-21]).mean() * 100:.0f} %", None, "SMA heute vs. vor 20 T", q, "20 Handelstage")
    mo = {}
    for n, d in ((1, 21), (3, 63), (6, 126), (12, 252)):
        r = px.iloc[-1] / px.iloc[-1 - d] - 1
        mo[n] = (r > 0).sum() / r.notna().sum() * 100
    kz("Breite", "Aktien mit positivem Momentum 1/3/6/12 M", " / ".join(f"{mo[n]:.0f} %" for n in mo), None, "", q, "21/63/126/252 Handelstage")
    sec = ((px.iloc[-1] > m200.iloc[-1]).groupby(sm).mean() * 100).dropna().sort_values()
    CH["sek"] = dict(n=list(sec.index), v=[round(v, 1) for v in sec])
    n3 = 756
    CH["breadth"] = dict(d=[f"{x:%Y-%m-%d}" for x in px.index[-n3:]], b50=ser(b50.iloc[-n3:]), b200=ser(b200.iloc[-n3:]))
    a3, s3 = ad.iloc[-n3:], sp.iloc[-n3:]
    CH["ad"] = dict(d=CH["breadth"]["d"], ad=ser(a3 - a3.iloc[0]), spx=ser(s3 / s3.iloc[0] * 100))
    r = px.pct_change().iloc[-60:].corr().to_numpy(copy=True)
    np.fill_diagonal(r, np.nan)
    kz("Risiko", "Ø Korrelation der Aktien", f"{np.nanmean(r):.2f}", None, "hoch = Marktstress / Gleichlauf", q, "60 Handelstage")


@schritt
def bewertung():
    cape, pe, dy, quelle = shiller()
    S["ey"] = 100 / pe.iloc[-1]
    q = f"{quelle}, Monatswert {cape.index[-1]:%m/%Y}"
    kz("Bewertung", "Forward-P/E, P/S, P/B, EV/EBITDA", "n/a", None, "keine freie, verlässliche Quelle für Konsensschätzungen/Fundamentaldaten", "–", "–")
    for name, s, teuer in (("Shiller-CAPE", cape, True), ("Trailing-P/E", pe, True), ("Dividendenrendite", dy, False)):
        x = s.iloc[-1]
        st = {y: stat(s, y) for y in (10, 20, 30)}
        p = st[20]["pr"]
        a = ("G" if p <= 25 else "R" if p >= 75 else "Y") if teuer else ("G" if p >= 75 else "R" if p <= 25 else "Y")
        u = "x" if teuer else " %"
        z = (x - s.iloc[-120:].mean()) / s.iloc[-120:].std()
        kz("Bewertung", name, f"{x:.1f}{u}", a,
           f"20J: Ø {st[20]['m']:.1f} · Median {st[20]['md']:.1f} · Q25 {st[20]['q1']:.1f} / Q75 {st[20]['q3']:.1f} · "
           f"Perzentil 10/20/30J: {st[10]['pr']:.0f}/{p:.0f}/{st[30]['pr']:.0f} · Z-Score vs. 10J {z:+.1f}", q, "10/20/30 Jahre")
        if name == "Shiller-CAPE":
            S["cape_pr"] = p
            AREA["Bewertung"] = a
            w = s.iloc[-360:]
            CH["cape"] = dict(d=[f"{x:%Y-%m}" for x in w.index], v=ser(w), m=round(st[20]["m"], 1),
                              q1=round(st[20]["q1"], 1), q3=round(st[20]["q3"], 1))
    kz("Bewertung", "Trailing Earnings Yield", f"{S['ey']:.2f} %", None, "Kehrwert des Trailing-P/E (Forward nicht verfügbar)", q, "letzte 12 Monate")


@schritt
def zinsen():
    d = {k: fr(v) for k, v in dict(y2="DGS2", y10="DGS10", y30="DGS30", kurve="T10Y2Y", real="DFII10",
                                   baa="DBAA", ff="DFF", hi="DFEDTARU", lo="DFEDTARL").items()}
    L = lambda k: None if d[k] is None else d[k].iloc[-1]
    q = "FRED (St. Louis Fed), Tageswerte"
    ey, y10, baa = S.get("ey"), L("y10"), L("baa")
    if ey is not None and y10 is not None:
        sp = ey - y10
        S["sp"] = sp
        a = "R" if sp < 0 else "Y" if sp < 1 else "G"
        AREA["Fundament & Zinsen"] = a
        kz("Fundament & Zinsen", "Earnings Yield − 10J-Treasury (zentral)", f"{sp:+.2f} PP", a,
           f"Trailing-EY {ey:.2f} % − {y10:.2f} %. Gelb <1 PP; Rot hier schon bei negativem Spread (EPS-Revisionen fehlen)", q + " + multpl", "aktuell")
        if sp < 0:
            ALERTS.append("Earnings-Yield-Spread zur 10J-Rendite negativ")
    if ey is not None and baa is not None:
        kz("Fundament & Zinsen", "Earnings Yield − Baa-Rendite", f"{ey - baa:+.2f} PP", None, f"Baa-Rendite {baa:.2f} %", q, "aktuell")
    for k, n in (("y2", "Rendite 2J"), ("y10", "Rendite 10J"), ("y30", "Rendite 30J"), ("kurve", "Zinskurve 10J−2J"), ("real", "Realzins 10J (TIPS)")):
        if d[k] is not None:
            kz("Fundament & Zinsen", n, f"{L(k):.2f} %", None, f"20 T: {(d[k].iloc[-1] - d[k].iloc[-21]) * 100:+.0f} bp", q, "20 Handelstage")
    if d["hi"] is not None and d["lo"] is not None and d["y2"] is not None and d["ff"] is not None:
        kz("Fundament & Zinsen", "Fed-Funds-Zielkorridor", f"{L('lo'):.2f}–{L('hi'):.2f} %", None,
           f"2J-Rendite − Leitzins {L('y2') - L('ff'):+.2f} PP (<0: Markt erwartet Senkungen)", q, "aktuell")
    kz("Fundament & Zinsen", "Konsens-EPS, EPS-Revisionen, Surprise-Quote, Umsatz/Margen", "n/a", None, "keine freie, verlässliche Quelle", "–", "–")


@schritt
def risiko():
    s = S["spx"]
    v = hist("^VIX", "2y")
    x, v3 = v.iloc[-1], v.rolling(63).mean().iloc[-1]
    S["vix_chg5"] = x / v.iloc[-6] - 1
    if S["vix_chg5"] >= 0.3:
        ALERTS.append(f"VIX stark gestiegen ({S['vix_chg5'] * 100:+.0f} % in 5 Tagen)")
    hy, ig = fr("BAMLH0A0HYM2"), fr("BAMLC0A0CM")
    chg = hy.iloc[-1] - hy.iloc[-21] if hy is not None else 0
    if hy is None:  # Ersatz: HYG/IEF fällt, wenn sich Kreditspreads ausweiten
        try:
            rt = (hist("HYG", "6mo") / hist("IEF", "6mo")).dropna()
            c20 = (rt.iloc[-1] / rt.iloc[-21] - 1) * 100
            chg = 0.6 if c20 < -2 else 0.2 if c20 < -0.5 else 0.0
            kz("Risiko", "Kredit-Proxy HYG/IEF (FRED-Spread nicht erreichbar)", f"{c20:+.1f} % (20 T)",
               "R" if c20 < -2 else "Y" if c20 < -0.5 else "G", "fällt = Kreditspreads weiten sich aus", "Yahoo HYG/IEF", "20 Handelstage")
        except Exception:
            pass
    S["hy_chg"] = chg
    a = "R" if x >= 30 or chg > 0.5 else "G" if x < 20 and chg <= 0.1 else "Y"
    AREA["Risiko"] = a
    q = f"Yahoo ^VIX, Schluss {v.index[-1]:%d.%m.%Y}"
    kz("Risiko", "VIX / Ø 3 Monate", f"{x:.1f} / {v3:.1f}", a, "<20 ruhig, >30 Panik", q, "63 Handelstage")
    lr = np.log(s).diff()
    kz("Risiko", "Realisierte Volatilität 10/20/60 T", " / ".join(f"{lr.iloc[-n:].std() * np.sqrt(252) * 100:.1f} %" for n in (10, 20, 60)), None, "annualisiert", "Yahoo ^GSPC", "10/20/60 Handelstage")
    kz("Risiko", "Drawdown (ATH) / Abstand 52W-Tief", f"{S['dd']:.1f} %", None, f"Abstand zum 52W-Tief {(S['close'] / S['lo52'] - 1) * 100:+.1f} %", "Yahoo ^GSPC", "252 Handelstage")
    kz("Risiko", "RSI(14) S&P 500", f"{rsi(s).iloc[-1]:.0f}", None, "kein eigenständiges Signal", "Yahoo ^GSPC", "14 Handelstage")
    if hy is not None:
        kz("Risiko", "Kredit-Spread High Yield / Invest. Grade", f"{hy.iloc[-1]:.2f} / {'n/a' if ig is None else format(ig.iloc[-1], '.2f')} PP", a,
           f"HY 20 T: {chg:+.2f} PP", "FRED BAMLH0A0HYM2 / BAMLC0A0CM", "20 Handelstage")
    kz("Risiko", "Put/Call-Ratio, Beta zu Dollar/Öl/Zinsen", "n/a", None, "keine freie, verlässliche Quelle", "–", "–")


@schritt
def sektoren():
    tk = list(SEK) + ["SPY", "RSP", "IVE", "IVW", "IWM", "VXUS"]
    p = yf.download(tk, period="5y", progress=False, auto_adjust=True, timeout=30)["Close"].ffill()
    per = {"1T": 1, "1W": 5, "1M": 21, "3M": 63, "6M": 126, "12M": 252}
    r = pd.DataFrame({k: (p.iloc[-1] / p.iloc[-1 - n] - 1) * 100 for k, n in per.items()})
    rows = [[n] + [round(r.loc[t, k], 1) for k in per] + [round(r.loc[t, "1M"] - r.loc["SPY", "1M"], 1),
            round(r.loc[t, "3M"] - r.loc["SPY", "3M"], 1)] for t, n in SEK.items()]
    CH["sektor"] = dict(cols=list(per) + ["RS 1M", "RS 3M"], rows=rows)
    q = f"Yahoo ETFs, Schluss {p.index[-1]:%d.%m.%Y}"
    for n, a, b in (("Equal vs. Cap Weight (RSP − SPY)", "RSP", "SPY"), ("Value vs. Growth (IVE − IVW)", "IVE", "IVW"),
                    ("Large vs. Small Cap (SPY − IWM)", "SPY", "IWM"), ("US vs. International (SPY − VXUS)", "SPY", "VXUS")):
        kz("Sektoren & Stil", n, f"{r.loc[a, '3M'] - r.loc[b, '3M']:+.1f} PP (3M)", None,
           f"1M {r.loc[a, '1M'] - r.loc[b, '1M']:+.1f} · 12M {r.loc[a, '12M'] - r.loc[b, '12M']:+.1f} PP", q, "21/63/252 Handelstage")
    c = p[["RSP", "SPY"]].iloc[-756:]
    CH["ew"] = dict(d=[f"{x:%Y-%m-%d}" for x in c.index], ew=ser(c.RSP / c.RSP.iloc[0] * 100), cw=ser(c.SPY / c.SPY.iloc[0] * 100))
    kz("Sektoren & Stil", "Top-10-Positionen / Konzentration", "n/a", None, "keine freie, verlässliche Quelle für Indexgewichte", "–", "–")


def gesamt():
    t, dist, b, bc = S.get("trend"), S.get("dist200"), S.get("b200"), S.get("b200c20", 0)
    sp, cp, dd = S.get("sp"), S.get("cape_pr"), S.get("dd", 0)
    if t is None:
        return None, "Keine Bewertung möglich", "Trend-Daten fehlen."
    teile = [f"Index {'über' if dist > 0 else 'unter'} 200-Tage-Linie ({dist:+.1f} %)"]
    if b is not None:
        teile.append(f"Breite {'stabil/steigend' if bc >= -5 else 'fallend'} ({b:.0f} % über SMA 200)")
    if sp is not None:
        teile.append(f"Earnings Yield {'positiv' if sp > 0 else 'negativ'} ggü. 10J-Rendite ({sp:+.2f} PP)")
    txt = " · ".join(teile)
    hy = S.get("hy_chg", 0)
    if t == "R" and ((b is not None and b < 40) or hy > 0.5):
        code, tit = "R", "Defensive Marktlage"
    elif (dd <= -5 and -3 <= dist <= 5 and S.get("slope20", 0) >= 0 and S.get("b50c5", 0) >= 0
          and hy < 0.3 and (cp is None or cp < 75)):
        code, tit = "E", "Mögliche Einstiegszone"
    elif t == "G" and b is not None and b >= 50 and bc >= -5 and (sp is None or sp > 0) and (cp is None or cp < 90):
        code, tit = "G", "Konstruktive Marktlage"
    else:
        code, tit = "Y", "Vorsichtig optimistisch / abwarten"
    kz("Risiko", "Einstiegslogik / Gesamtampel", tit, code, "regelbasiert (Priorität: Rot → Einstieg → Grün → Gelb), keine Handelsempfehlung", "berechnet", "aktuell")
    return code, tit, txt


def render(code, tit, txt):
    dot = lambda a: f'<span class="dot" style="background:{FARBE[a]}"></span>'
    secs = ""
    for k, title, ids in (("Trend", "1 · Marktregime & Trend", ["c_spx"]), ("Breite", "2 · Marktbreite", ["c_breite", "c_ad", "c_sek"]),
                          ("Bewertung", "3 · Bewertung", ["c_cape"]), ("Fundament & Zinsen", "4 · Fundament & Zinsen", []),
                          ("Risiko", "5 · Risiko & Einstieg", []), ("Sektoren & Stil", "6 · Sektoren & Stilrotation", ["c_ew"])):
        rows = "".join(
            f'<div class="r"><div class="n">{H.escape(m["name"])}<small>{H.escape(m["quelle"])} · {H.escape(m["zeit"])} · Stand {m["stand"]}</small></div>'
            f'<div class="v">{dot(m["amp"]) if m["amp"] else ""}{H.escape(str(m["wert"]))}</div><div class="i">{H.escape(m["info"])}</div></div>'
            for m in K if m["kachel"] == k)
        rng = '<div class="rng">' + "".join(f'<button data-y="{y}">{y}J</button>' for y in (1, 3, 5, 10)) + "</div>" if k == "Trend" else ""
        heat = heatmap() if k == "Sektoren & Stil" else ""
        cv = "".join(f'<div class="box"><canvas id="{i}" height="{170 if i == "c_sek" else 110}"></canvas></div>' for i in ids)
        secs += f'<h2>{dot(AREA.get(k))}{title}</h2><div class="box">{rows or "<i>keine Daten</i>"}</div>{rng}{cv}{heat}'
    al = "".join(f"<li>{H.escape(a)}</li>" for a in ALERTS) or "<li>keine aktiven Hinweise</li>"
    fe = "".join(f"<li>{H.escape(f)}</li>" for f in FEHLER)
    nicht = ("Forward-P/E, P/S, P/B, EV/EBITDA, Konsens-EPS, EPS-Revisionen, Surprise-Quote, Umsatz/Margen, Put/Call, "
             "Beta zu Dollar/Öl/Zinsen, Top-10-Konzentration, Unterstützungszonen, Point-in-Time-Historie, Push-Benachrichtigungen, "
             "Detailseiten je Kachel (keine freie, verlässliche Datenquelle bzw. außerhalb dieses Umfangs).")
    return (TPL.replace("__STAND__", f"{JETZT:%d.%m.%Y %H:%M}").replace("__SC__", FARBE[code])
            .replace("__STATUS__", H.escape(f"S&P 500: {tit} – {txt}")).replace("__ALERTS__", al)
            .replace("__SECS__", secs).replace("__FEHLER__", fe or "<li>keine</li>").replace("__NICHT__", nicht)
            .replace("__DATA__", json.dumps(CH)))


def heatmap():
    if "sektor" not in CH:
        return ""
    d = CH["sektor"]
    bg = lambda v, sc: f"rgba({'46,158,91' if v >= 0 else '211,58,58'},{min(abs(v) / sc, 1) * .7:.2f})"
    h = "<tr><th></th>" + "".join(f"<th>{c}</th>" for c in d["cols"]) + "</tr>"
    for r in d["rows"]:
        h += f"<tr><td>{r[0]}</td>" + "".join(
            f'<td style="background:{bg(v, 5 if i >= 6 else 10)}">{v:+.1f}</td>' for i, v in enumerate(r[1:], 1)) + "</tr>"
    return f'<div class="box" style="overflow-x:auto"><table class="hm">{h}</table><small>Performance in %, RS = Differenz zum S&amp;P 500 (SPY) in PP</small></div>'


TPL = """<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>S&amp;P 500 Investor-Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>body{font-family:system-ui,sans-serif;max-width:1000px;margin:0 auto;padding:12px;color:#222;background:#fafafa}
h1{font-size:1.3rem;margin:0}h2{font-size:1.05rem;margin:18px 0 6px}.sub{color:#666;font-size:.8rem;margin:2px 0 10px}
.status{border:2px solid;border-radius:10px;padding:10px 12px;background:#fff;font-size:1rem}
.box{background:#fff;border:1px solid #e3e3e3;border-radius:10px;padding:8px 12px;margin-bottom:10px}
.dot{display:inline-block;width:12px;height:12px;border-radius:50%;margin-right:6px;vertical-align:middle}
.r{display:grid;grid-template-columns:1fr auto;gap:2px 10px;padding:7px 0;border-bottom:1px solid #f0f0f0}.r:last-child{border:0}
.n{font-weight:600;font-size:.92rem}small{display:block;color:#888;font-weight:400;font-size:.72rem}
.v{font-weight:600;text-align:right;white-space:nowrap}.i{grid-column:1/3;color:#555;font-size:.82rem}
.rng button{margin:0 4px 6px 0;padding:4px 10px;border:1px solid #ccc;border-radius:6px;background:#fff}
.hm{border-collapse:collapse;font-size:.8rem;width:100%}.hm td,.hm th{padding:4px 6px;text-align:right}.hm td:first-child{text-align:left}
ul{margin:4px 0;padding-left:18px;font-size:.85rem}@media print{.rng{display:none}body{background:#fff}}</style></head><body>
<h1>S&amp;P 500 Investor-Dashboard</h1>
<div class="sub">Stand __STAND__ · <a href="investor.csv">CSV-Export</a> · PDF: Teilen → Drucken · Entscheidungshilfe, keine Anlageberatung</div>
<div class="status" style="border-color:__SC__">__STATUS__</div>
<h2>Hinweise / Warnungen</h2><div class="box"><ul>__ALERTS__</ul></div>
__SECS__
<h2>Fehlende oder verzögerte Daten</h2><div class="box"><ul>__FEHLER__</ul></div>
<h2>Nicht enthalten</h2><div class="box"><small>__NICHT__ Marktbreite basiert auf den heutigen Indexmitgliedern (Survivorship-Bias).</small></div>
<script>const D=__DATA__;
const cs=(id,type,data,opt)=>{const e=document.getElementById(id);if(!e)return;return new Chart(e,{type,data,options:Object.assign({responsive:true,interaction:{mode:'index',intersect:false},elements:{point:{radius:0}},scales:{x:{ticks:{maxTicksLimit:7}}}},opt||{})})};
const L=(l,d,c,x)=>Object.assign({label:l,data:d,borderColor:c,borderWidth:1.5,tension:0},x||{});
if(D.spx){const n=D.spx.d.length;const ch=cs('c_spx','line',{labels:D.spx.d,datasets:[L('S&P 500',D.spx.c,'#222'),L('50T',D.spx.m50,'#1f77b4'),L('200T',D.spx.m200,'#ff7f0e')]});
document.querySelectorAll('[data-y]').forEach(b=>b.onclick=()=>{const k=Math.min(n,252*b.dataset.y),s=a=>a.slice(n-k);ch.data.labels=s(D.spx.d);ch.data.datasets[0].data=s(D.spx.c);ch.data.datasets[1].data=s(D.spx.m50);ch.data.datasets[2].data=s(D.spx.m200);ch.update()})}
if(D.breadth)cs('c_breite','line',{labels:D.breadth.d,datasets:[L('% über SMA 50',D.breadth.b50,'#1f77b4'),L('% über SMA 200',D.breadth.b200,'#ff7f0e')]},{plugins:{title:{display:true,text:'Anteil der S&P-500-Aktien über SMA 50/200 (3 Jahre)'}}});
if(D.ad)cs('c_ad','line',{labels:D.ad.d,datasets:[L('S&P 500 (rebased)',D.ad.spx,'#222',{yAxisID:'y'}),L('A/D-Line (kumuliert)',D.ad.ad,'#d33a3a',{yAxisID:'y1'})]},{scales:{x:{ticks:{maxTicksLimit:7}},y:{position:'left'},y1:{position:'right',grid:{drawOnChartArea:false}}},plugins:{title:{display:true,text:'S&P 500 vs. Advance/Decline-Line'}}});
if(D.sek)cs('c_sek','bar',{labels:D.sek.n,datasets:[{label:'% über SMA 200',data:D.sek.v,backgroundColor:'#1f77b4'}]},{indexAxis:'y',scales:{x:{min:0,max:100}},plugins:{title:{display:true,text:'Sektor-Breite (% über SMA 200)'}}});
if(D.cape){const c=D.cape,n=c.d.length,f=v=>Array(n).fill(v);cs('c_cape','line',{labels:c.d,datasets:[L('Shiller-CAPE',c.v,'#222'),L('Ø 20J',f(c.m),'#1f77b4',{borderDash:[5,4]}),L('Q25',f(c.q1),'#2e9e5b',{borderDash:[2,3]}),L('Q75',f(c.q3),'#d33a3a',{borderDash:[2,3]})]},{plugins:{title:{display:true,text:'CAPE mit 20J-Mittel und Perzentilbändern (30 Jahre)'}}})}
if(D.ew)cs('c_ew','line',{labels:D.ew.d,datasets:[L('Equal Weight (RSP)',D.ew.ew,'#2e9e5b'),L('Cap Weight (SPY)',D.ew.cw,'#222')]},{plugins:{title:{display:true,text:'Gleichgewichtet vs. marktkapitalisierungsgewichtet (rebased)'}}});
</script></body></html>"""


def main():
    out = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--out=")), "investor.html")
    def arbeit():
        vorladen()
        for f in (trend, bewertung, zinsen, risiko, sektoren, breite):  # Breite zuletzt (langsamster Teil)
            f()

    th = threading.Thread(target=arbeit, daemon=True)
    th.start()
    th.join(480)  # nach 8 Minuten wird mit dem bis dahin Berechneten abgeschlossen
    haengt = th.is_alive()
    if haengt:
        FEHLER.append(f"Zeitlimit (8 Min.): Schritt '{S.get('laeuft')}' war noch nicht fertig, spätere Abschnitte fehlen")
    code, tit, txt = gesamt()
    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render(code, tit, txt), encoding="utf-8")
    pd.DataFrame(K).rename(columns=dict(kachel="Bereich", name="Kennzahl", wert="Wert", amp="Ampel", info="Interpretation",
                                        quelle="Quelle", zeit="Berechnungszeitraum", stand="Aktualisiert")
                           ).to_csv(p.with_suffix(".csv"), index=False, sep=";", encoding="utf-8-sig")
    print(f"gespeichert: {p.resolve()} ({len(K)} Kennzahlen, {len(FEHLER)} Fehler)")
    for f in FEHLER:
        print("  Fehler:", f)
    if haengt:
        sys.stdout.flush()
        os._exit(0)  # hängende Download-Threads beenden


if __name__ == "__main__":
    main()
