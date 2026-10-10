"""A config/strategia.yaml olvasása, ellenőrzése és írása (a webfelület Beállítások füléhez).

- MEZOK: az űrlapon szerkeszthető értékek (útvonal, címke, típus, határok, súgó).
- validate(cfg): hibalista (üres = rendben).
- render(cfg): a teljes YAML a magyarázó megjegyzésekkel (a PyYAML a megjegyzéseket nem őrzi meg, ezért sablonból írjuk).
- save_form(ertekek) / save_raw(szoveg): ellenőrzés → a régi fájl a config/archiv/ mappába → írás.
"""
from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "config/strategia.yaml"
ARCHIV = ROOT / "config/archiv"

# típus: num | num_null (üres = null) | list (vesszővel elválasztott szövegek) | evmap (év → szám vagy üres)
MEZOK = [
    {"path": "horizont_ev", "csoport": "Alap", "label": "Összehasonlítási horizont (év)", "type": "num", "min": 0.5, "max": 15,
     "help": "Ennyi évre (vagy a lejáratig) hasonlítja a várható átlagkamatot."},
    {"path": "csere.min_elony_pp", "csoport": "Alap", "label": "Csereküszöb (pont/év)", "type": "num", "min": 0, "max": 10,
     "help": "Csere csak akkor, ha a várható többlet a visszaváltási költség után évente legalább ennyi százalékpont."},
    {"path": "kizart_tipusok", "csoport": "Alap", "label": "Kizárt terméktípusok", "type": "list",
     "help": "Vesszővel elválasztva, a lakossági kamattábla típusnevei (pl. BABA, KTJ I). Minden más kapható papírt vizsgál."},
    {"path": "deviza.cel_eur_arany", "csoport": "Devizaarány", "label": "Cél EUR-arány (%)", "type": "num", "min": 0, "max": 100,
     "help": "Az EUR-papírok és az EUR-pénz aránya a teljes portfólióban."},
    {"path": "deviza.sav_pp", "csoport": "Devizaarány", "label": "Sáv ± (pont)", "type": "num", "min": 0, "max": 50,
     "help": "Ezen belül nincs váltás. 0 = minden pénzmozgás a cél felé visz."},
    {"path": "deviza.valtas_koltseg_pct", "csoport": "Devizaarány", "label": "Váltási költség (%)", "type": "num", "min": 0, "max": 5,
     "help": "A bank árfolyamrése (a Kincstár nem vált)."},
    {"path": "tartalek.HUF", "csoport": "Pénz", "label": "Tartalék (Ft)", "type": "num", "min": 0,
     "help": "Ennyi forint marad befektetetlenül a kincstári számlán."},
    {"path": "tartalek.EUR", "csoport": "Pénz", "label": "Tartalék (EUR)", "type": "num", "min": 0, "help": ""},
    {"path": "min_vasarlas.HUF", "csoport": "Pénz", "label": "Legkisebb vétel (Ft)", "type": "num", "min": 0, "help": ""},
    {"path": "min_vasarlas.EUR", "csoport": "Pénz", "label": "Legkisebb vétel (EUR)", "type": "num", "min": 0, "help": ""},
    {"path": "inflacio.HUF", "csoport": "Feltevések", "label": "HUF-infláció (KSH, éves átlag, %)", "type": "evmap",
     "help": "Év → %. Üres = a KSH év eleje óta mért átlaga (csak a folyó évre). Az 'alap' minden további évre."},
    {"path": "inflacio.EUR", "csoport": "Feltevések", "label": "EUR-infláció (HICP, éves átlag, %)", "type": "evmap",
     "help": "Év → %. A lezárt éveket a program letölti; a folyó és a jövő évekre kell feltevés."},
    {"path": "dkj_3h", "csoport": "Feltevések", "label": "3 havi DKJ-hozam a BMÁP-hoz (%)", "type": "num_null", "min": 0, "max": 30,
     "help": "Üres = a mostani BMÁP-kamat marad."},
    {"path": "euribor_3h", "csoport": "Feltevések", "label": "3 havi EURIBOR az EMÁP-hoz (%)", "type": "num_null", "min": -2, "max": 15,
     "help": "Üres = az ECB legutóbbi havi átlaga."},
    {"path": "visszavaltas.alap", "csoport": "Visszaváltás", "label": "Kincstári vételi nettó árfolyam (%)", "type": "num",
     "min": 90, "max": 100, "help": "Lejárat előtti eladáskor (+ felhalmozott kamat). 2026-10: 99%."},
    {"path": "visszavaltas.MAP_Plusz_ablakban", "csoport": "Visszaváltás", "label": "MÁP Plusz a kamatjuttatás utáni 5 munkanapban (%)",
     "type": "num", "min": 90, "max": 100, "help": ""},
]


