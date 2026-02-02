# extract.py
from __future__ import annotations

import os
import posixpath
import re
import logging
import zipfile
import unicodedata
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from bs4 import BeautifulSoup
from lxml import etree

logger = logging.getLogger(__name__)

NON_CHAPTER_KEYWORDS = [
    # front matter
    "cover", "portada",
    "title", "título", "titulo",
    "copyright", "derechos",
    "dedication", "dedicatoria",
    "epigraph", "epígrafe", "epigrafe",
    "preface", "prefacio",
    "foreword", "prólogo", "prologo",
    "introduction", "introducción", "introduccion",
    "acknowledgements", "acknowledgments", "agradecimientos",
    "contents", "table of contents", "Index", "índice", "indice", "sumario",

    # back matter
    "notes", "notas",
    "endnotes",
    "bibliography", "bibliografía", "bibliografia",
    "references", "referencias",
    "glossary", "glosario",
    "appendix", "apéndice", "apendice", "anexo",
    "afterword", "epílogo", "epilogo",
    "colophon", "colofón", "colofon",
    "credits", "créditos", "creditos",
    "reconocimientos"
]

TOC_KEYWORDS = ["indice", "índice", "Index", "contents", "table of contents", "sumario"]

# Common file/id hints for non-content docs
NON_CHAPTER_PATH_HINTS = [
    "cover", "portada",
    "title", "titulo",
    "toc", "contents",
    "nav", "ncx",
    "copyright",
    "preface", "foreword", "introduction", "prologue", "epilogue",
    "acknowledg", "agrade", "epigraph", "epigrafe", "epígrafe"
    "bibliograph", "bibliograf",
    "notes", "notas", "dedication"
    "appendix", "apendice", "apéndice", "anexo",
    "glossary", "glosario", "reconocimientos"
]


@dataclass
class Chapter:
    idref: str
    href: str          # path inside the epub zip
    title: str
    text: str


def _read_xml(zf: zipfile.ZipFile, path: str) -> etree._Element:
    data = zf.read(path)
    return etree.fromstring(data)


def _join_epub_path(base_dir: str, href: str) -> str:
    """
    EPUB internal paths are POSIX-style. Always join with posixpath.
    """
    if not base_dir:
        return href
    return posixpath.normpath(posixpath.join(base_dir, href))


def _html_to_text(xhtml_bytes: bytes) -> str:
    soup = BeautifulSoup(xhtml_bytes, features="xml")

    # Remove common non-content elements
    for tag in soup(["script", "style"]):
        tag.decompose()

    # Some ebooks place navigation in <nav> (EPUB3) or similar
    for tag in soup.find_all(["nav", "header", "footer", "aside"]):
        tag.decompose()

    text = soup.get_text(separator="\n")

    # Normalize whitespace
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    text = "\n".join(lines)

    # Optional: collapse excessive blank lines (already filtered) and trim
    return text.strip()


def _parse_container_for_opf_path(zf: zipfile.ZipFile) -> str:
    """
    Reads META-INF/container.xml to locate the OPF (package) file.
    Falls back to common defaults if needed.
    """
    try:
        root = _read_xml(zf, "META-INF/container.xml")
        ns = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
        rootfile = root.find(".//c:rootfile", namespaces=ns)
        if rootfile is not None and rootfile.get("full-path"):
            return rootfile.get("full-path")
    except KeyError:
        pass

    # Fallbacks (not ideal, but helps with odd EPUBs)
    for candidate in ("content.opf", "OEBPS/content.opf", "OPS/content.opf"):
        if candidate in zf.namelist():
            return candidate

    raise ValueError("Could not locate OPF package file (content.opf).")


def _parse_opf_metadata(opf_root: etree._Element) -> Tuple[Optional[str], List[str]]:
    ns = {
        "opf": "http://www.idpf.org/2007/opf",
        "dc": "http://purl.org/dc/elements/1.1/",
    }

    title_el = opf_root.find(".//dc:title", namespaces=ns)
    title = title_el.text.strip() if title_el is not None and title_el.text else None

    creators = []
    for c in opf_root.findall(".//dc:creator", namespaces=ns):
        if c.text and c.text.strip():
            creators.append(c.text.strip())

    return title, creators


def _parse_opf_manifest_spine(opf_root: etree._Element) -> Tuple[Dict[str, Dict[str, str]], List[str]]:
    ns = {"opf": "http://www.idpf.org/2007/opf"}

    manifest: Dict[str, Dict[str, str]] = {}
    for item in opf_root.findall(".//opf:manifest/opf:item", namespaces=ns):
        item_id = item.get("id")
        if not item_id:
            continue
        manifest[item_id] = {
            "href": item.get("href", ""),
            "media-type": item.get("media-type", ""),
        }

    spine_ids: List[str] = []
    for itemref in opf_root.findall(".//opf:spine/opf:itemref", namespaces=ns):
        idref = itemref.get("idref")
        if idref:
            spine_ids.append(idref)

    return manifest, spine_ids


