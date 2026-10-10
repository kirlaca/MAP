"""A Magyar Államkincstár (WebKincstár) portfólió-exportjának (portfolio.xls) beolvasása.

Az export oszlopai: Ügyfél, Számla, Technikai számla típusa, Instrumentum, Devizanem, Névérték, Összeg, Árfolyam,
Piaci érték, Arány %, Következő esedékességfizetés időpontja, Esedékességfizetés típusa.
- Értékpapír-sor: Instrumentum = papír neve (pl. "Prémium Magyar Állampapír 2028/I"), Névérték, Árfolyam (bruttó, %),
  Piaci érték (Ft, a Kincstár vételi árfolyamán), következő esemény (Kamatfizetés / Lejárat).
- Pénzsor: "Szabad pénzegyenleg", Instrumentum = EURO / MAGYAR FORINT, Összeg devizában, Piaci érték Ft-ban.

Az ügyfél neve és a számlaszám NEM kerül a kimenetbe. A számla-azonosító a technikai számla típusából jön
(Szabad … egyenleg → "kincstar"; TBSZ → "tbsz"), a config szerint átnevezhető.

Kimenet: private/holdings.csv, private/cash.csv (a régiek → private/archiv/), private/snapshots/ÉÉÉÉ-HH-NN_ÓÓPPMM.json
Futtatás: python scripts/mak_portfolio_import.py <portfolio.xls> [--proba]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PRIV = ROOT / "private"

# Instrumentum-név eleje → (típus a lakossági kamattáblában, sorozat-utótag). A hosszabb előtag előbb!
TIPUSOK = [
    ("Prémium Euró Magyar Állampapír", "PEMÁP", "_EUR"),
    ("Euró Magyar Állampapír", "EMÁP", "_EUR"),
    ("Prémium Magyar Állampapír", "PMÁP", ""),
    ("Bónusz Magyar Állampapír", "BMÁP", ""),
    ("Fix Magyar Állampapír", "FixMÁP", ""),
    ("Magyar Állampapír Plusz", "MÁP Plusz", ""),
    ("Egyéves Magyar Állampapír", "1MÁP", ""),
    ("Kétéves Magyar Állampapír", "2MÁP", ""),
    ("Magyar Államkötvény", "MAK", ""),
    ("Diszkont Kincstárjegy", "DKJ", ""),
    ("Babakötvény", "BABA", "_BABA"),
]
PENZ = {"EURO": "EUR", "MAGYAR FORINT": "HUF", "USA DOLLÁR": "USD", "AMERIKAI DOLLÁR": "USD", "SVÁJCI FRANK": "CHF"}
HOLD_COLS = ["account", "tipus", "sorozat", "nev", "ccy", "nevertek", "arfolyam_pct", "piaci_ertek_huf",
             "kov_datum", "kov_tipus"]


def _num(v) -> float | None:
    if v is None or v == "" or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace("\xa0", "").replace(" ", "").replace("%", "")
    if "," in s and "." in s:
        s = s.replace(".", "")
    return float(s.replace(",", "."))


def _date(v) -> str | None:
    s = str(v or "").strip().rstrip(".")
    m = re.match(r"(\d{4})\.(\d{2})\.(\d{2})", s)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def parse_instrument(nev: str) -> tuple[str | None, str | None]:
    for prefix, tipus, sfx in TIPUSOK:
        if nev.startswith(prefix):
            m = re.search(r"([A-Z]?\d{4}/[A-Z0-9]+)\s*$", nev)
            return tipus, (m.group(1) + sfx) if m else None
    return None, None


def account_of(technikai: str, cfg_map: dict | None = None) -> str:
    t = technikai.lower()
    acc = "tbsz" if "tbsz" in t or "tartós" in t else "kincstar"
    return (cfg_map or {}).get(acc, acc)


def read_xls(path: Path, cfg_map: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    raw = pd.read_excel(path, header=0, dtype=object)  # .xls → xlrd
    raw.columns = [str(c).strip() for c in raw.columns]
    need = ["Technikai számla típusa", "Instrumentum", "Devizanem", "Névérték", "Összeg", "Árfolyam", "Piaci érték"]
    miss = [c for c in need if c not in raw.columns]
    if miss:
        raise SystemExit(f"Ismeretlen exportformátum, hiányzó oszlop(ok): {', '.join(miss)}")
    warn, hold, cash = [], [], []
    for _, r in raw.iterrows():
        tech, instr = str(r["Technikai számla típusa"] or ""), str(r["Instrumentum"] or "").strip()
        if not instr or instr == "nan":
            continue
        acc = account_of(tech, cfg_map)
        if "pénz" in tech.lower():
            ccy = PENZ.get(instr.upper())
            if not ccy:
                warn.append(f"Ismeretlen pénznem: {instr}")
                continue
            cash.append({"account": acc, "ccy": ccy, "amount": _num(r["Összeg"]), "huf": _num(r["Piaci érték"])})
            continue
        tipus, sorozat = parse_instrument(instr)
        if not tipus:
            warn.append(f"Ismeretlen értékpapír-típus, kihagyva az ajánlásból: {instr}")
        hold.append({
            "account": acc, "tipus": tipus, "sorozat": sorozat, "nev": instr,
            "ccy": str(r["Devizanem"] or "").strip() or None, "nevertek": _num(r["Névérték"]),
            "arfolyam_pct": _num(r["Árfolyam"]), "piaci_ertek_huf": _num(r["Piaci érték"]),
            "kov_datum": _date(r.get("Következő esedékességfizetés időpontja")),
            "kov_tipus": str(r.get("Esedékességfizetés típusa") or "").strip() or None,
        })
    h = pd.DataFrame(hold, columns=HOLD_COLS)
    c = pd.DataFrame(cash, columns=["account", "ccy", "amount", "huf"])
    if h.empty and c.empty:
        raise SystemExit("Az exportban nincs sem értékpapír, sem pénzegyenleg.")
    return h, c, warn


def snapshot(h: pd.DataFrame, c: pd.DataFrame, src: str) -> dict:
    eur = [(r.piaci_ertek_huf / (r.nevertek * r.arfolyam_pct / 100)) for r in h.itertuples()
           if r.ccy == "EUR" and r.nevertek and r.arfolyam_pct]
    return {"ido": f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}", "forras": src,
            "eurhuf": round(sum(eur) / len(eur), 4) if eur else None,
            "papir_huf": round(float(h["piaci_ertek_huf"].sum()), 0), "penz_huf": round(float(c["huf"].sum()), 0),
            "holdings": json.loads(h.to_json(orient="records", force_ascii=False)),
            "cash": json.loads(c.to_json(orient="records", force_ascii=False))}


def save(h: pd.DataFrame, c: pd.DataFrame, snap: dict) -> Path:
    stamp = f"{dt.datetime.now():%Y%m%d-%H%M%S}"
    (PRIV / "archiv").mkdir(parents=True, exist_ok=True)
    for name in ("holdings.csv", "cash.csv"):
        if (PRIV / name).exists():
            shutil.copy2(PRIV / name, PRIV / "archiv" / f"{Path(name).stem}_{stamp}.csv")
    h.to_csv(PRIV / "holdings.csv", index=False)
    c.to_csv(PRIV / "cash.csv", index=False)
    (PRIV / "snapshots").mkdir(parents=True, exist_ok=True)
    sp = PRIV / "snapshots" / f"{dt.datetime.now():%Y-%m-%d_%H%M%S}.json"
    sp.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    return sp


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("xls", type=Path)
    ap.add_argument("--proba", action="store_true", help="csak kiírja, nem ment")
    a = ap.parse_args()
    h, c, warn = read_xls(a.xls)
    pd.set_option("display.width", 200)
    print(h.drop(columns=["nev"]).to_string(index=False))
    print(c.to_string(index=False))
    for w in warn:
        print("FIGYELEM:", w)
    snap = snapshot(h, c, a.xls.name)
    print(f"Összesen: papír {snap['papir_huf']:,.0f} Ft + pénz {snap['penz_huf']:,.0f} Ft; EUR/HUF (exportból) {snap['eurhuf']}")
    if not a.proba:
        print("Mentve, pillanatkép:", save(h, c, snap).name)


if __name__ == "__main__":
    sys.exit(main())
