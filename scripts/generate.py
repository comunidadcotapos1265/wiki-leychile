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
OUT_NORMAS_DIR = DOCS_DIR / "normas"
RAW_DIR = ROOT / "raw" / "xml"
CACHE_DIR = ROOT / ".cache"

OUT_NORMAS_DIR.mkdir(parents=True, exist_ok=True)
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


def clean_text(s: str) -> str:
    s = s.replace("\r", "\n")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n[ \t]+\n", "\n\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def extract_article_title(art_el) -> str:
    # Preferimos NombreParte (suele traer “Artículo 1°”, “Artículo primero”, etc.)
    nombre = art_el.xpath('.//*[local-name()="Metadatos"]//*[local-name()="NombreParte"]//text()')
    nombre = " ".join([x.strip() for x in nombre if x and x.strip()]).strip()
    if nombre:
        return nombre

    titulo = art_el.xpath('.//*[local-name()="Metadatos"]//*[local-name()="TituloParte"]//text()')
    titulo = " ".join([x.strip() for x in titulo if x and x.strip()]).strip()
    if titulo:
        return titulo

    return ""


def extract_article_body(art_el) -> str:
    # Copiamos el artículo y removemos Metadatos para que el texto quede limpio
    art_copy = etree.fromstring(etree.tostring(art_el))
    for m in art_copy.xpath('.//*[local-name()="Metadatos"]'):
        parent = m.getparent()
        if parent is not None:
            parent.remove(m)

    txt = etree.tostring(art_copy, method="text", encoding="unicode")
    return clean_text(txt)


def main():
    cfg = yaml.safe_load(NORMS_YAML.read_text(encoding="utf-8"))
    norms = cfg.get("normas", [])
    if not norms:
        print("No hay normas en norms.yaml")
        return 1

    for n in norms:
        titulo_norma = n.get("titulo", "Norma")
        slug = n["slug"]
        source_url = (n.get("source_url") or "").strip()

        params = {"opt": "7", "notaPIE": "1"}
        if "idNorma" in n:
            params["idNorma"] = str(n["idNorma"])
            key = f"idNorma={n['idNorma']}"
        else:
            params["idLey"] = str(n["idLey"])
            key = f"idLey={n['idLey']}"

        xml_bytes = fetch_xml(params)
        xml_hash = sha256(xml_bytes)

        hash_path = CACHE_DIR / f"{slug}.sha256"
        old_hash = hash_path.read_text(encoding="utf-8").strip() if hash_path.exists() else ""

        # Guardamos XML raw (útil para auditoría)
        (RAW_DIR / f"{slug}.xml").write_bytes(xml_bytes)

        root = etree.fromstring(xml_bytes)

        articulos = root.xpath('//*[local-name()="Articulo"]')

        fetched = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        # 1) Creamos carpeta por ley para los artículos
        law_dir = OUT_NORMAS_DIR / slug
        law_dir.mkdir(parents=True, exist_ok=True)

        # 2) Generamos cada artículo como página propia
        index_entries = []
        for i, art in enumerate(articulos, start=1):
            art_title = extract_article_title(art) or f"Artículo {i}"
            art_body = extract_article_body(art)
            if not art_body:
                continue

            art_filename = f"articulo-{i:03d}.md"
            art_path = law_dir / art_filename

            art_page = []
            art_page.append(f"# {titulo_norma} — {art_title}")
            art_page.append("")
            art_page.append(f"Última actualización automática: {fetched}")
            if source_url:
                art_page.append(f"Fuente (LeyChile/BCN): {source_url}")
            art_page.append(f"Identificador: {key}")
            art_page.append("")
            art_page.append("---")
            art_page.append("")
            art_page.append(art_body)
            art_page.append("")

            art_path.write_text("\n".join(art_page), encoding="utf-8")

            # Link relativo desde la página índice de la ley
            index_entries.append((art_title, f"{slug}/{art_filename}"))

        # 3) Generamos la página “corta” de la ley (Índice con links)
        law_index_path = OUT_NORMAS_DIR / f"{slug}.md"
        law_index = []
        law_index.append(f"# {titulo_norma}")
        law_index.append("")
        law_index.append(f"Última actualización automática: {fetched}")
        if source_url:
            law_index.append(f"Fuente (LeyChile/BCN): {source_url}")
        law_index.append(f"Identificador: {key}")
        law_index.append("")
        law_index.append("## Índice")
        law_index.append("")

        if not index_entries:
            law_index.append("_No se encontraron artículos para generar páginas._")
        else:
            for title, rel_link in index_entries:
                law_index.append(f"- [{title}]({rel_link})")

        law_index.append("")
        law_index_path.write_text("\n".join(law_index), encoding="utf-8")

        # Cache hash (no es imprescindible, pero evita trabajo si no cambió)
        hash_path.write_text(xml_hash, encoding="utf-8")

        print(f"OK: {slug} -> índice + {len(index_entries)} artículos")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
