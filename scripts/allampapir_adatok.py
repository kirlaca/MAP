"""Állampapír-adatok letöltése (napi cache): kínálat, sorozatok kamatai, kincstári árfolyamok, infláció, EURIBOR, EUR/HUF.

Források:
- allampapir.hu (ÁKK) belső JSON API-ja (SvelteKit oldal mögött, CSRF-tokennel):
    /api/csrf                          → token
    /api/paper/all_with_meta           → termékek (aktív / megszűnt), THM, futamidő
    /api/retailInterest/get_all_data   → minden lakossági sorozat minden kamatperiódusa (1999-től), underIssue = most kapható
    /api/networkRate/get_prices        → Magyar Államkincstár napi vételi/eladási árfolyamai (MAP, MAPP, DKJ, EMAP, BABA)
    /api/paper/get_files               → a sorozatok nyilvános ajánlattételei (PDF) → kamatprémium
- KSH STADAT ara0040: fogyasztói árindex, havonta és az év eleje óta (böngésző-fejléc kell, különben a WAF elutasítja)
- Eurostat prc_hicp_aind: euróövezeti éves átlagos HICP
- ECB Data Portal: 3 havi EURIBOR (havi átlag), EUR/HUF referenciaárfolyam

Kimenet: data/allampapir/adatok_ÉÉÉÉ-HH-NN.json  (naponta egyszer tölt le, utána a cache-ből olvas)
Futtatás:  python scripts/allampapir_adatok.py [--frissit]
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verzio  # noqa: E402
import requests

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data/allampapir"
AP = "https://www.allampapir.hu"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36")
NR_PAPERS = ["MAP", "MAPP", "DKJ", "EMAP"]          # kincstári árfolyamjegyzés fülei
# Ezeknél nem kell az ajánlattétel (sok PDF, és az ajánló úgysem veszi őket): Babakötvény, KTJ, nyomdai MÁP Plusz,
# aukciós DKJ és MÁK. Egy ÚJ terméknél viszont igen, ezért ez tiltólista, nem engedélyező lista.
NEM_KELL_AJANLATTETEL = {"BABA", "KTJ", "MAPP_F", "DKJ", "MAK"}


def num(s) -> float | None:
    """'5,47 %' / '99,0000' / 2.9 → float; üres → None."""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    if isinstance(s, (int, float)):
        return float(s)
    m = re.search(r"-?\d+(?:[.,]\d+)?", str(s).replace("\xa0", ""))
    return float(m.group(0).replace(",", ".")) if m else None


class Allampapir:
    def __init__(self) -> None:
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        tok = self.s.get(f"{AP}/api/csrf", headers={"Accept": "application/json"}, timeout=30).json()["data"]["token"]
        self.s.headers.update({"Accept": "application/json", "X-CSRF-TOKEN": tok})

    def call(self, path: str, body: dict) -> dict:
        r = self.s.post(f"{AP}/api{path}", json=body, timeout=60)
        r.raise_for_status()
        return r.json()

    def pdf_text(self, file_path: str) -> str:
        import pypdf
        r = self.s.get(AP + file_path, timeout=60, headers={"Accept": "application/pdf"})
        r.raise_for_status()
        return "\n".join(p.extract_text() or "" for p in pypdf.PdfReader(io.BytesIO(r.content)).pages)


def offering_terms(text: str) -> dict:
    """A nyilvános ajánlattétel szövegéből: kamatprémium, első periódus kamata, sávos kamatok."""
    t = re.sub(r"\s+", " ", text)
    out: dict = {}
    # PMÁP: "A kamatprémium mértéke 0,10% p.a." · BMÁP: "A kamatprémium mértéke: Kamatperiódus* … 1.: 2026.06.16. - … 0,25%"
    m = re.search(r"kamatpr[ée]mium m[ée]rt[ée]ke:?.{0,120}?(\d+,\d+) ?%", t, re.I)
    if m:
        out["premium"] = num(m.group(1))
    m = re.search(r"[ée]ves[ií]tett kamat m[ée]rt[ée]ke (\d+,\d+) ?%", t, re.I)
    if m:
        out["elso_periodus_kamat"] = num(m.group(1))
    # MÁP Plusz sávos kamat: "2026.10.02 - 2027.11.27 2027.11.27 5,00% 5,77%"
    sav = re.findall(r"(\d{4}\.\d{2}\.\d{2}) - (\d{4}\.\d{2}\.\d{2}) \d{4}\.\d{2}\.\d{2} (\d+,\d+) ?%", t)
    if sav:
        out["savos"] = [[a.replace(".", "-"), b.replace(".", "-"), num(r)] for a, b, r in sav]
    return out


def fetch_allampapir(log=print) -> dict:
    a = Allampapir()
    meta = a.call("/paper/all_with_meta", {"separate": 1})["data"]
    retail = a.call("/retailInterest/get_all_data", {})["data"]["data"]
    nr = {}
    for p in NR_PAPERS:
        d = a.call("/networkRate/get_prices", {"paper": p}).get("data") or {}
        nr[p] = {"settle_date": d.get("settle_date"), "rows": d.get("data") or []}
    offers = {}
    ma = dt.date.today().isoformat()
    for p in meta["papers"]["active"]:
        sn = p["short_name"]  # minden aktív termékhez (egy új terméknél is) lekérjük az ajánlattételt
        if sn in NEM_KELL_AJANLATTETEL:
            continue
        det = a.call("/paper/details", {"slug": sn}).get("data") or {}
        files = (a.call("/paper/get_files", {"paper": det.get("paper_id")}).get("data") or {}).get("files") or {}
        # csak a most futó értékesítés dokumentumai; sorozatonként a "nyilvános ajánlattétel" az első
        futo = [f for f in (files.get("active") or {}).get("ajanlattetel") or [] if (f.get("endPeriod") or ma) >= ma]
        futo.sort(key=lambda f: "ajánlattétel" not in (f.get("html_name") or ""))
        lattuk = set()
        for f in futo:
            if f.get("name") in lattuk:
                continue
            lattuk.add(f.get("name"))
            rec = {k: f.get(k) for k in ("name", "isin", "maturity", "startPeriod", "endPeriod", "currency",
                                         "interestType", "file_path", "html_name")}
            try:
                rec.update(offering_terms(a.pdf_text(f["file_path"])))
            except Exception as e:  # noqa: BLE001 – a prémium hiánya nem végzetes
                log(f"  ! ajánlattétel nem olvasható ({sn} {f.get('name')}): {e}")
            offers.setdefault(sn, []).append(rec)
    papers = [{k: p.get(k) for k in ("short_name", "long_name", "query_name")} | {"aktiv": st == "active"}
              for st in ("active", "inactive") for p in meta["papers"][st]]
    return {"papers": papers, "papermeta": meta.get("papermeta"), "retail": retail, "kincstar": nr, "ajanlattetel": offers}


def fetch_ksh_cpi() -> dict:
    """KSH ara0040 'Összesen' oszlop: havi (előző év azonos hó = 100) és év eleje óta átlag (előző év azonos időszaka = 100).
    Vissza: {'havi': {'2026-09': 1.6, ...}, 'ytd': {'2026': {'honap': 9, 'atlag': 1.7}}, 'eves': {'2025': 4.4, ...}}"""
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "text/html,*/*", "Accept-Language": "hu-HU,hu;q=0.9"})
    r = s.get("https://www.ksh.hu/stadat_files/ara/hu/ara0040.html", timeout=60)
    r.encoding = "iso-8859-2"
    if "Request Rejected" in r.text:
        raise RuntimeError("a KSH elutasította a kérést")
    df = pd.read_html(io.StringIO(r.text), decimal=",", thousands="\xa0")[0]
    per, sub, tot = df.columns[0], df.columns[1], [c for c in df.columns if str(c).startswith("Összesen")][0]
    honapok = ["január", "február", "március", "április", "május", "június", "július", "augusztus",
               "szeptember", "október", "november", "december"]
    out = {"havi": {}, "ytd": {}, "eves": {}}
    blokk, ev = None, None
    for _, row in df.iterrows():
        p, m, v = str(row[per]), str(row[sub]).strip(), row[tot]
        if "=" in p:  # blokkfejléc: havi éves változás / előző hónap / előző év dec. / évkezdettől kumulált
            blokk = ("ytd" if p.startswith("Évkezdettől") else
                     "havi" if p.startswith("Az előző év azonos") else "egyeb")
            ev = None
            continue
        if re.match(r"\d{4}\.", p):
            ev = int(p[:4])
        v = num(v)
        if ev is None or v is None:
            continue
        if blokk == "havi" and m in honapok:
            out["havi"][f"{ev}-{honapok.index(m) + 1:02d}"] = round(v - 100, 2)
        elif blokk == "ytd" and "–" in m:
            hon = honapok.index(m.split("–")[1].strip()) + 1
            out["ytd"][str(ev)] = {"honap": hon, "atlag": round(v - 100, 2)}
            if hon == 12:
                out["eves"][str(ev)] = round(v - 100, 2)
    return out


def fetch_hicp_eves() -> dict:
    """Eurostat: euróövezeti éves átlagos HICP-változás (%)."""
    u = ("https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/prc_hicp_aind"
         "?geo=EA&coicop=CP00&unit=RCH_A_AVG&sinceTimePeriod=2018")
    j = requests.get(u, timeout=60, headers={"User-Agent": UA}).json()
    idx = {v: k for k, v in j["dimension"]["time"]["category"]["index"].items()}
    return {idx[int(i)]: v for i, v in j["value"].items()}


def fetch_ecb(series: str, n: int) -> list[tuple[str, float]]:
    u = f"https://data-api.ecb.europa.eu/service/data/{series}?lastNObservations={n}&format=csvdata"
    df = pd.read_csv(io.StringIO(requests.get(u, timeout=60, headers={"User-Agent": UA}).text))
    return list(zip(df["TIME_PERIOD"].astype(str), df["OBS_VALUE"].astype(float)))


def download(log=print) -> dict:
    d: dict = {"letoltve": f"{dt.datetime.now():%Y-%m-%d %H:%M}", "hibak": []}
    log("allampapir.hu: kínálat, kamatok, kincstári árfolyamok, ajánlattételek…")
    d["allampapir"] = fetch_allampapir(log)
    for key, fn, label in [("ksh_cpi", fetch_ksh_cpi, "KSH infláció"),
                           ("hicp_eves", fetch_hicp_eves, "Eurostat HICP"),
                           ("euribor3m", lambda: fetch_ecb("FM/M.U2.EUR.RT.MM.EURIBOR3MD_.HSTA", 6), "ECB EURIBOR"),
                           ("eurhuf", lambda: fetch_ecb("EXR/D.HUF.EUR.SP00.A", 5), "ECB EUR/HUF")]:
        log(f"{label}…")
        try:
            d[key] = fn()
        except Exception as e:  # noqa: BLE001 – a makroadat hiánya esetén a config értékei maradnak
            d[key] = None
            d["hibak"].append(f"{label}: {e}")
            log(f"  ! {label} nem tölthető le: {e}")
    return d


def load(frissit: bool = False, log=print) -> dict:
    """A mai cache, ha van; különben letöltés. Ha a letöltés nem sikerül, a legutóbbi cache (figyelmeztetéssel)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    today = CACHE / f"adatok_{dt.date.today():%Y-%m-%d}.json"
    if today.exists() and not frissit:
        return json.loads(today.read_text(encoding="utf-8"))
    try:
        d = download(log)
    except Exception as e:  # noqa: BLE001
        old = sorted(CACHE.glob("adatok_*.json"))
        if not old:
            raise
        log(f"! Letöltési hiba ({e}); a legutóbbi cache-t használom: {old[-1].name}")
        d = json.loads(old[-1].read_text(encoding="utf-8"))
        d.setdefault("hibak", []).append(f"letöltés: {e} → régi cache: {old[-1].name}")
        return d
    today.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", action="version", version=verzio.cimke())
    ap.add_argument("--frissit", action="store_true", help="a mai cache ellenére újratölt")
    a = ap.parse_args()
    d = load(a.frissit)
    r = pd.DataFrame(d["allampapir"]["retail"])
    kaphato = r[r["underIssue"] == True]  # noqa: E712
    print(f"Letöltve: {d['letoltve']}. Sorozat-periódus: {len(r)}, most kapható: {len(kaphato)}")
    print(kaphato[["type", "name", "rate", "ehm", "maturity"]].to_string(index=False))
    print("Ajánlattételek:", {k: [(o["name"], o.get("premium")) for o in v] for k, v in d["allampapir"]["ajanlattetel"].items()})
    k = d.get("ksh_cpi") or {}
    print("KSH éves:", k.get("eves"), "| YTD:", k.get("ytd", {}).get(str(dt.date.today().year)))
    print("HICP éves:", d.get("hicp_eves"), "| EURIBOR:", d.get("euribor3m"), "| EUR/HUF:", d.get("eurhuf"))
    for h in d.get("hibak", []):
        print("HIBA:", h)


if __name__ == "__main__":
    main()
