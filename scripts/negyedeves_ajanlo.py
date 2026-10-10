"""Negyedéves állampapír-ajánló: átnézi a tartott sorozatokat és a most kapható kínálatot, vetíti a kamatokat
(infláció, DKJ, EURIBOR), és javaslatot tesz: tartás / csere (mikor) / új pénz és lejáró összegek befektetése.

Bemenet:  config/strategia.yaml, allampapir.hu + KSH + Eurostat + ECB (napi cache), private/holdings.csv, private/cash.csv
Kimenet:  private/reports/ajanlo_ÉÉÉÉ-QN.md  (BIZALMAS – a portfólióadatokat tartalmazza)
Futtatás (a projekt gyökeréből):  python scripts/negyedeves_ajanlo.py [--frissit]

Modell (egyszerűsített, összehasonlításra):
- Minden sorozat kamatperiódusokra bomlik. A múltbeli és a folyó periódus kamata ismert (lakossági kamattábla);
  a jövőbelieket a periódushossz ismétlésével, típusonként vetítjük:
    PMÁP:  KSH éves átlagos infláció (a periódus kezdőévét megelőző év) + prémium
    PEMÁP: euróövezeti éves átlagos HICP (előző év) + prémium  (csak éves periódusú sorozatnál; a félévesnél marad a kamat)
    BMÁP:  3 havi DKJ-átlag + prémium (ha nincs DKJ-feltevés, a mostani kamat marad)
    EMÁP:  3 havi EURIBOR (+ prémium)
    FixMÁP: fix;  MÁP Plusz: az ajánlattétel sávos kamatai
  A prémium = ismert kamat − az alapjául szolgáló infláció (tartott sorozatnál), illetve a nyilvános ajánlattételből.
- Várható átlagkamat egy időablakra = a periódus-kamatok időarányos átlaga (kamatos kamat nélkül).
- Csere-nyereség az ablakon (a névérték %-ában) = ∫ alternatíva − ∫ tartott − visszaváltási költség (100 − kincstári
  vételi nettó árfolyam). Az eladás lehetséges napjai: ma, illetve a tartott papír későbbi kamatfordulói.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import allampapir_adatok as ad  # noqa: E402

D = dt.date
NEV = {"PMÁP": "Prémium Magyar Állampapír", "PEMÁP": "Prémium Euró Magyar Állampapír",
       "BMÁP": "Bónusz Magyar Állampapír", "FixMÁP": "Fix Magyar Állampapír", "MÁP Plusz": "Magyar Állampapír Plusz",
       "EMÁP": "Euró Magyar Állampapír"}
ISMERT_TIPUSOK = {"PMÁP", "PEMÁP", "BMÁP", "FixMÁP", "MÁP Plusz", "EMÁP"}  # ezeknek ismeri a kamatképletét
ISMERT_FAJL = ROOT / "data/allampapir/ismert.json"
UJ_ABLAK_NAP = 92  # ennyi napig jelzi az újdonságot / megszűnést (kb. egy negyedév)
LIKVID = {"MÁP Plusz": "évente 5 munkanapig 100%-on, egyébként 99%-on visszaváltható",
          "FixMÁP": "99%-on visszaváltható", "PMÁP": "99%-on visszaváltható", "BMÁP": "99%-on visszaváltható",
          "EMÁP": "99%-on visszaváltható", "PEMÁP": "99%-on visszaváltható"}


# ---------- dátum / formázás ----------

def pdate(s) -> D | None:
    if s is None or (isinstance(s, float) and math.isnan(s)) or str(s) in ("", "None", "nan"):
        return None
    s = str(s).strip().rstrip(".").replace(".", "-")
    try:
        return D.fromisoformat(s[:10])
    except ValueError:
        return None


def add_months(d: D, m: int) -> D:
    y, mo = divmod(d.month - 1 + m, 12)
    y, mo = d.year + y, mo + 1
    for day in (d.day, 30, 29, 28):
        try:
            return D(y, mo, day)
        except ValueError:
            continue
    raise ValueError(d)


def yrs(a: D, b: D) -> float:
    return (b - a).days / 365.25


def ft(x: float | None) -> str:
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:,.0f}".replace(",", " ")


def eur(x: float | None) -> str:
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:,.0f}".replace(",", " ")


def pct(x: float | None, d: int = 2) -> str:
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{d}f}%".replace(".", ",")


def quarter(d: D) -> str:
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


# ---------- piaci feltevések ----------

class Piac:
    def __init__(self, data: dict, cfg: dict, today: D):
        self.d, self.cfg, self.today = data, cfg, today
        self.ksh = data.get("ksh_cpi") or {}
        self.hicp = {int(k): v for k, v in (data.get("hicp_eves") or {}).items()}
        self.notes: list[str] = []

    def infl(self, ccy: str, year: int) -> tuple[float, str]:
        """Éves átlagos infláció (%) és a forrása."""
        if ccy == "HUF":
            eves = {int(k): v for k, v in (self.ksh.get("eves") or {}).items()}
            if year in eves:
                return eves[year], "KSH"
        elif year in self.hicp:
            return self.hicp[year], "Eurostat"
        conf = self.cfg["inflacio"][ccy]
        v = conf.get(year, conf.get(str(year)))
        if v is None and ccy == "HUF" and year == self.today.year:
            ytd = (self.ksh.get("ytd") or {}).get(str(year))
            if ytd:
                return ytd["atlag"], f"KSH jan–{ytd['honap']}. hó átlag"
        if v is None:
            v = conf["alap"]
        return float(v), "feltevés"

    def euribor(self) -> float | None:
        if self.cfg.get("euribor_3h") is not None:
            return float(self.cfg["euribor_3h"])
        e = self.d.get("euribor3m")
        return e[-1][1] if e else None

    def eurhuf(self, fallback: float | None) -> float:
        e = self.d.get("eurhuf")
        return e[-1][1] if e else (fallback or 400.0)


# ---------- sorozat-modell ----------

@dataclass
class Periodus:
    start: D
    end: D          # kizárólagos: ezen a napon fizet kamatot / ekkor fordul
    rate: float
    ismert: bool
    forras: str = ""


@dataclass
class Sorozat:
    tipus: str
    nev: str
    ccy: str
    lejarat: D
    periodusok: list[Periodus] = field(default_factory=list)
    premium: float | None = None
    megj: list[str] = field(default_factory=list)

    @property
    def cimke(self) -> str:
        return f"{self.tipus} {self.nev.replace('_EUR', '')}"

    def rate_at(self, d: D) -> float | None:
        for p in self.periodusok:
            if p.start <= d < p.end:
                return p.rate
        return None

    def current(self, d: D) -> Periodus | None:
        return next((p for p in self.periodusok if p.start <= d < p.end), None)

    def next_period(self, d: D) -> Periodus | None:
        return next((p for p in self.periodusok if p.start > d), None)

    def integral(self, a: D, b: D) -> float:
        """∫ kamat dt  (kamat% × év) az [a, b) ablakon; a lejárat után 0."""
        tot = 0.0
        for p in self.periodusok:
            lo, hi = max(a, p.start), min(b, p.end)
            if hi > lo:
                tot += p.rate * yrs(lo, hi)
        return tot

    def avg(self, a: D, b: D) -> float | None:
        b = min(b, self.lejarat)
        return self.integral(a, b) / yrs(a, b) if b > a else None


def retail_df(data: dict) -> pd.DataFrame:
    r = pd.DataFrame(data["allampapir"]["retail"])
    r["vf"] = r["validFrom"].map(pdate)
    r["vt"] = r["validTo"].map(pdate)
    r["mat"] = r["maturity"].map(pdate)
    # rate_val hiányzik pl. a PEMÁP-nál → a szöveges 'rate'; sávos kamatnál az alsó érték
    r["r"] = [ad.num(v) if ad.num(v) is not None else (ad.num(t) if ad.num(t) is not None else float("nan"))
              for v, t in zip(r["rate_val"], r["rate"])]
    r["ehm_v"] = r["ehm_val"].map(ad.num)
    return r


def offering(data: dict, place: str, nev: str) -> dict:
    """A sorozat nyilvános ajánlattételéből kiolvasott feltételek. place = a termék short_name-je (PMAP, FIX_MAP…)."""
    for o in (data["allampapir"].get("ajanlattetel") or {}).get(place or "", []):
        if o["name"] == nev and ("premium" in o or "savos" in o or "elso_periodus_kamat" in o):
            return o
    return {}


ALAP_LEPES = {"PMÁP": 12, "PEMÁP": 12, "BMÁP": 3, "EMÁP": 3, "FixMÁP": 3}


def step_months(p_start: D, p_end: D) -> int:
    m = round((p_end - p_start).days / 30.44)
    return min((3, 6, 12), key=lambda x: abs(x - m))


def build(tipus: str, nev: str, r: pd.DataFrame, data: dict, piac: Piac, cfg: dict) -> Sorozat | None:
    rows = r[(r["type"] == tipus) & (r["name"] == nev)].sort_values("vf")
    if rows.empty:
        return None
    last = rows.iloc[-1]
    ccy = "EUR" if nev.endswith("_EUR") or tipus in ("PEMÁP", "EMÁP") else "HUF"
    mat = last["mat"] or (last["vt"] + dt.timedelta(days=1) if last["vt"] else None)
    if mat is None:  # lejárat nélküli (pl. KTJ): nem modellezhető
        return None
    s = Sorozat(tipus, nev, ccy, mat)
    off = offering(data, last.get("place"), nev)
    if tipus not in ISMERT_TIPUSOK:
        s.megj.append("ÚJ/ismeretlen típus: a kamatképletét a program nem ismeri, a mostani kamattal (EHM-mel) számol")

    if tipus == "MÁP Plusz" and off.get("savos"):  # sávos fix: az ajánlattétel adja a teljes pályát
        s.periodusok = [Periodus(pdate(a), pdate(b), v, True, "ajánlattétel") for a, b, v in off["savos"]]
        return s
    if tipus == "FixMÁP":  # fix, negyedéves kamat; a kamattábla csak az értékesítési időszakot adja →
        rate = last["r"] if not math.isnan(last["r"]) else last["ehm_v"]  # a periódusok a lejárattól visszafelé
        kezd = rows.iloc[0]["vf"] or piac.today
        end = mat
        while end > kezd:
            st = add_months(end, -3)
            s.periodusok.insert(0, Periodus(max(st, kezd), end, rate, True, "fix"))
            end = st
        return s
    if tipus == "MÁP Plusz" or last["vt"] is None:  # nincs periódus-adat → a kamat/EHM a lejáratig
        start = last["vf"] or piac.today
        rate = last["r"] if not math.isnan(last["r"]) else last["ehm_v"]
        if tipus == "MÁP Plusz":
            rate = last["ehm_v"] or rate
            s.megj.append("sávos kamat, az EHM-mel számolva")
        s.periodusok = [Periodus(min(start, piac.today), mat, rate, True, "fix")]
        return s

    for _, x in rows.iterrows():
        if x["vf"] and x["vt"]:
            s.periodusok.append(Periodus(x["vf"], x["vt"] + dt.timedelta(days=1), x["r"], True, "kamattábla"))
    if not s.periodusok:
        return None
    lp = s.periodusok[-1]
    # az első periódus gyakran rendhagyó (pl. PMÁP 2036/I1: 7 hónap) → egyetlen ismert periódusnál a típus szokásos lépése
    step = step_months(lp.start, lp.end) if len(s.periodusok) > 1 else ALAP_LEPES.get(tipus, step_months(lp.start, lp.end))

    # prémium: ajánlattétel > kézi > levezetés az utolsó ismert éves periódusból
    kezi = (cfg.get("premium_kezi") or {}).get(f"{tipus} {nev.replace('_EUR', '')}")
    if off.get("premium") is not None:
        s.premium = off["premium"]
    elif kezi is not None:
        s.premium = float(kezi)
    elif tipus in ("PMÁP", "PEMÁP") and step == 12:
        base, _ = piac.infl(ccy, lp.start.year - 1)
        s.premium = round(lp.rate - max(base, 0), 2)
    elif tipus == "EMÁP":
        s.premium = 0.0

    def projected(start: D) -> tuple[float, str]:
        if tipus in ("PMÁP", "PEMÁP") and step == 12 and s.premium is not None:
            base, src = piac.infl(ccy, start.year - 1)
            return round(max(base, 0) + s.premium, 2), f"{start.year - 1}. évi infláció {pct(base, 1)} ({src}) + {pct(s.premium)}"
        if tipus == "BMÁP" and cfg.get("dkj_3h") is not None:
            prem = s.premium if s.premium is not None else lp.rate - bmap_base(r, data)
            return round(float(cfg["dkj_3h"]) + prem, 2), f"DKJ-feltevés {pct(cfg['dkj_3h'])} + {pct(prem)}"
        if tipus == "EMÁP" and piac.euribor() is not None:
            return round(piac.euribor() + (s.premium or 0), 3), f"EURIBOR 3 hó {pct(piac.euribor(), 3)}"
        if tipus == "FixMÁP":
            return lp.rate, "fix"
        return lp.rate, "a mostani kamat marad (feltevés)"

    cur = lp.end
    while cur < mat:
        nxt = min(add_months(cur, step), mat)
        if (mat - nxt).days < 20:  # a lejárat napja a kamatnaptárban néha 1-2 nappal eltér
            nxt = mat
        rate, src = projected(cur)
        s.periodusok.append(Periodus(cur, nxt, rate, False, src))
        cur = nxt
    if s.periodusok[-1].end < mat:
        s.periodusok[-1].end = mat
    return s


def bmap_base(r: pd.DataFrame, data: dict) -> float:
    """A most kapható BMÁP kamatbázisa (kamat − prémium) ≈ a 3 havi DKJ aukciós átlaghozam."""
    for _, x in r[(r["type"] == "BMÁP") & (r["underIssue"] == True)].iterrows():  # noqa: E712
        o = offering(data, x["place"], x["name"])
        if o.get("premium") is not None:
            return x["r"] - o["premium"]
    return float("nan")


# ---------- portfólió ----------

def load_portfolio(holdings: Path, cash: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    return pd.read_csv(holdings), pd.read_csv(cash)


def exit_cost(s: Sorozat, d: D, cfg: dict) -> float:
    """Visszaváltási költség a névérték %-ában a d napon (lejáratkor 0)."""
    if d >= s.lejarat:
        return 0.0
    v = cfg["visszavaltas"]
    if s.tipus == "MÁP Plusz":
        for p in s.periodusok:
            if p.end <= d < p.end + dt.timedelta(days=7):  # ~5 munkanap
                return 100 - v["MAP_Plusz_ablakban"]
    return 100 - v["alap"]


def switch_analysis(h: Sorozat, alts: list[Sorozat], today: D, horizon_end: D, cfg: dict) -> dict:
    """A legjobb (alternatíva, eladási nap) pár a tartott sorozatra. Nyereség a névérték %-ában az ablakon."""
    end = min(horizon_end, h.lejarat)
    if end <= today:
        return {"gain": 0, "ann": 0}
    hold = h.integral(today, end)
    cands = [today] + [p.start for p in h.periodusok if today < p.start < end]
    best = {"gain": -1e9}
    for a in alts:
        for sd in cands:
            if a.lejarat <= sd:
                continue
            g = h.integral(today, sd) + a.integral(sd, end) - hold - exit_cost(h, sd, cfg)
            if g > best["gain"]:
                best = {"gain": g, "alt": a, "datum": sd, "koltseg": exit_cost(h, sd, cfg)}
    best["ann"] = best["gain"] / yrs(today, end)
    best["ablak_vege"] = end
    best["tartas_atlag"] = hold / yrs(today, end)
    return best


# ---------- riport ----------

def run(cfg: dict, holdings: Path, cash_file: Path, frissit: bool = False, log=print) -> tuple[str, str]:
    today = D.today()
    data = ad.load(frissit, log)
    piac = Piac(data, cfg, today)
    r = retail_df(data)
    h, c = load_portfolio(holdings, cash_file)
    horizon_end = add_months(today, int(round(cfg["horizont_ev"] * 12)))
    exp_eurhuf = None
    eur_rows = h[(h["ccy"] == "EUR") & h["nevertek"].gt(0)]
    if not eur_rows.empty:
        exp_eurhuf = float((eur_rows["piaci_ertek_huf"] / (eur_rows["nevertek"] * eur_rows["arfolyam_pct"] / 100)).mean())
    fx = piac.eurhuf(exp_eurhuf)
    warn = list(data.get("hibak", []))

    # tartott sorozatok
    held: list[tuple[pd.Series, Sorozat | None]] = []
    for _, x in h.iterrows():
        s = build(x["tipus"], x["sorozat"], r, data, piac, cfg) if isinstance(x["tipus"], str) else None
        if s is None:
            warn.append(f"Nincs kamatadat ehhez: {x['nev']} – csak listázom.")
        held.append((x, s))

    # most kapható kínálat: minden, ami nincs kizárva (egy új terméktípus is)
    offers: dict[str, list[Sorozat]] = {"HUF": [], "EUR": []}
    kaphato = r[(r["underIssue"] == True) & ~r["type"].isin(cfg.get("kizart_tipusok") or [])]  # noqa: E712
    for _, x in kaphato.drop_duplicates(["type", "name"]).iterrows():
        s = build(x["type"], x["name"], r, data, piac, cfg)
        if s is None:
            warn.append(f"Kapható, de nem modellezhető (nincs lejárat/kamat): {x['type']} {x['name']}")
        elif s.lejarat > today:
            offers[s.ccy].append(s)
    uj = ujdonsagok(data, r, today, cfg.get("kizart_tipusok") or [])

    def rank(ccy: str) -> list[tuple[Sorozat, float]]:
        out = [(s, s.avg(today, horizon_end)) for s in offers[ccy]]
        return sorted([o for o in out if o[1] is not None], key=lambda o: -o[1])

    best = {ccy: rank(ccy) for ccy in offers}
    lines: list[str] = []
    w = lines.append
    q = quarter(today)
    total_papir = float(h["piaci_ertek_huf"].sum())
    total_penz = float(c["huf"].sum()) if "huf" in c else 0.0

    # ---- teendők és csereelemzés ----
    actions: list[dict] = []
    csere_sorok, tart_sorok = [], []
    for x, s in held:
        if s is None:
            continue
        cur = s.current(today)
        alts = [a for a, _ in best[s.ccy] if not (a.tipus == s.tipus and a.nev == s.nev)]
        sw = switch_analysis(s, alts, today, horizon_end, cfg) if alts else {"gain": 0, "ann": 0}
        nyp = s.next_period(today)
        tart_sorok.append((x, s, cur, nyp, sw))
        if alts and sw["ann"] >= cfg["csere"]["min_elony_pp"]:
            ertek = x["nevertek"]
            mikor = "most" if sw["datum"] == today else f"{sw['datum']:%Y-%m-%d} (kamatforduló) után"
            actions.append({"datum": sw["datum"], "muvelet": "CSERE", "mit": s.cimke, "mire": sw["alt"].cimke,
                            "osszeg": f"{ft(ertek) if s.ccy == 'HUF' else eur(ertek)} {s.ccy} névérték",
                            "indok": f"{mikor}: a horizonton évi ~{pct(sw['ann'])} pont többlet a {pct(sw['koltseg'], 1)} "
                                     f"visszaváltási költség után",
                            "tobblet": ertek * sw["gain"] / 100, "ccy": s.ccy, "vege": sw["ablak_vege"]})
            csere_sorok.append((x, s, sw))
        if s.lejarat <= horizon_end and s.lejarat > today and not (alts and sw["ann"] >= cfg["csere"]["min_elony_pp"]):
            utolso = s.rate_at(s.lejarat - dt.timedelta(days=1)) or 0
            p_last = next((p for p in s.periodusok if p.end == s.lejarat), None)
            kamat = x["nevertek"] * utolso / 100 * yrs(p_last.start, p_last.end) if p_last else 0
            if s.lejarat <= add_months(today, 12):
                tgt_s = best[s.ccy][0][0] if best[s.ccy] else None
                tgt = tgt_s.cimke if tgt_s else "–"
                ujra_vege = min(horizon_end, tgt_s.lejarat) if tgt_s else s.lejarat
                actions.append({"datum": s.lejarat, "muvelet": "LEJÁRAT → ÚJRABEFEKTETÉS", "mit": s.cimke, "mire": tgt,
                                "tobblet": (x["nevertek"] + kamat) * tgt_s.integral(s.lejarat, ujra_vege) / 100 if tgt_s else None,
                                "ccy": s.ccy, "vege": ujra_vege, "amt": x["nevertek"] + kamat,
                                "osszeg": f"~{ft(x['nevertek'] + kamat) if s.ccy == 'HUF' else eur(x['nevertek'] + kamat)} {s.ccy}",
                                "indok": "a tőke és az utolsó kamat a pénzszámlára érkezik; az akkori kínálatból a legjobb "
                                         "(a következő negyedéves futás újraszámolja)"})

    # szabad készpénz
    for ccy in ("HUF", "EUR"):
        amt = float(c.loc[c["ccy"] == ccy, "amount"].sum())
        free = amt - float(cfg["tartalek"].get(ccy, 0))
        if free >= cfg["min_vasarlas"][ccy] and best[ccy]:
            tgt, y = best[ccy][0]
            actions.append({"datum": today, "muvelet": "VÉTEL", "mit": f"{ccy}-készpénz", "mire": tgt.cimke,
                            "osszeg": f"{ft(free) if ccy == 'HUF' else eur(free)} {ccy}",
                            "indok": f"szabad pénz (tartalék: {ft(cfg['tartalek'].get(ccy, 0))} {ccy}); várható átlagkamat {pct(y)}",
                            "tobblet": free * tgt.integral(today, horizon_end) / 100, "ccy": ccy, "amt": free,
                            "vege": min(horizon_end, tgt.lejarat)})
    total_huf = float(h["piaci_ertek_huf"].sum()) + (float(c["huf"].sum()) if "huf" in c else 0.0)
    eur_huf_most = float(h.loc[h["ccy"] == "EUR", "piaci_ertek_huf"].sum()) + float(c.loc[c["ccy"] == "EUR", "huf"].sum())
    actions, deviza_terv = deviza_igazitas(actions, cfg, best, eur_huf_most / fx, total_huf, fx, horizon_end)
    uj_tipusu = {s.cimke for ss in offers.values() for s in ss if s.tipus not in ISMERT_TIPUSOK}
    for a in actions:
        if a["mire"] in uj_tipusu:
            a["indok"] += ". ⚠️ ÚJ terméktípus: a kamata csak a mostani szinttel van számolva, kötés előtt nézd meg a feltételeit"
    actions.sort(key=lambda a: a["datum"])

    # ---- összefoglaló ----
    w(f"# Negyedéves állampapír-ajánló, {q} ({today:%Y-%m-%d})")
    w("")
    w("> BIZALMAS: portfólióadatokat tartalmaz. Ez modellszámítás, nem befektetési tanácsadás. Kötés előtt nézd meg "
      "a WebKincstárban az aktuális kínálatot és árfolyamot.")
    w("")
    w("## Összefoglaló")
    huf_papir = float(h.loc[h["ccy"] == "HUF", "piaci_ertek_huf"].sum())
    eur_papir = float(h.loc[h["ccy"] == "EUR", "piaci_ertek_huf"].sum())
    w(f"- Portfólió: **{ft(total_papir + total_penz)} Ft**: állampapír {ft(total_papir)} Ft "
      f"(HUF {ft(huf_papir)}, EUR {ft(eur_papir)} Ft-ban), pénz {ft(total_penz)} Ft. Az EUR/HUF árfolyam {fx:.2f} (ECB).")
    wsum, wcur, wexp = 0.0, 0.0, 0.0
    for x, s, cur, nyp, sw in tart_sorok:
        if cur:
            wsum += x["piaci_ertek_huf"]
            wcur += x["piaci_ertek_huf"] * cur.rate
            wexp += x["piaci_ertek_huf"] * (s.avg(today, add_months(today, 12)) or cur.rate)
    if wsum:
        w(f"- Súlyozott kamat most: **{pct(wcur / wsum)}**. A következő 12 hónapra várható: **{pct(wexp / wsum)}**, "
          "a kamatfordulók vetítésével.")
    for ccy in ("HUF", "EUR"):
        if best[ccy]:
            b, y = best[ccy][0]
            w(f"- Legjobb most kapható {ccy}-papír a {cfg['horizont_ev']} éves horizonton: **{b.cimke}**, várható átlagkamat {pct(y)}.")
    hu_cur, hu_src = piac.infl("HUF", today.year)
    hu_elozo = piac.infl("HUF", today.year - 1)[0]
    d_inf = hu_cur - hu_elozo
    dd = f"{abs(d_inf):.1f}".replace(".", ",")
    irany = (f"kb. {dd} ponttal csökkennek" if d_inf < -0.25 else
             f"kb. {dd} ponttal nőnek" if d_inf > 0.25 else "nagyjából változatlanok")
    w(f"- A PMÁP-ok {today.year + 1}. évi kamata a {today.year}. évi átlaginflációtól függ: **{pct(hu_cur, 1)}** ({hu_src}); "
      f"{today.year - 1}-ben {pct(hu_elozo, 1)} volt. A PMÁP-kamatok a következő fordulón {irany}.")
    w(f"- Javasolt lépések száma: {len(actions)} (lásd alább).")
    if any(uj.values()):
        w("- 🆕 **Változott a kínálat** (lásd: Újdonságok).")
    w("")

    dv = cfg.get("deviza") or {}
    if dv.get("cel_eur_arany") is not None:
        cel, sav = float(dv["cel_eur_arany"]), float(dv.get("sav_pp", 5))
        most = eur_huf_most / total_huf * 100 if total_huf else 0
        w("## Devizaarány")
        allapot = ("a sávon belül" if cel - sav <= most <= cel + sav else
                   "a sáv felett: a felszabaduló EUR egy része forintba megy" if most > cel + sav else
                   "a sáv alatt: a felszabaduló HUF egy része euróba megy")
        w(f"- EUR-arány most: **{pct(most, 1)}** ({ft(eur_huf_most)} Ft a {ft(total_huf)} Ft-ból). Cél: {pct(cel, 0)}, "
          f"sáv: {pct(cel - sav, 0)}–{pct(cel + sav, 0)}. Állapot: {allapot}.")
        w("- Igazítás lejárat előtti eladás nélkül: csak a szabad pénz és a lejáró összegek devizáját választja meg a program. "
          f"Váltási költség feltevés: {pct(dv.get('valtas_koltseg_pct', 0.5), 1)} (banki árfolyamrés; a Kincstár nem vált, "
          "az összeget bankszámlára kell utalni, és ott váltani).")
        if deviza_terv:
            w("- A terv: " + "; ".join(deviza_terv) + ".")
        if best["HUF"] and best["EUR"]:
            d = best["HUF"][0][1] - best["EUR"][0][1]
            be = fx * (1 + d / 100) ** cfg["horizont_ev"]
            w(f"- Fedezeti pont: a legjobb HUF-papír ({pct(best['HUF'][0][1])}) és a legjobb EUR-papír ({pct(best['EUR'][0][1])}) "
              f"különbsége évi {pct(d)}. Az euró akkor jár jobban, ha a forint évente ennél többet gyengül, vagyis "
              f"{cfg['horizont_ev']} év múlva az EUR/HUF **{be:.0f}** felett van (most {fx:.0f}).")
        w("")

    if any(uj.values()):
        w("## Újdonságok a kínálatban")
        w(f"Az elmúlt {UJ_ABLAK_NAP} nap változásai (nyilvántartás: data/allampapir/ismert.json). Az új sorozatok "
          "automatikusan bekerültek a vizsgálatba. Egy új terméktípust a program a mostani kamatával számol, mert a "
          "kamatképletét nem ismeri.")
        w("")
        for cim, key in (("Új terméktípus", "uj_termek"), ("Megszűnt termék", "megszunt_termek"),
                         ("Új sorozat", "uj_sorozat"), ("Már nem kapható sorozat", "megszunt_sorozat")):
            for x in uj[key]:
                w(f"- **{cim}:** {x}")
        w("")

    w("## Javasolt lépések")
    if actions:
        w("| mikor | művelet | mit | mibe | összeg | várható nominális többlet / év | indok |")
        w("|---|---|---|---|---|---|---|")
        ossz = 0.0
        for a in actions:
            t = a.get("tobblet")
            if t is not None:  # 1 évre vetítve: a teljes időszak többlete / az időszak hossza
                ev = yrs(a["datum"], a["vege"])
                t = t / ev if ev > 0 else None
            tx = "–" if t is None else (f"+{ft(t)} Ft" if a["ccy"] == "HUF" else f"+{eur(t)} EUR (~{ft(t * fx)} Ft)")
            if t is not None:
                ossz += t * (fx if a["ccy"] == "EUR" else 1)
            w(f"| {a['datum']:%Y-%m-%d} | {a['muvelet']} | {a['mit']} | {a['mire']} | {a['osszeg']} | **{tx}** | {a['indok']} |")
        w("")
        w(f"Várható nominális többlet összesen: **~{ft(ossz)} Ft / év** (az EUR {fx:.2f} árfolyamon). 1 évre vetítve: "
          "a lépés kezdetétől a horizont végéig (vagy a lejáratig) várható többlet, osztva az időszak éveinek számával. "
          "Mihez képest? CSERE: a tartott papír megtartásához képest, a visszaváltási költség után (a költség az "
          "időszakra elosztva). VÉTEL és ÚJRABEFEKTETÉS: a kamat nélkül álló pénzhez képest. Becsült, a vetített "
          "kamatokkal, kamatos kamat nélkül. A jövőbeli lépések többlete csak a lépés napjától számít.")
        w("")
        w("> A jövőbeli dátumú cserék tervek. A kínálat és a kamatok addig változhatnak, ezért a következő negyedéves "
          "futás újraszámolja őket. A visszaváltás a WebKincstárban történik: nettó 99% + felhalmozott kamat. "
          "A kamatot ilyenkor is megkapod, a költség csak a nettó 1%.")
    else:
        w("Nincs teendő: minden tartott papír marad, és nincs befektetendő szabad pénz.")
    w("")

    # ---- tartott papírok ----
    w("## Tartott papírok")
    w(f"A várható átlag a ma és a horizont vége ({horizon_end:%Y-%m-%d}) közötti időre vonatkozik, de legfeljebb a lejáratig.")
    w("")
    w("| papír | névérték | piaci érték (Ft) | kamat most | periódus vége | következő kamat (becslés) | várható átlag | lejárat | legjobb csere | csere-előny/év | javaslat |")
    w("|---|---|---|---|---|---|---|---|---|---|---|")
    for x, s, cur, nyp, sw in tart_sorok:
        ny = f"{pct(nyp.rate)}{'' if nyp.ismert else ' *'}" if nyp else "– (lejár)"
        alt = f"{sw['alt'].cimke}, {sw['datum']:%Y-%m-%d}" if sw.get("alt") and sw["ann"] > 0 else "–"
        jav = "CSERE" if sw.get("alt") and sw["ann"] >= cfg["csere"]["min_elony_pp"] else "tartás"
        w(f"| {s.cimke} | {ft(x['nevertek'])} {s.ccy} | {ft(x['piaci_ertek_huf'])} | {pct(cur.rate) if cur else '–'} | "
          f"{cur.end - dt.timedelta(days=1) if cur else '–'} | {ny} | {pct(sw.get('tartas_atlag'))} | {s.lejarat} | {alt} | "
          f"{pct(sw['ann']) if sw.get('alt') and sw['ann'] > 0 else '–'} | **{jav}** |")
    for x, s in held:
        if s is None:
            w(f"| {x['nev']} | {ft(x['nevertek'])} {x['ccy']} | {ft(x['piaci_ertek_huf'])} | – | – | – | – | – | – | – | csak listázva |")
    w("")
    w("\\* becsült: a kamatforduló után a vetített kamat (feltevések lent).")
    w("")

    # ---- kamatpályák ----
    w("### Kamatpályák (ismert és vetített periódusok)")
    for x, s, cur, nyp, sw in tart_sorok:
        per = [p for p in s.periodusok if p.end > today][:6]
        w(f"- **{s.cimke}** (prémium: {pct(s.premium) if s.premium is not None else '–'}): " + "; ".join(
            f"{p.start:%Y-%m-%d}→{p.end:%Y-%m-%d} {pct(p.rate)}{'' if p.ismert else ' (' + p.forras + ')'}" for p in per)
          + (" …" if len([p for p in s.periodusok if p.end > today]) > 6 else ""))
    w("")

    # ---- csere részletek ----
    if csere_sorok:
        w("## Csere: részletek")
        for x, s, sw in csere_sorok:
            a = sw["alt"]
            w(f"### {s.cimke} → {a.cimke}")
            w(f"- Tartva a horizonton (a lejáratig, de legfeljebb {sw['ablak_vege']:%Y-%m-%d}-ig) a várható átlagkamat "
              f"{pct(sw['tartas_atlag'])}. Az alternatíva {a.cimke}, a csere napja {sw['datum']:%Y-%m-%d}.")
            w(f"- Visszaváltási költség: a névérték {pct(sw['koltseg'], 1)}-a, kb. {ft(x['nevertek'] * sw['koltseg'] / 100)} {s.ccy}. "
              f"Nettó többlet az ablakon: a névérték {pct(sw['gain'])}-a, kb. **{ft(x['nevertek'] * sw['gain'] / 100)} {s.ccy}**, "
              f"ami évi {pct(sw['ann'])} pont.")
            if sw["datum"] > today:
                cr = s.rate_at(today)
                w(f"- Miért nem most? A mostani periódus {pct(cr)} kamata a fordulóig jobb vagy közel azonos, "
                  "ezért érdemes kivárni. Az eladás a forduló után már a felhalmozott kamat nélkül, nettó 99%-on történik.")
            w("")

    # ---- kínálat ----
    w("## Most kapható papírok")
    w(f"Átlagkamat: a {cfg['horizont_ev']} éves horizonton (vagy a lejáratig), ha ma veszed. A kamatok a vetítéssel számolva.")
    w("")
    w("| papír | deviza | lejárat | kamat most | várható átlag | EHM (ÁKK) | prémium | likviditás | megjegyzés |")
    w("|---|---|---|---|---|---|---|---|---|")
    for ccy in ("HUF", "EUR"):
        for s, y in best[ccy]:
            ehm = r[(r["type"] == s.tipus) & (r["name"] == s.nev) & (r["underIssue"] == True)]["ehm_v"]  # noqa: E712
            ehm_v = ehm.iloc[0] if len(ehm) else None
            w(f"| {s.cimke} | {ccy} | {s.lejarat} | {pct(s.rate_at(today))} | **{pct(y)}** | {pct(ehm_v) if ehm_v else '–'} | "
              f"{pct(s.premium) if s.premium is not None else '–'} | {LIKVID.get(s.tipus, '')} | {'; '.join(s.megj)} |")
    w("")

    # ---- pénzáramlás ----
    w("## Pénzáramlás a következő 12 hónapban")
    w("A kamatok becslése: névérték × periódus-kamat × napok / 365. A vetített periódusoknál a kamat becsült.")
    w("")
    w("| dátum | papír | esemény | összeg | deviza |")
    w("|---|---|---|---|---|")
    cf_end = add_months(today, 12)
    sums = {"HUF": 0.0, "EUR": 0.0}
    events = []
    for x, s, cur, nyp, sw in tart_sorok:
        for p in s.periodusok:
            if today < p.end <= cf_end:
                kamat = x["nevertek"] * p.rate / 100 * (p.end - p.start).days / 365
                lejar = p.end >= s.lejarat
                ev = "lejárat + kamat" if lejar else "kamat" + ("" if p.ismert else " (becsült)")
                osszeg = kamat + (x["nevertek"] if lejar else 0)
                penz = not (s.tipus == "MÁP Plusz" and not lejar)  # a MÁP Plusz kamata papírban íródik jóvá
                if not penz:
                    ev += ", papírban jóváírva"
                events.append((p.end, s.cimke, ev, osszeg, s.ccy, penz))
    for d_, nev, ev, osszeg, ccy, penz in sorted(events):
        if penz:
            sums[ccy] += osszeg
        w(f"| {d_:%Y-%m-%d} | {nev} | {ev} | {ft(osszeg) if ccy == 'HUF' else eur(osszeg)} | {ccy} |")
    w("")
    w(f"Összesen 12 hónap alatt: {ft(sums['HUF'])} HUF és {eur(sums['EUR'])} EUR érkezik a pénzszámlára. Az elv: "
      "ami befolyik, az a kínálat legjobb papírjába megy; a deviza a cél EUR-arány szerint (lásd: Devizaarány).")
    w("")

    # ---- lejárati létra ----
    w("## Lejárati létra")
    lad: dict[int, dict] = {}
    for x, s, cur, nyp, sw in tart_sorok:
        lad.setdefault(s.lejarat.year, {"HUF": 0.0, "EUR": 0.0})[s.ccy] += x["nevertek"]
    w("| év | HUF névérték | EUR névérték |")
    w("|---|---|---|")
    for y in sorted(lad):
        w(f"| {y} | {ft(lad[y]['HUF'])} | {eur(lad[y]['EUR'])} |")
    w("")

    # ---- feltevések ----
    w("## Feltevések és adatok")
    for ccy, nev in (("HUF", "KSH fogyasztói árindex"), ("EUR", "euróövezeti HICP")):
        ys = range(today.year - 1, today.year + 3)
        w(f"- {nev}, éves átlag: " + ", ".join(f"{y}: {pct(piac.infl(ccy, y)[0], 1)} ({piac.infl(ccy, y)[1]})" for y in ys))
    eb = piac.euribor()
    w(f"- 3 havi EURIBOR: {pct(eb, 3) if eb is not None else '–'}"
      f"{' (config)' if cfg.get('euribor_3h') is not None else ' (ECB, legutóbbi havi átlag)'}. "
      f"BMÁP-kamatbázis (3 havi DKJ): {pct(cfg['dkj_3h']) + ' (config)' if cfg.get('dkj_3h') is not None else 'a mostani szint marad'}; "
      f"a most kapható BMÁP alapján {pct(bmap_base(r, data))}.")
    w(f"- Visszaváltás: nettó {pct(cfg['visszavaltas']['alap'], 1)} + felhalmozott kamat. MÁP Plusz a kamatjuttatás utáni "
      f"5 munkanapban {pct(cfg['visszavaltas']['MAP_Plusz_ablakban'], 1)}. Csereküszöb: évi {pct(cfg['csere']['min_elony_pp'], 1)} pont.")
    w(f"- Adatok letöltve: {data.get('letoltve')} (allampapir.hu, KSH, Eurostat, ECB). Portfólió: private/holdings.csv.")
    w("- Egyszerűsítések: kamatos kamat nélküli átlag; munkaszüneti napok nélkül. Az újrabefektetés (lejárat, kamat) "
      "a tartásnál és a cserénél azonos feltevéssel számol. A jövőbeli kínálat ismeretlen, ezért a terv a mai kínálattal számol.")
    for x in warn:
        w(f"- ⚠️ {x}")
    w("")
    return "\n".join(lines), q


def ujdonsagok(data: dict, r: pd.DataFrame, today: D, kizart: list[str]) -> dict:
    """Az ismert termékek és kapható sorozatok nyilvántartása (első és utolsó észlelés), és az elmúlt negyedév változásai.
    Az első futás csak felveszi a kiinduló állapotot, újdonságot nem jelez."""
    t = today.isoformat()
    elso_futas = not ISMERT_FAJL.exists()
    ism = {"kiindulas": t} if elso_futas else json.loads(ISMERT_FAJL.read_text(encoding="utf-8"))
    term, sor = ism.setdefault("termekek", {}), ism.setdefault("sorozatok", {})
    alap = {"kiindulo": True} if elso_futas else {}  # a kiinduló állapot tételei sosem "újak"
    for p in data["allampapir"]["papers"]:
        e = term.setdefault(p["short_name"], {"nev": p["long_name"], "elso": t, **alap})
        if p["aktiv"]:
            e["utolso_aktiv"] = t
    kaph = r[r["underIssue"] == True].drop_duplicates(["type", "name"])  # noqa: E712
    for _, x in kaph.iterrows():
        e = sor.setdefault(f"{x['type']} {x['name'].replace('_EUR', '')}",
                           {"elso": t, "kamat": x["rate"], "lejarat": x["maturity"], "tipus": x["type"], **alap})
        e["utolso"] = t
    ISMERT_FAJL.parent.mkdir(parents=True, exist_ok=True)
    ISMERT_FAJL.write_text(json.dumps(ism, ensure_ascii=False, indent=1), encoding="utf-8")

    hatar = (today - dt.timedelta(days=UJ_ABLAK_NAP)).isoformat()

    def friss(e: dict) -> bool:
        return e["elso"] >= hatar and not e.get("kiindulo") and e.get("tipus") not in kizart

    def elmult(d: str | None) -> bool:
        return d is not None and hatar <= d < t

    def nem_kizart(e: dict) -> bool:
        return e.get("tipus") not in kizart

    return {
        "uj_termek": [f"{e['nev']} ({k}), első észlelés: {e['elso']}" for k, e in term.items() if friss(e)],
        "megszunt_termek": [f"{e['nev']} ({k}), utoljára aktív: {e['utolso_aktiv']}" for k, e in term.items()
                            if elmult(e.get("utolso_aktiv"))],
        "uj_sorozat": [f"{k}: kamat {e['kamat']}, lejárat {e['lejarat']}, első észlelés: {e['elso']}"
                       for k, e in sor.items() if friss(e)],
        "megszunt_sorozat": [f"{k}, utoljára kapható: {e['utolso']}" for k, e in sor.items()
                             if elmult(e.get("utolso")) and nem_kizart(e)],
    }


def deviza_igazitas(actions: list[dict], cfg: dict, best: dict, eur_eur: float, total_huf: float, fx: float,
                    horizon_end: D) -> tuple[list[dict], list[str]]:
    """A cél EUR-arány tartása a pénzmozgásokkal: időrendben végigmegy a szabad pénz és a lejárat lépésein. Ha az
    EUR-arány a sáv felett van, az EUR-összeg (a célig szükséges része) forintba megy, ha alatta, a HUF-összeg euróba.
    Tartott papírt emiatt nem ad el. Az összérték és az árfolyam állandó (egyszerűsítés)."""
    dv = cfg.get("deviza") or {}
    if dv.get("cel_eur_arany") is None or not total_huf:
        return actions, []
    cel, sav = float(dv["cel_eur_arany"]) / 100, float(dv.get("sav_pp", 5)) / 100
    koltseg = float(dv.get("valtas_koltseg_pct", 0.5))
    out, terv = [], []
    for a in sorted(actions, key=lambda a: a["datum"]):
        amt, src = a.get("amt"), a.get("ccy")
        if amt is None or a["muvelet"] not in ("VÉTEL", "LEJÁRAT → ÚJRABEFEKTETÉS"):
            out.append(a)
            continue
        arany = eur_eur * fx / total_huf
        conv = 0.0  # a forrásdevizában
        if src == "EUR" and arany > cel + sav:
            conv = min(amt, eur_eur - cel * total_huf / fx)
        elif src == "HUF" and arany < cel - sav:
            conv = min(amt, (cel * total_huf / fx - eur_eur) * fx)
        dst = "HUF" if src == "EUR" else "EUR"
        if conv <= 0 or not best[dst]:
            out.append(a)
            continue
        maradek = amt - conv
        if maradek > 0:
            out.append(dict(a, amt=maradek, osszeg=f"~{ft(maradek) if src == 'HUF' else eur(maradek)} {src}",
                            tobblet=a["tobblet"] * maradek / amt if a.get("tobblet") is not None else None))
        tgt = best[dst][0][0]
        cel_amt = conv * fx if dst == "HUF" else conv / fx  # a céldevizában
        vege = min(horizon_end, tgt.lejarat)
        tobb = cel_amt * (tgt.integral(a["datum"], vege) - koltseg) / 100
        irany = "EUR → HUF" if dst == "HUF" else "HUF → EUR"
        out.append({"datum": a["datum"], "muvelet": f"VÁLTÁS {irany} + VÉTEL", "mit": a["mit"], "mire": tgt.cimke,
                    "osszeg": f"~{ft(conv) if src == 'HUF' else eur(conv)} {src} → "
                              f"~{ft(cel_amt) if dst == 'HUF' else eur(cel_amt)} {dst}",
                    "indok": f"cél EUR-arány {pct(cel * 100, 0)}, most {pct(arany * 100, 1)}; a váltás költsége "
                             f"~{pct(koltseg, 1)} (bankszámlán)",
                    "tobblet": tobb, "ccy": dst, "vege": vege, "amt": cel_amt})
        terv.append(f"{a['datum']:%Y-%m-%d}: {eur(conv / fx if src == 'HUF' else conv)} EUR "
                    + ("forintba" if dst == "HUF" else "forintból euróba"))
        eur_eur += -conv if src == "EUR" else conv / fx
    return out, terv


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frissit", action="store_true", help="az adatokat a mai cache ellenére újratölti")
    ap.add_argument("--holdings", type=Path, default=ROOT / "private/holdings.csv")
    ap.add_argument("--cash", type=Path, default=ROOT / "private/cash.csv")
    ap.add_argument("--out", type=Path, help="riportfájl (alapértelmezés: private/reports/ajanlo_ÉÉÉÉ-QN.md)")
    a = ap.parse_args()
    cfg = yaml.safe_load((ROOT / "config/strategia.yaml").read_text(encoding="utf-8"))
    rep, q = run(cfg, a.holdings, a.cash, a.frissit)
    out = a.out or ROOT / f"private/reports/ajanlo_{q}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rep, encoding="utf-8")
    print(f"Riport: {out}")


if __name__ == "__main__":
    main()
