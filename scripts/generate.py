import hashlib
import pathlib
import re
from datetime import datetime, timezone

import requests
import yaml
from lxml import etree

ROOT = pathlib.Path(__file__).resolve().parents[1]
NORMS_YAML = ROOT / "norms.yaml"

DOCS_DIR = ROOT / "docs"
OUT_DIR = DOCS_DIR / "normas"
RAW_DIR = ROOT / "raw" / "xml"
CACHE_DIR = ROOT / ".cache"

OUT_DIR.mkdir(parents=True, exist_ok=True)
RAW_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Servicio oficial descrito por LeyChile: opt=7 entrega XML de la norma
LEYCHILE_XML = "https://www.leychile.cl/Consulta/obtxml"

HEADERS = {
    "User-Agent": "wiki-leychile-bot/1.0 (public wiki; contact in repo)",
}

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

def get_text_excluding_metadatos(el) -> str:
    # Toma texto del elemento excluyendo todo lo que esté dentro de Metadatos
    parts = el.xpath('.//text()[not(ancestor::*[local-name()="Metadatos"])]')
    parts = [p.strip() for p in parts if p and p.strip()]
    # separa en líneas para no quedar como “un párrafo infinito”
    txt = "\n".join(parts)
    txt = re.sub(r"\n{2,}", "\n", txt)
    return txt.strip()

def get_articulo_title(art_el, fallback: str) -> str:
    # Intenta leer el "NombreParte" dentro de Metadatos (suele contener “Artículo 1°”, “Artículo primero”, etc.)
    nombre = art_el.xpath('.//*[local-name()="Metadatos"]//*[local-name()="NombreParte"]//text()')
    nombre = " ".join([n.strip() for n in nombre if n and n.strip()]).strip()
    if nombre:
        return nombre
    # Alternativa: TituloParte
    titulo = art_el.xpath('.//*[local-name()="Metadatos"]//*[local-name()="TituloParte"]//text()')
    titulo = " ".join([t.strip() for t in titulo if t and t.strip()]).strip()
    if titulo:
        return titulo
    return fallback

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

        # Guarda XML raw
        raw_path.write_bytes(xml_bytes)

        # Parse XML
        try:
            root = etree.fromstring(xml_bytes)
        except Exception as e:
            print("No pude parsear el XML. Primeros 300 bytes:")
            print(xml_bytes[:300])
            raise

        # Extrae artículos en orden
        articulos = root.xpath('//*[local-name()="Articulo"]')

        lines = []
        fetched = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

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
            # Fallback: si no encuentra Articulo, vuelca texto plano
            txt = etree.tostring(root, method="text", encoding="unicode")
            txt = clean_spaces(txt)
            lines.append(txt)
        else:
            for i, art in enumerate(articulos, start=1):
                art_title = get_articulo_title(art, f"Artículo {i}")
                art_text = get_text_excluding_metadatos(art)
                if not art_text:
                    continue

                lines.append(f"## {art_title}")
                lines.append("")
                lines.append(art_text)
                lines.append("")

        md_path.write_text(clean_spaces("\n".join(lines)) + "\n", encoding="utf-8")
        hash_path.write_text(xml_hash, encoding="utf-8")
        print(f"Actualizada: {slug} -> {md_path}")

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
