# build_epub.py
from __future__ import annotations

from ebooklib import epub
import re
import logging

logger = logging.getLogger(__name__)

def summary_to_html(summary_text: str) -> str:
    # Very light formatting: bullets -> <li>, keep everything else <p>
    lines = [ln.rstrip() for ln in summary_text.splitlines()]
    html = []
    in_ul = False

    def close_ul():
        nonlocal in_ul
        if in_ul:
            html.append("</ul>")
            in_ul = False

    for ln in lines:
        if not ln.strip():
            close_ul()
            continue

        if ln.strip().startswith("- "):
            if not in_ul:
                html.append("<ul>")
                in_ul = True
            html.append(f"<li>{ln.strip()[2:]}</li>")
        else:
            close_ul()
            html.append(f"<p>{ln}</p>")

    close_ul()
    return "\n".join(html)

def build_summary_epub(metadata: dict, chapter_summaries: list[dict], out_path: str):
    book = epub.EpubBook()

    title = metadata.get("title") or "Untitled"
    book.set_title(f"{title} — Resumen")

    authors = metadata.get("authors") or []
    for a in authors:
        book.add_author(a)

    # Intro page
    intro = epub.EpubHtml(title="Overview", file_name="intro.xhtml", lang="en")
    intro.content = f"<h1>Summaries</h1><p>Book: {title}</p>"
    book.add_item(intro)

    spine = ["nav", intro]
    toc = [epub.Link("intro.xhtml", "Overview", "intro")]

    for i, ch in enumerate(chapter_summaries, start=1):
        chap_title = ch["title"]
        html = summary_to_html(ch["summary"])

        page = epub.EpubHtml(
            title=chap_title,
            file_name=f"summary_{i:03d}.xhtml",
            lang="en",
        )
        page.content = f"<h2>{chap_title}</h2>\n{html}"
        book.add_item(page)

        toc.append(epub.Link(page.file_name, chap_title, f"sum_{i:03d}"))
        spine.append(page)

    book.toc = toc
    book.spine = spine
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    epub.write_epub(out_path, book)