def _build_toc_title_map_from_ncx(
    zf: zipfile.ZipFile,
    ncx_path: str,
    opf_dir: str,
) -> Dict[str, str]:
    """
    Builds a mapping: href(without fragment) -> navLabel text.
    This is the most reliable source of chapter titles for EPUB2.
    """
    toc_map: Dict[str, str] = {}

    ncx_root = _read_xml(zf, ncx_path)
    ns = {"ncx": "http://www.daisy.org/z3986/2005/ncx/"}

    nav_map = ncx_root.find(".//ncx:navMap", namespaces=ns)
    if nav_map is None:
        return toc_map

    # Flatten navPoints
    navpoints = nav_map.findall(".//ncx:navPoint", namespaces=ns)
    for np in navpoints:
        label = np.find(".//ncx:navLabel/ncx:text", namespaces=ns)
        content = np.find(".//ncx:content", namespaces=ns)
        if label is None or content is None:
            continue
        if not (label.text and content.get("src")):
            continue

        src = content.get("src")
        # src is relative to the NCX file location in many EPUBs, but often matches OPF-relative too.
        # We normalize against OPF dir to keep consistent with manifest href resolution.
        # Remove any fragment like "chapter.html#foo"
        src_no_frag = src.split("#", 1)[0]
        normalized = _join_epub_path(opf_dir, src_no_frag)

        toc_map[normalized] = label.text.strip()

    return toc_map

def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"\s+", " ", s)
    return s

def _extract_display_title_from_body(raw: bytes) -> Optional[str]:
    """
    Extract a human chapter/title string from the body. Handles:
    - <h1>/<h2>/<h3>
    - Calibre-style headings in <p>/<div> with classes like class_s2h, class_s2h1, etc.
    """
    soup = BeautifulSoup(raw, features="xml")

    # 1) Standard headings first
    h = soup.find(["h1", "h2", "h3"])
    if h:
        txt = h.get_text(" ", strip=True)
        if txt:
            return txt

    # 2) Heading-like <p> or <div> by class/id heuristics
    candidates = soup.find_all(["p", "div"], limit=40)
    for el in candidates:
        # class can be a list -> normalize to a single lowercase string
        cls_list = el.get("class") or []
        cls = " ".join(cls_list).lower()
        el_id = (el.get("id") or "").lower()

        txt = el.get_text(" ", strip=True)
        if not txt:
            continue

        # Keep titles short-ish; avoid picking whole paragraphs
        if len(txt) > 140:
            continue

        # Heuristic signals for headings:
        # - explicit heading/title tokens
        # - calibre/common patterns: s2h, s2h1, *_h, *_h1, "calibre" + heading styles
        looks_heading = (
            any(tok in cls for tok in ["h1", "h2", "h3", "title", "heading", "chapter"])
            or any(tok in el_id for tok in ["h1", "h2", "h3", "title", "heading", "chapter"])
            or re.search(r"(?:^|[_\-\s])s\d*h\d*(?:$|[_\-\s])", cls) is not None   # matches s2h, s2h1, s3h, etc.
            or re.search(r"(?:^|[_\-\s])h\d*(?:$|[_\-\s])", cls) is not None      # matches h, h1, h2 as standalone tokens
        )

        if looks_heading:
            return txt

    return None

def looks_like_non_chapter(title: str) -> bool:
    t = _norm(title)
    return any(_norm(kw) in t for kw in NON_CHAPTER_KEYWORDS)

def looks_like_non_chapter_path(href: str, idref: str) -> bool:
    h = _norm(href)
    i = _norm(idref)
    blob = f"{h} {i}"
    return any(_norm(hint) in blob for hint in NON_CHAPTER_PATH_HINTS)

def looks_like_toc(text: str) -> bool:
    """
    Detect TOC pages even when title_guess is misleading.
    Signals:
       - 'ÍNDICE/CONTENTS/SUMARIO' near the top
       - many dot-leader lines (..........)
       - many numeric-only lines (page numbers)
    """
    if not text:
        return False
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return False
 
    top = "\n".join(lines[:60])
    top_norm = _norm(top)
 
    has_toc_word = any(_norm(k) in top_norm for k in TOC_KEYWORDS)
    if not has_toc_word:
        return False
 
    dot_leaders = sum(1 for ln in lines[:300] if re.search(r"\.{8,}", ln))
    numeric_only = sum(1 for ln in lines[:300] if re.fullmatch(r"\d{1,4}", ln))
 
    # Tuned to common printed-book style TOCs
    return dot_leaders >= 8 and numeric_only >= 8