def load() -> dict:
    return yaml.safe_load(CFG.read_text(encoding="utf-8"))


def get(cfg: dict, path: str):
    cur = cfg
    for k in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def put(cfg: dict, path: str, value) -> None:
    keys = path.split(".")
    cur = cfg
    for k in keys[:-1]:
        cur = cur.setdefault(k, {})
    cur[keys[-1]] = value


def form_state() -> dict:
    cfg = load()
    return {"mezok": [dict(m, value=get(cfg, m["path"])) for m in MEZOK], "raw": CFG.read_text(encoding="utf-8")}


def _num(v, m: dict, hibak: list[str], nullable: bool = False):
    if v is None or (isinstance(v, str) and v.strip() == ""):
        if nullable:
            return None
        hibak.append(f"{m['label']}: kötelező")
        return None
    try:
        x = float(str(v).replace(",", "."))
    except ValueError:
        hibak.append(f"{m['label']}: nem szám ({v})")
        return None
    if "min" in m and x < m["min"] or "max" in m and x > m["max"]:
        hibak.append(f"{m['label']}: {m.get('min', '-∞')} és {m.get('max', '∞')} között kell lennie")
    return int(x) if x == int(x) and m["type"] == "num" and abs(x) >= 1 else x


def coerce(m: dict, v, hibak: list[str]):
    """Űrlapérték → config-érték, a típus szerint."""
    t = m["type"]
    if t == "num":
        return _num(v, m, hibak)
    if t == "num_null":
        return _num(v, m, hibak, nullable=True)
    if t == "list":
        items = v if isinstance(v, list) else str(v or "").split(",")
        return [s.strip() for s in items if str(s).strip()]
    if t == "evmap":
        out = {}
        for k, x in (v or {}).items():
            k = str(k).strip()
            if not k:
                continue
            if k != "alap" and not (k.isdigit() and len(k) == 4):
                hibak.append(f"{m['label']}: érvénytelen év: {k}")
                continue
            out[int(k) if k.isdigit() else k] = _num(x, dict(m, min=-5, max=50), hibak, nullable=k != "alap")
        if "alap" not in out:
            hibak.append(f"{m['label']}: az 'alap' érték kötelező")
        return out
    raise ValueError(t)


def validate(cfg: dict) -> list[str]:
    if not isinstance(cfg, dict):
        return ["A config nem kulcs–érték szerkezet."]
    hibak: list[str] = []
    for m in MEZOK:
        v = get(cfg, m["path"])
        if m["type"] == "evmap":
            coerce(m, {str(k): v2 for k, v2 in (v or {}).items()}, hibak)
        elif m["type"] == "list":
            if not isinstance(v, list):
                hibak.append(f"{m['label']}: lista kell")
        else:
            coerce(m, v, hibak)
    for k in ("premium_kezi", "szamla_atnevezes"):
        if not isinstance(cfg.get(k) or {}, dict):
            hibak.append(f"{k}: kulcs–érték szerkezet kell (pl. {{}})")
    return hibak


def _f(v) -> str:
    """Egysoros YAML-érték (flow stílus)."""
    s = yaml.safe_dump(v, default_flow_style=True, allow_unicode=True, sort_keys=False).strip()
    return s[:-4].strip() if s.endswith("\n...") else s.replace("\n...", "")


