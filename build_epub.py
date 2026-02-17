# build_epub.py
from __future__ import annotations

from ebooklib import epub
import logging
import re
import html

logger = logging.getLogger(__name__)

def summary_to_html(summary_text: str) -> str:
    """
    Convert a lightweight Markdown-ish summary into EPUB-friendly HTML.
    """
    lines = [ln.rstrip() for ln in summary_text.splitlines()]
    html_out: list[str] = []
    in_ul = False
    in_ol = False
    in_nested_ul = False

    def close_nested_ul_if_open():
        nonlocal in_nested_ul
        if in_nested_ul:
            html_out.append("</ul>")
            in_nested_ul = False

    def close_lists():
        nonlocal in_ul, in_ol, in_nested_ul
        close_nested_ul_if_open()
        if in_ul:
            html_out.append("</ul>")
            in_ul = False
        if in_ol:
            # if we opened a nested UL, it was already closed above
            html_out.append("</ol>")
            in_ol = False
    
    def inline_format(s: str) -> str:
        # Escape first prevent summaries from breaking XHTML
        s = html.escape(s)

        # Bold
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)

        # Italic
        s = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<em>\1</em>", s)

        return s

    for raw in lines:
        ln = raw.strip()

        if not ln:
            continue
        
        # Headings: #, ##, ###
        m = re.match(r"^(#{1,6})\s+(.*)$", ln)
        if m:
            close_lists()
            level = len(m.group(1))
            text = inline_format(m.group(2).strip())
            html_out.append(f"<h{level}>{text}</h{level}>")
            continue

        # Ordered list: "1. item"
        m = re.match(r"^(\d+)\.\s+(.*)$", ln)
        if m:
            if in_ul:
                html_out.append("</ul>")
                in_ul = False
            
            close_nested_ul_if_open()

            if not in_ol:
                html_out.append("<ol>")
                in_ol = True

            item = inline_format(m.group(2).strip())
            html_out.append(f"<li>{item}</li>")
            continue

        # Unordered list item: "- text"
        if ln.startswith("- "):
            item = inline_format(ln[2:].strip())

            if in_ol:
                if not html_out or not html_out[-1].endswith("</li>"):
                    # Fallback: if can't nest cleanly, treat as separate UL
                    close_lists()
                    html_out.append("<ul>")
                    in_ul = True
                    html_out.append(f"<li>{item}</li>")
                    continue

                # Open nested ul inside last li by rewriting the last li line
                last = html_out.pop()
                # last is "<li>...</li>"
                last_open = last[:-5]  # strip "</li>"
                if not in_nested_ul:
                    html_out.append(last_open + "<ul>")
                    in_nested_ul = True
                else:
                    html_out.append(last_open)

                html_out.append(f"<li>{item}</li>")
                continue

            # Top-level UL
            if in_ol:
                html_out.append("</ol>")
                in_ol = False

            if not in_ul:
                html_out.append("<ul>")
                in_ul = True

            html_out.append(f"<li>{item}</li>")
            continue

        # Paragraph
        close_lists()
        html_out.append(f"<p>{inline_format(ln)}</p>")

    # Finalize: if nested UL is open, close it and close the parent LI properly
    if in_nested_ul:
        html_out.append("</ul></li>")
        in_nested_ul = False

    close_lists()
    return "\n".join(html_out)

def build_summary_epub(metadata: dict, chapter_summaries: list[dict], out_path: str):
    book = epub.EpubBook()

    title = metadata.get("title") or "Untitled"
    authors = metadata.get("authors") or []
    authors_str = ", ".join(authors) if authors else ""
    for a in authors:
        book.add_author(a)
    
    book.set_title(f"{title} de {authors_str} — Resumen")

    # Intro page
    intro = epub.EpubHtml(title="Overview", file_name="intro.xhtml", lang="es")
    intro.content = f"<h1>Introducción</h1><p>Libro: {title}</p><p>Autores(s): {authors_str}</p>"
    book.add_item(intro)

    spine = ["nav", intro]
    toc = [epub.Link("intro.xhtml", "Introducción", "intro")]

    for i, ch in enumerate(chapter_summaries, start=1):
        chap_title = ch["title"]
        html = summary_to_html(ch["summary"])

        page = epub.EpubHtml(
            title=chap_title,
            file_name=f"summary_{i:03d}.xhtml",
            lang="es",
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
