import hashlib
import pathlib
import re
from datetime import datetime, timezone
import xml.etree.ElementTree as ET

import requests
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
NORMS_YAML = ROOT / "norms.yaml"

DOCS_DIR = ROOT / "docs"
OUT_DIR = DOCS_DIR / "normas"
RAW_DIR = ROOT / "raw" / "xml"
CACHE_DIR = ROOT / ".cache"

OUT_DIR.mkdir(parents=True, exist_ok=True)
RAW_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

LEYCHILE_XML = "https://www.leychile.cl/Consulta/obtxml"

HEADERS = {"User-Agent": "wiki-leychile-bot/1.0 (public wiki; contact in repo)"}

def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

def fetch_xml(params: dict) -> bytes:
    r = requests.get(LEYCHILE_XML, params=params, headers=HEADERS, timeout=120)
    r.raise_for_status()
    return r.content

def clean_spaces(s: str) -> str:
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

def localname(tag: str) -> str:
    # Maneja namespaces: {namespace}Tag
    return tag.split("}", 1)[-1] if "}" in tag else tag

def element_text(el: ET.Element) -> str:
    # Texto plano de un elemento (incluyendo descendientes)
    parts = []
    for t in el.itertext():
        t = (t or "").strip()
        if t:
            parts.append(t)
    return "\n".join(parts).strip()

def find_first_text(el: ET.Element, wanted_localname: str) -> str:
    for child in el.iter():
        if localname(child.tag) == wanted_localname:
            txt = element_text(child).strip()
            if txt:
                return txt
    return ""

def main():
    cfg = yaml.safe_load(NORMS_YAML.read_text(encoding="utf-8"))
    norms = cfg.get("normas", [])
    if not norms:
        print("No hay normas en norms.yaml")
        return 1

    for n in norms:
        titulo_norma = n.get("titulo", "Norma")
        slug = n["slug"]
        source_url = n.get("source_url", "").strip()

        params = {"opt": "7", "notaPIE": "1"}
        if "idNorma" in n:
            params["idNorma"] = str(n["idNorma"])
            key = f"idNorma={n['idNorma']}"
        else:
            params["idLey"] = str(n["idLey"])
            key = f"idLey={n['idLey']}"

        xml_bytes = fetch_xml(params)
        xml_hash = sha256(xml_bytes)

        raw_path = RAW_DIR / f"{slug}.xml"
        md_path = OUT_DIR / f"{slug}.md"
        hash_path = CACHE_DIR / f"{slug}.sha256"

        old_hash = hash_path.read_text(encoding="utf-8").strip() if hash_path.exists() else ""
        if old_hash == xml_hash and md_path.exists():
            print(f"Sin cambios: {slug}")
            continue

        raw_path.write_bytes(xml_bytes)

        try:
            root = ET.fromstring(xml_bytes)
        except Exception:
            print("No pude parsear el XML. Primeros 300 bytes:")
            print(xml_bytes[:300])
            return 1

        # Recolecta artículos
        articulos = []
        for el in root.iter():
            if localname(el.tag) == "Articulo":
                articulos.append(el)

        fetched = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        lines = []
        lines.append(f"# {titulo_norma}")
        lines.append("")
        lines.append(f"Última actualización automática: {fetched}")
        if source_url:
            lines.append(f"Fuente (LeyChile/BCN): {source_url}")
        lines.append(f"Identificador: {key}")
        lines.append("")
        lines.append("---")
        lines.append("")

        if not articulos:
            # fallback: todo el texto plano
            text_all = element_text(root)
            lines.append(clean_spaces(text_all))
        else:
            for idx, art in enumerate(articulos, start=1):
                # título: intenta NombreParte, luego TituloParte, si no, Artículo N
                nombre = find_first_text(art, "NombreParte")
                titulo = find_first_text(art, "TituloParte")
                heading = nombre or titulo or f"Artículo {idx}"

                # contenido: texto del Articulo completo (sin metadatos finos; esto es simple)
                txt = element_text(art)
                txt = clean_spaces(txt)

                if not txt:
                    continue

                lines.append(f"## {heading}")
                lines.append("")
                lines.append(txt)
                lines.append("")

        md_path.write_text(clean_spaces("\n".join(lines)) + "\n", encoding="utf-8")
        hash_path.write_text(xml_hash, encoding="utf-8")

        print(f"Actualizada: {slug}")

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
