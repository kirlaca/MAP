"""Statikus GitHub Pages oldal: a napi kínálat és a minta-portfólióra futtatott ajánló riportja.

A minta-portfólió (minta/portfolio_minta.xls) KITALÁLT adat. A private/ mappához a szkript nem nyúl:
a mintát egy ideiglenes mappába importálja, a riportot onnan készíti.

Kimenet: site/index.html (+ site/minta_riport.md, site/.nojekyll)
Futtatás (a projekt gyökeréből):  python scripts/pages_build.py [--frissit]
A GitHub Actions (.github/workflows/pages.yml) naponta futtatja, és kiteszi a Pages-re.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "app"))
import config_kezelo as ck  # noqa: E402
import mak_portfolio_import as imp  # noqa: E402
import negyedeves_ajanlo as aj  # noqa: E402
import verzio  # noqa: E402
from webapp import md_to_html  # noqa: E402  (a szerver importkor nem indul el)

MINTA = ROOT / "minta/portfolio_minta.xls"
SITE = ROOT / "site"
REPO = "https://github.com/kirlaca/MAP"


def section(md: str, cim: str) -> str:
    """A riport egy '## cim' része (a következő '## '-ig)."""
    m = re.search(rf"^## {re.escape(cim)}\n.*?(?=^## |\Z)", md, flags=re.S | re.M)
    return m.group(0) if m else ""


def build(frissit: bool) -> Path:
    cfg = ck.load()
    with tempfile.TemporaryDirectory() as tmp:
        h, c, warn = imp.read_xls(MINTA)
        hp, cp = Path(tmp) / "holdings.csv", Path(tmp) / "cash.csv"
        h.to_csv(hp, index=False)
        c.to_csv(cp, index=False)
        rep, q = aj.run(cfg, hp, cp, frissit=frissit)
    # a minta-riport nem bizalmas; a privát útvonal-utalás sem kell
    rep = re.sub(r"^> BIZALMAS:.*$", "> MINTA: kitalált portfólió (minta/portfolio_minta.xls). Ez modellszámítás, nem "
                 "befektetési tanácsadás.", rep, count=1, flags=re.M)
    rep = rep.replace("Portfólió: private/holdings.csv.", "Portfólió: minta/portfolio_minta.xls (kitalált).")

    kinalat = section(rep, "Most kapható papírok")
    feltev = section(rep, "Feltevések és adatok")
    uj = section(rep, "Újdonságok a kínálatban")
    deviza = re.search(r"^- Fedezeti pont:.*$", rep, flags=re.M)
    ido = dt.datetime.now(dt.timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")

    style = re.search(r"<style>.*?</style>", (ROOT / "app/index.html").read_text(encoding="utf-8"), flags=re.S).group(0)
    oldal = f"""<!doctype html>
<html lang="hu">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Állampapír-kínálat</title>
<meta name="description" content="A lakossági állampapírok napi kínálata, kamatvetítése és egy minta-portfólió ajánlója.">
{style}
</head>
<body>
<header><div class="bar">
  <div class="brand">Állampapír-ajánló <small>v{html.escape(verzio.verzio())} · napi kínálat és minta-riport · {html.escape(ido)}</small></div>
  <nav><a class="btn" href="{REPO}">GitHub</a></nav>
</div></header>
<main>
  <div class="minta" role="note"><b>⚠️ {html.escape(imp.MINTA_CIM)}</b>
    <div>A lenti riport egy <strong>kitalált</strong> portfólióra készült, csak bemutató. A kínálati táblázat viszont a valódi, mai kínálat.</div>
    <div style="margin-top:6px"><strong>A saját portfóliód elemzése:</strong> töltsd le a programot a <a href="{REPO}">GitHubról</a>
    (<code>pip install -r requirements.txt</code>, majd <code>python app/webapp.py</code>), és:</div>
    <ol>{"".join(f"<li>{html.escape(x)}</li>" for x in imp.MINTA_LEPESEK)}</ol></div>
  <div class="card">
    <h2>Mi ez?</h2>
    <p>Naponta frissülő összesítés a <strong>Magyar Államkincstárnál</strong> kapható lakossági állampapírokról: a most kapható sorozatok
    várható kamata a következő {html.escape(str(cfg['horizont_ev']))} évre, inflációs és kamatfeltevésekkel vetítve. Alatta a program teljes
    riportja egy <strong>kitalált minta-portfólióra</strong>: tartás vagy csere, újrabefektetés, devizaarány, pénzáramlás.</p>
    <p class="muted small">A saját portfóliódhoz töltsd le a programot a <a href="{REPO}">GitHubról</a>: helyben fut, a WebKincstár-exportodat
    sehová nem tölti fel. Ez modellszámítás, nem befektetési tanácsadás. Kötés előtt nézd meg a WebKincstárban az aktuális kínálatot.</p>
  </div>
  <div class="card report">{md_to_html(kinalat)}
    {f'<p>{html.escape(deviza.group(0)[2:])}</p>' if deviza else ''}</div>
  {f'<div class="card report">{md_to_html(uj)}</div>' if uj else ''}
  <div class="card report">{md_to_html(feltev)}</div>
  <div class="card report">
    <details><summary><strong>A teljes minta-riport</strong> <span class="muted small">(kitalált portfólió, {html.escape(q)})</span></summary>
    {md_to_html(rep)}
    <p class="muted small">Markdownban: <a href="minta_riport.md">minta_riport.md</a></p></details>
  </div>
  <p class="muted small">Adatforrások: allampapir.hu (ÁKK), KSH, Eurostat, ECB. Frissítve: {html.escape(ido)}.
  Forráskód: <a href="{REPO}">{REPO}</a> (MIT licenc) · {html.escape(verzio.cimke())} ·
  <a href="{REPO}/blob/main/CHANGELOG.md">változások</a>.</p>
</main>
</body>
</html>
"""
    SITE.mkdir(exist_ok=True)
    (SITE / "index.html").write_text(oldal, encoding="utf-8")
    (SITE / "minta_riport.md").write_text(rep, encoding="utf-8")
    (SITE / ".nojekyll").write_text("", encoding="utf-8")
    return SITE / "index.html"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", action="version", version=verzio.cimke())
    ap.add_argument("--frissit", action="store_true", help="az adatokat a mai cache ellenére újratölti")
    print("Oldal:", build(ap.parse_args().frissit))


if __name__ == "__main__":
    main()
