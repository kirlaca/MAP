"""Helyi webfelület az állampapír-ajánlóhoz: Kincstár-XLS feltöltés és import, ajánló futtatása, riportok, útmutató.

CSAK HELYI: 127.0.0.1-en figyel, és minden POST-hoz az induláskor generált token kell. Az oldal semmit nem tölt be
az internetről. Minden adat a private/ mappában marad. Port: 8766.

Indítás (a projekt gyökeréből):  python app/webapp.py   → a böngésző magától megnyílik
                                    vagy dupla klikk: start_webapp.bat
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
import secrets
import shutil
import socket
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import config_kezelo as ck  # noqa: E402
import mak_portfolio_import as imp  # noqa: E402
import negyedeves_ajanlo as aj  # noqa: E402

PRIV = ROOT / "private"
STAGING = PRIV / "uploads/_staging"
REPORTS = PRIV / "reports"
PORT = 8766
TOKEN = secrets.token_urlsafe(24)
RUN_LOCK = threading.Lock()
XLS_MAGIC = (b"\xd0\xcf\x11\xe0", b"PK\x03\x04")  # régi .xls (OLE2) és .xlsx (zip)


# ---------- segédfüggvények ----------

def md_to_html(md: str) -> str:
    """A riportok markdown-részhalmaza: címsorok, idézet, lista, táblázat, félkövér, kód."""
    def inline(s: str) -> str:
        s = html.escape(s.replace("\\*", "*"), quote=False)
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        return re.sub(r"`(.+?)`", r"<code>\1</code>", s)

    out, table, lst = [], None, False

    def flush() -> None:
        nonlocal table, lst
        if table is not None:
            head, *body = table
            zold = {i for i, c in enumerate(head) if "többlet" in c}  # a többlet-oszlop zöld felirattal
            out.append('<div class="tw"><table><thead><tr>' + "".join(f"<th>{inline(c)}</th>" for c in head) +
                       "</tr></thead><tbody>" + "".join(
                           "<tr>" + "".join(("<td class='ok'>" if i in zold else "<td>") + f"{inline(c)}</td>"
                                            for i, c in enumerate(r)) + "</tr>"
                           for r in body) + "</tbody></table></div>")
            table = None
        if lst:
            out.append("</ul>")
            lst = False

    for line in md.splitlines():
        s = line.strip()
        if s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(re.fullmatch(r":?-+:?", c) for c in cells):
                continue
            if table is None:
                flush()
                table = []
            table.append(cells)
            continue
        if s.startswith("- "):
            if table is not None or not lst:
                flush()
                out.append("<ul>")
                lst = True
            out.append(f"<li>{inline(s[2:])}</li>")
            continue
        flush()
        if not s:
            continue
        m = re.match(r"(#{1,3}) (.*)", s)
        if m:
            n = len(m.group(1)) + 1  # a riport # címe h2 lesz az oldalon
            out.append(f"<h{n}>{inline(m.group(2))}</h{n}>")
        elif s.startswith("> "):
            out.append(f'<p class="note">{inline(s[2:])}</p>')
        else:
            out.append(f"<p>{inline(s)}</p>")
    flush()
    return "\n".join(out)


def df_rows(df: pd.DataFrame) -> list[dict]:
    return json.loads(df.to_json(orient="records", force_ascii=False))


def read_csv(name: str) -> pd.DataFrame:
    p = PRIV / name
    return pd.read_csv(p) if p.exists() else pd.DataFrame()


def load_cfg() -> dict:
    return yaml.safe_load((ROOT / "config/strategia.yaml").read_text(encoding="utf-8"))


def rules_summary(cfg: dict) -> list[str]:
    inf = cfg["inflacio"]
    return [
        f"Összehasonlítási horizont: {cfg['horizont_ev']} év (vagy a lejáratig).",
        "Vehető: minden éppen kapható papír (új típus is), kivéve: " + ", ".join(cfg.get("kizart_tipusok") or []) + ".",
        f"Csere, ha a várható többlet a visszaváltási költség (nettó {cfg['visszavaltas']['alap']}%) után "
        f"évi ≥ {cfg['csere']['min_elony_pp']} pont. Az eladás napja: ma vagy egy későbbi kamatforduló.",
        "Inflációs feltevés (PMÁP/PEMÁP): HUF " + ", ".join(f"{k}: {'KSH YTD' if v is None else v}" for k, v in inf["HUF"].items())
        + "; EUR " + ", ".join(f"{k}: {v}" for k, v in inf["EUR"].items()) + ".",
        f"Cél EUR-arány: {(cfg.get('deviza') or {}).get('cel_eur_arany')}% ± {(cfg.get('deviza') or {}).get('sav_pp')} pont "
        "(a szabad pénzzel és a lejáratokkal igazítva, eladás nélkül).",
        "Tartalék: " + ", ".join(f"{v:,} {k}".replace(",", " ") for k, v in cfg["tartalek"].items()) + ".",
    ]


def reports_list() -> list[dict]:
    files = sorted(REPORTS.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True) if REPORTS.exists() else []
    return [{"id": str(p.relative_to(PRIV)).replace("\\", "/"), "nev": p.stem,
             "ido": f"{dt.datetime.fromtimestamp(p.stat().st_mtime):%Y-%m-%d %H:%M}"} for p in files]


def safe_private(rel: str) -> Path:
    p = (PRIV / rel).resolve()
    if PRIV.resolve() not in p.parents or p.suffix != ".md":
        raise ValueError("érvénytelen riport")
    return p


def staged() -> list[str]:
    return sorted(p.name for p in STAGING.iterdir() if p.suffix.lower() in (".xls", ".xlsx")) if STAGING.exists() else []


def state() -> dict:
    h, c = read_csv("holdings.csv"), read_csv("cash.csv")
    hm = (PRIV / "holdings.csv").stat().st_mtime if (PRIV / "holdings.csv").exists() else None
    return {
        "holdings": df_rows(h.drop(columns=["nev"], errors="ignore")), "cash": df_rows(c),
        "holdings_ido": f"{dt.datetime.fromtimestamp(hm):%Y-%m-%d %H:%M}" if hm else None,
        "staged": staged(), "reports": reports_list(), "rules": rules_summary(load_cfg()),
    }


def preview() -> dict:
    xs = [STAGING / n for n in staged()]
    if len(xs) != 1:
        raise ValueError("Pontosan egy portfolio.xls-t tölts fel." if xs else "Nincs feltöltött XLS.")
    h, c, warn = imp.read_xls(xs[0], load_cfg().get("szamla_atnevezes"))
    return {"holdings": df_rows(h.drop(columns=["nev"])), "cash": df_rows(c), "warnings": warn}


def apply_import() -> dict:
    xs = [STAGING / n for n in staged()]
    if len(xs) != 1:
        raise ValueError("Pontosan egy portfolio.xls-t tölts fel.")
    h, c, warn = imp.read_xls(xs[0], load_cfg().get("szamla_atnevezes"))
    sp = imp.save(h, c, imp.snapshot(h, c, xs[0].name))
    dest = PRIV / f"exports/{dt.datetime.now():%Y-%m-%d_%H%M%S}"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.move(str(xs[0]), dest / xs[0].name)
    return {"ok": True, "warnings": warn,
            "uzenet": f"Import kész, pillanatkép: {sp.stem}. A régi fájlok a private/archiv/ mappába kerültek, "
                      f"az XLS helye: {dest.relative_to(ROOT)}."}


def run_recommender() -> dict:
    if not RUN_LOCK.acquire(blocking=False):
        raise ValueError("Az ajánló már fut.")
    try:
        rep, q = aj.run(load_cfg(), PRIV / "holdings.csv", PRIV / "cash.csv")
        out = REPORTS / f"ajanlo_{q}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rep, encoding="utf-8")
        return {"id": str(out.relative_to(PRIV)).replace("\\", "/"), "html": md_to_html(rep)}
    finally:
        RUN_LOCK.release()


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "AllampapirAjanlo/1"

    def log_message(self, fmt: str, *args) -> None:  # csendes konzol
        pass

    def _host_ok(self) -> bool:  # DNS-rebinding ellen
        return self.headers.get("Host", "") in {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: dict, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self) -> None:
        if not self._host_ok():
            return self._send(403, b"forbidden", "text/plain")
        u = urlparse(self.path)
        try:
            if u.path == "/":
                page = (ROOT / "app/index.html").read_text(encoding="utf-8").replace("__TOKEN__", TOKEN)
                return self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            if u.path == "/api/config":
                return self._json(ck.form_state())
            if u.path == "/api/state":
                return self._json(state())
            if u.path == "/api/report":
                p = safe_private(parse_qs(u.query).get("id", [""])[0])
                return self._json({"id": str(p.relative_to(PRIV)).replace("\\", "/"),
                                   "html": md_to_html(p.read_text(encoding="utf-8"))})
            self._send(404, b"not found", "text/plain")
        except Exception as e:  # noqa: BLE001
            self._json({"hiba": str(e)}, 400)

    def do_POST(self) -> None:
        if not self._host_ok() or self.headers.get("X-Token") != TOKEN:
            return self._send(403, b"forbidden", "text/plain")
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length", 0))
        if n > 20_000_000:
            return self._json({"hiba": "túl nagy fájl"}, 413)
        body = self.rfile.read(n)
        try:
            if u.path == "/api/upload":
                name = Path(unquote(self.headers.get("X-Filename", "portfolio.xls"))).name
                name = re.sub(r"[^\w.\- ()]", "_", name)
                if not name.lower().endswith((".xls", ".xlsx")) or not body.startswith(XLS_MAGIC):
                    raise ValueError("Csak a WebKincstár portfolio.xls exportja tölthető fel.")
                if STAGING.exists():  # egyszerre egy export
                    shutil.rmtree(STAGING)
                STAGING.mkdir(parents=True, exist_ok=True)
                (STAGING / name).write_bytes(body)
                return self._json({"staged": staged()})
            if u.path == "/api/upload/clear":
                if STAGING.exists():
                    shutil.rmtree(STAGING)
                return self._json({"staged": []})
            if u.path == "/api/import/preview":
                return self._json(preview())
            if u.path == "/api/import/apply":
                return self._json(apply_import())
            if u.path == "/api/config":
                return self._json(ck.save_form(json.loads(body or b"{}").get("ertekek") or {}))
            if u.path == "/api/config/raw":
                return self._json(ck.save_raw(json.loads(body or b"{}").get("text") or ""))
            if u.path == "/api/run":
                return self._json(run_recommender())
            self._send(404, b"not found", "text/plain")
        except SystemExit as e:  # az importer SystemExit-tel jelzi a hibás bemenetet
            self._json({"hiba": str(e)}, 400)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._json({"hiba": f"{type(e).__name__}: {e}"}, 400)


class Server(ThreadingHTTPServer):
    """Kizárólagos portfoglalás: Windows alatt a SO_REUSEADDR egy második példányt is ráengedne a portra."""
    allow_reuse_address = False

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def main() -> None:
    url = f"http://127.0.0.1:{PORT}/"
    try:
        srv = Server(("127.0.0.1", PORT), Handler)
    except OSError:
        print(f"A {PORT}-es port foglalt: valószínűleg már fut egy példány. Nyisd meg: {url}")
        if "--no-browser" not in sys.argv:
            webbrowser.open(url)
        return
    print(f"Állampapír-ajánló: {url}  (leállítás: Ctrl+C)")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