def render(cfg: dict) -> str:
    g = lambda p: _f(get(cfg, p))  # noqa: E731
    return f"""# Negyedéves állampapír-ajánló – szabályok és feltevések
# A hozamok mind évi %-ban, a kamatadó-mentes (2019. 06. 01. utáni) lakossági papírokra.
# A webfelület Beállítások fülén is szerkeszthető (mentéskor a régi változat a config/archiv/ mappába kerül).

horizont_ev: {g('horizont_ev')}            # ennyi évre (vagy a lejáratig, ha az korábbi) hasonlítja a várható átlagkamatot

# Mit NEM vehet az ajánló? Minden más, éppen kapható papírt vizsgál, egy új terméktípust is (azt a mostani
# kamatával, amíg a kamatképletét be nem építjük). Típusnevek a lakossági kamattáblából.
kizart_tipusok: {g('kizart_tipusok')}

# Devizaarány: az EUR-papírok és az EUR-pénz aránya a teljes portfólióban (Ft-ban mérve).
# A sávon kívül a szabad pénzt és a lejáró összegeket a program a másik devizába irányítja. Papírt emiatt nem ad el.
deviza:
  cel_eur_arany: {g('deviza.cel_eur_arany')}       # %
  sav_pp: {g('deviza.sav_pp')}               # ± pont: ezen belül nincs teendő
  valtas_koltseg_pct: {g('deviza.valtas_koltseg_pct')} # feltevés: banki árfolyamrés (a Kincstár nem vált; bankszámlán kell váltani)

# Kincstári visszaváltás lejárat előtt: nettó árfolyam %-ban (+ felhalmozott kamat).
# Forrás: kincstári árfolyamjegyzés (allampapir.hu), 2026-10: minden sorozat 99,00%.
# MÁP Plusz: a kamatjuttatás utáni 5 munkanapban 100%.
visszavaltas:
  alap: {g('visszavaltas.alap')}
  MAP_Plusz_ablakban: {g('visszavaltas.MAP_Plusz_ablakban')}

# Infláció-feltevések a PMÁP (KSH-fogyasztóiár-index) és a PEMÁP (euróövezeti HICP) kamatának vetítéséhez.
# A lezárt évek tényadatait a program letölti (KSH, Eurostat). Folyó év: null = a KSH év eleje óta mért átlaga.
# A jövő évekre itt kell feltevést adni; frissítsd negyedévente az MNB inflációs jelentése alapján.
inflacio:
  HUF: {g('inflacio.HUF')}
  EUR: {g('inflacio.EUR')}

# Változó kamatok vetítése. null = a mostani szint marad (nincs kamatpálya-feltevés).
dkj_3h: {g('dkj_3h')}              # BMÁP kamatbázisa (3 hónapos DKJ aukciós átlaghozam), %
euribor_3h: {g('euribor_3h')}          # EMÁP kamata; null = az ECB legutóbbi havi átlaga

# Kamatprémium kézi megadása, ha a program nem tudja kiszámolni ("típus sorozat": %)
premium_kezi: {_f(cfg.get('premium_kezi') or {})}

csere:
  min_elony_pp: {g('csere.min_elony_pp')}       # csere csak akkor, ha a horizonton a várható többlethozam a visszaváltási
                          # költség után évesítve legalább ennyi százalékpont

tartalek:                 # ennyi készpénz marad befektetetlenül a kincstári számlán
  HUF: {g('tartalek.HUF')}
  EUR: {g('tartalek.EUR')}

min_vasarlas: {g('min_vasarlas')}

# A számla-azonosítók (holdings.csv / cash.csv "account") átnevezése importkor, pl. {{tbsz: tbsz2026}}
szamla_atnevezes: {_f(cfg.get('szamla_atnevezes') or {})}
"""


def _write(text: str) -> str:
    ARCHIV.mkdir(parents=True, exist_ok=True)
    arch = ARCHIV / f"strategia_{dt.datetime.now():%Y%m%d-%H%M%S}.yaml"
    shutil.copy2(CFG, arch)
    CFG.write_text(text, encoding="utf-8")
    return arch.name


def save_form(ertekek: dict) -> dict:
    cfg = load()
    hibak: list[str] = []
    for m in MEZOK:
        if m["path"] in ertekek:
            put(cfg, m["path"], coerce(m, ertekek[m["path"]], hibak))
    hibak += validate(cfg) if not hibak else []
    if hibak:
        return {"ok": False, "hibak": hibak}
    text = render(cfg)
    if yaml.safe_load(text) != cfg:  # a sablon minden értéket hűen visszaad?
        return {"ok": False, "hibak": ["Belső hiba: a mentett YAML eltér a beállításoktól, nem mentettem."]}
    return {"ok": True, "archiv": _write(text)}


def save_raw(text: str) -> dict:
    try:
        cfg = yaml.safe_load(text)
    except yaml.YAMLError as e:
        return {"ok": False, "hibak": [f"YAML-hiba: {e}"]}
    hibak = validate(cfg)
    if hibak:
        return {"ok": False, "hibak": hibak}
    return {"ok": True, "archiv": _write(text)}
