import hashlib
import pathlib
import re
import unicodedata
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


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def text_of(xpath_result) -> str:
    parts = [x.strip() for x in xpath_result if x and x.strip() and x.strip() != " "]
    return " ".join(parts).strip()


def extract_body_excluding_metadatos(container_el) -> str:
    clone = etree.fromstring(etree.tostring(container_el))
    for m in clone.xpath('.//*[local-name()="Metadatos"]'):
        p = m.getparent()
        if p is not None:
            p.remove(m)
    txt = etree.tostring(clone, method="text", encoding="unicode")
    return clean_text(txt)


def infer_epigrafe_from_body(body: str) -> str:
    """
    Fallback: intenta inferir un epígrafe desde la primera línea del cuerpo.
    Útil cuando el XML no trae TituloParte.
    """
    if not body:
        return ""

    first_line = body.splitlines()[0].strip()
    if not first_line:
        return ""

    # Elimina prefijos típicos “Artículo 1°.-”, “ARTÍCULO 1°:”, etc.
    first_line = re.sub(
        r'^(art[ií]culo)\s+\S+\s*[\.\-–—:]*\s*',
        '',
        first_line,
        flags=re.IGNORECASE,
    ).strip()

    # Epígrafes demasiado largos no ayudan
    if first_line and len(first_line) <= 120:
        return first_line

    return ""


def find_article_nodes(root):
    """
    Artículos = nodos con atributo tipoParte="Artículo"/"Articulo"/"Artículo transitorio", etc.
    Retorna lista de tuplas: (num_label, epigrafe, body)
    """
    nodes = root.xpath('//*[@tipoParte]')
    arts = []
    seen = set()

    for el in nodes:
        tp = (el.get("tipoParte") or "").strip()
        tp_norm = strip_accents(tp).lower()

        if not tp_norm.startswith("articulo"):
            continue

        pid = (el.get("idParte") or "").strip()
        key = pid if pid else str(id(el))
        if key in seen:
            continue
        seen.add(key)

        nombre = text_of(el.xpath('.//*[local-name()="Metadatos"]//*[local-name()="NombreParte"]//text()'))
        titulo = text_of(el.xpath('.//*[local-name()="Metadatos"]//*[local-name()="TituloParte"]//text()'))

        # Etiqueta corta (ideal para TOC lateral): solo número de artículo
        if nombre:
            n_norm = strip_accents(nombre).lower()
            num_label = nombre if n_norm.startswith("articulo") else f"Artículo {nombre}"
        else:
            num_label = "Artículo"

        body = extract_body_excluding_metadatos(el)
        if not body:
            continue

        # Epígrafe preferente: TituloParte; si no existe, se infiere desde la primera línea del cuerpo
        epigrafe = titulo.strip() if titulo else ""
        if not epigrafe:
            epigrafe = infer_epigrafe_from_body(body)

        arts.append((num_label, epigrafe, body))

    return arts


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
        articles = find_article_nodes(root)

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
            lines.append("_No se encontraron artículos (tipoParte=Artículo). Publicando texto plano._")
            lines.append("")
            full_text = etree.tostring(root, method="text", encoding="unicode")
            lines.append(clean_text(full_text))
        else:
            # Índice con epígrafe (si existe)
            for i, (num_label, epigrafe, _) in enumerate(articles, start=1):
                anchor = f"articulo-{i:03d}"
                if epigrafe:
                    lines.append(f"- [{num_label} — {epigrafe}](#{anchor})")
                else:
                    lines.append(f"- [{num_label}](#{anchor})")
            lines.append("")

            # Secciones por artículo: TOC lateral quedará “Artículo X”, epígrafe se ve debajo
            for i, (num_label, epigrafe, body) in enumerate(articles, start=1):
                anchor = f"articulo-{i:03d}"
                lines.append(f'<a id="{anchor}"></a>')
                lines.append(f"## {num_label}")
                lines.append("")
                if epigrafe:
                    lines.append(f"_{epigrafe}_")
                    lines.append("")
                lines.append(body)
                lines.append("")

        out_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"OK: {slug} -> {len(articles)} artículos")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
