import hashlib
import os
import pathlib
import re
import sys
from datetime import datetime, timezone

import requests
import yaml
from bs4 import BeautifulSoup
from lxml import etree
from markdownify import markdownify as md

ROOT = pathlib.Path(__file__).resolve().parents[1]
NORMS_YAML = ROOT / "norms.yaml"
OUT_DIR = ROOT / "docs" / "normas"
RAW_DIR = ROOT / "raw" / "xml"
CACHE_DIR = ROOT / ".cache"
OUT_DIR.mkdir(parents=True, exist_ok=True)
RAW_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

LEYCHILE_XML = "https://www.leychile.cl/Consulta/obtxml"
LEYCHILE_XSL = "https://www.leychile.cl/esquemas/TestEjemploIntercambioV3.xsl"  # XSLT ejemplo oficial

HEADERS = {
    "User-Agent": "wiki-leychile-bot/1.0 (+public wiki; contact in repo)",
}

def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

def fetch_bytes(url: str, params=None) -> bytes:
    r = requests.get(url, params=params, headers=HEADERS, timeout=90)
    r.raise_for_status()
    return r.content

def load_xslt() -> etree.XSLT:
    xsl_path = CACHE_DIR / "TestEjemploIntercambioV3.xsl"
    if not xsl_path.exists():
        xsl_bytes = fetch_bytes(LEYCHILE_XSL)
        xsl_path.write_bytes(xsl_bytes)
    xsl_doc = etree.parse(str(xsl_path))
    return etree.XSLT(xsl_doc)

def html_to_markdown(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    # Quita scripts/estilos si vinieran
    for tag in soup(["script", "style"]):
        tag.decompose()

    # Intenta encontrar el contenido principal
    body = soup.body or soup
    text_html = str(body)

    md_text = md(text_html, heading_style="ATX")

    # Limpieza básica
    md_text = re.sub(r"\n{3,}", "\n\n", md_text).strip()
    return md_text

def main():
    cfg = yaml.safe_load(NORMS_YAML.read_text(encoding="utf-8"))
    norms = cfg.get("normas", [])
    if not norms:
        print("No hay normas en norms.yaml")
        return 1

    xslt = load_xslt()

    for n in norms:
        titulo = n["titulo"]
        slug = n["slug"]
        source_url = n.get("source_url", "").strip()
        params = {"opt": "7", "notaPIE": "1"}  # notaPIE documentado como opcional
        if "idNorma" in n:
            params["idNorma"] = str(n["idNorma"])
            key = f"idNorma-{n['idNorma']}"
        else:
            params["idLey"] = str(n["idLey"])
            key = f"idLey-{n['idLey']}"

        xml_bytes = fetch_bytes(LEYCHILE_XML, params=params)

        xml_hash = sha256(xml_bytes)
        raw_path = RAW_DIR / f"{slug}.xml"
        md_path = OUT_DIR / f"{slug}.md"
        hash_path = CACHE_DIR / f"{slug}.sha256"

        old_hash = hash_path.read_text(encoding="utf-8").strip() if hash_path.exists() else ""
        if old_hash == xml_hash and md_path.exists():
            print(f"Sin cambios: {slug}")
            continue

        raw_path.write_bytes(xml_bytes)

        xml_doc = etree.fromstring(xml_bytes)
        html_doc = xslt(xml_doc)
        html_str = str(html_doc)

        md_body = html_to_markdown(html_str)

        fetched = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        header = f"# {titulo}\n\n"
        header += f"Última actualización automática: {fetched}\n\n"
        if source_url:
            header += f"Fuente (LeyChile/BCN): {source_url}\n\n"
        header += f"Identificador: {key}\n\n---\n\n"

        md_path.write_text(header + md_body + "\n", encoding="utf-8")
        hash_path.write_text(xml_hash, encoding="utf-8")

        print(f"Actualizada: {slug}")

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
