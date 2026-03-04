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
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def extract_body_excluding_metadatos(container_el) -> str:
    # Clona el contenedor y elimina Metadatos para dejar solo el texto “normativo”
    clone = etree.fromstring(etree.tostring(container_el))
    for m in clone.xpath('.//*[local-name()="Metadatos"]'):
        p = m.getparent()
        if p is not None:
            p.remove(m)
    txt = etree.tostring(clone, method="text", encoding="unicode")
    return clean_text(txt)


def find_articles_by_nombreparte(root):
    """
    Encuentra “artículos” buscando Metadatos/NombreParte que empiece con Artículo/Articulo,
    y toma como contenedor el primer ancestro fuera de Metadatos.
    """
    nombre_nodes = root.xpath('//*[local-name()="NombreParte"]')

    articles = []
    seen = set()

    for node in nombre_nodes:
        title = " ".join([t.strip() for t in node.xpath(".//text()") if t and t.strip()]).strip()
        if not title:
            continue

        t0 = title.lower()
        if not (t0.startswith("artículo") or t0.startswith("articulo")):
            continue

        container = node.xpath(
            'ancestor::*[local-name()!="Metadatos" and local-name()!="NombreParte" and local-name()!="TituloParte"][1]'
        )
        if not container:
            continue
        container = container[0]

        key = id(container)
        if key in seen:
            continue
        seen.add(key)

        body = extract_body_excluding_metadatos(container)
        if body:
            articles.append((title, body))

    return articles


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

        (RAW_DIR / f"{slug}.xml").write_bytes(xml_bytes)
        (CACHE_DIR / f"{slug}.sha256").write_text(xml_hash, encoding="utf-8")

        root = etree.fromstring(xml_bytes)
        articles = find_articles_by_nombreparte(root)

        fetched = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        out_path = OUT_DIR / f"{slug}.md"
        lines = []
        lines.append(f"# {titulo_norma}")
        lines.append("")
        lines.append(f"Última actualización automática: {fetched}")
        if source_url:
            lines.append(f"Fuente (LeyChile/BCN): {source_url}")
        lines.append(f"Identificador: {key}")
        lines.append("")

        lines.append("## Índice")
        lines.append("")

        if not articles:
            lines.append("_No se encontraron artículos para generar secciones. Publicando texto plano._")
            lines.append("")
            full_text = etree.tostring(root, method="text", encoding="unicode")
            lines.append(clean_text(full_text))
        else:
            # Índice con anclas estables (#articulo-001, #articulo-002, ...)
            for i, (title, _) in enumerate(articles, start=1):
                anchor = f"articulo-{i:03d}"
                lines.append(f"- [{title}](#{anchor})")
            lines.append("")

            # Secciones por artículo (esto alimenta el TOC de MkDocs)
            for i, (title, body) in enumerate(articles, start=1):
                anchor = f"articulo-{i:03d}"
                lines.append(f'<a id="{anchor}"></a>')
                lines.append(f"## {title}")
                lines.append("")
                lines.append(body)
                lines.append("")

        out_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"OK: {slug} -> {len(articles)} artículos")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