def looks_like_copyright_or_imprint(text: str) -> bool:
    """
    Catch copyright/imprint pages that can be long enough to pass length checks.
    """
    top = _norm("\n".join([ln.strip() for ln in text.splitlines()[:80] if ln.strip()]))
    keywords = [
        "reservados todos los derechos",
        "copyright",
        "isbn",
        "deposito legal",
        "depósito legal",
        "impreso en",
        "printed in",
        "editorial",
        "derechos",
    ]
    hits = sum(1 for k in keywords if _norm(k) in top)
    return hits >= 2

def should_include_chapter(
    *,
    title: str,
    text: str,
    href: str,
    idref: str,
    min_words: int = 300,
) -> bool:
    # Drop very short docs (covers, title pages, etc.)
    if len(text.split()) < min_words:
        return False

    # Drop by title keyword
    if looks_like_non_chapter(title):
        return False

    # Drop by file/id hints
    if looks_like_non_chapter_path(href, idref):
        return False

    return True


def extract_chapters(epub_path: str, min_words: int) -> Tuple[Dict[str, object], List[Chapter]]:
    """
    Returns:
      metadata: dict with title/authors/opf_path
      chapters: list of Chapter in spine reading order
    """
    with zipfile.ZipFile(epub_path, "r") as zf:
        logger.debug("Parsing OPF metadata...")
        opf_path = _parse_container_for_opf_path(zf)
        opf_dir = posixpath.dirname(opf_path)

        opf_root = _read_xml(zf, opf_path)

        title, authors = _parse_opf_metadata(opf_root)
        logger.debug(f"title: {title} / authors: {authors}")
        manifest, spine_ids = _parse_opf_manifest_spine(opf_root)
        logger.info("Detected %d spine items", len(spine_ids))

        # Locate NCX via spine toc attribute or manifest scan
        ns = {"opf": "http://www.idpf.org/2007/opf"}
        spine_el = opf_root.find(".//opf:spine", namespaces=ns)
        toc_id = spine_el.get("toc") if spine_el is not None else None

        ncx_path = None
        if toc_id and toc_id in manifest:
            ncx_href = manifest[toc_id].get("href", "")
            if ncx_href:
                ncx_path = _join_epub_path(opf_dir, ncx_href)

        # Fallback: find any .ncx in manifest
        if not ncx_path:
            for _id, meta in manifest.items():
                href = meta.get("href", "")
                mt = meta.get("media-type", "")
                if mt == "application/x-dtbncx+xml" or href.lower().endswith(".ncx"):
                    ncx_path = _join_epub_path(opf_dir, href)
                    break

        toc_title_map: Dict[str, str] = {}
        if ncx_path and ncx_path in zf.namelist():
            toc_title_map = _build_toc_title_map_from_ncx(zf, ncx_path, opf_dir)

        chapters: List[Chapter] = []

        for idref in spine_ids:
            item = manifest.get(idref)
            if not item:
                continue

            href = item.get("href", "")
            media_type = item.get("media-type", "").lower()

            # Keep XHTML/HTML documents only
            if media_type not in ("application/xhtml+xml", "text/html"):
                logger.debug("Skipping non-document spine item: %s (%s)", idref, media_type)
                continue

            internal_path = _join_epub_path(opf_dir, href)
            if internal_path not in zf.namelist():
                # Some EPUBs use slightly different normalization; skip if missing.
                continue

            raw = zf.read(internal_path)
            text = _html_to_text(raw)

            # Title preference order: NCX TOC label -> <title>/<h1> -> idref/href
            title_guess = toc_title_map.get(internal_path)

            if not title_guess:
                title_guess = _extract_display_title_from_body(raw)

            if not title_guess:
                soup = BeautifulSoup(raw, features="xml")
                t = soup.find("title")
                title_guess = t.get_text(strip=True) if t else None

            if not title_guess:
                title_guess = idref or os.path.basename(internal_path)

            if looks_like_toc(text):
                logger.debug("Skipping TOC-like document: %s [%s]", title_guess, internal_path)
                continue

            if looks_like_copyright_or_imprint(text):
                logger.debug("Skipping copyright/imprint: %s [%s]", title_guess, internal_path)
                continue

            if not should_include_chapter(
                title=title_guess,
                text=text,
                href=internal_path,
                idref=idref,
                min_words=min_words,
            ):
                logger.debug("Skipping non-chapter: %s", title_guess)
                continue

            chapters.append(Chapter(idref=idref, href=internal_path, title=title_guess, text=text))
            logger.info("Added chapter: %s (%d words)", title_guess, len(text.split()))

        metadata = {
            "title": title,
            "authors": authors,
            "opf_path": opf_path,
            "ncx_path": ncx_path,
            "chapter_count": len(chapters),
        }
        return metadata, chapters