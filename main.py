# main.py
from __future__ import annotations

import sys
import argparse
import logging
from pathlib import Path 

from extract import extract_chapters
from summarize import DiskCache, summarize_chapter
from build_epub import build_summary_epub
from summarize import load_prompt_template

logger = logging.getLogger(__name__)

formatter = logging.Formatter(
    "{asctime} - {levelname} - {name} - {message}", 
    style="{", 
    datefmt="%d-%m-%Y %H:%M:%S"
    )

def set_console_logger(logger):
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

def set_file_logger(logger, log_name):
    file_handler = logging.FileHandler(f"{log_name}.log", mode="a", encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

def configure_logging(write_file: bool, input_epub: str) -> None:
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        return

    set_console_logger(logger)

    if write_file:
        file_name_no_ext = Path(input_epub).stem
        set_file_logger(logger, file_name_no_ext)


def main():
    if len(sys.argv) < 2:
        print("Usage: python main.py <input_epub> <output_epub>")
        raise SystemExit(2)
    
    ap = argparse.ArgumentParser()
    ap.add_argument("input_epub", type=str)
    ap.add_argument("output_epub", type=str)
    ap.add_argument("--model", type=str, help="The Ollama model used with the prompt to summarize the ebook", default="qwen2.5:3b", required=False)
    ap.add_argument("--prompt", type=str, help="The name of the file that contains the actual prompt (must be in parent/prompts)", default="resumir_capitulo_es_v2", required=False)
    ap.add_argument("--min-words", type=int, help="The minimum number of words for a chapter to be summarized", default=300, required=False)
    ap.add_argument("--compress-chapters", help="Whether to compress chapters to speed up inference", action="store_true", required=False)
    ap.add_argument("--logfile", help="Whether to create a logfile", action="store_true", required=False)
    args = ap.parse_args()

    input_path = Path(args.input_epub)
    if not input_path.exists():
        raise FileNotFoundError(f"File does not exit: {args.input_epub}")

    configure_logging(args.logfile, args.input_epub)

    logger.info("Starting epub summarization...")
    logger.info(f"Book: {input_path.name}")
    logger.info("Extracting chapters from epub...")
    metadata, chapters = extract_chapters(args.input_epub, args.min_words)
    logger.info("Extracted %d chapters", len(chapters))

    cache = DiskCache(".cache_summaries")
    chapter_summaries = []

    # Load prompt template once and use for all chapters
    prompt_template = load_prompt_template(args.prompt)
    logger.info("Loaded prompt template: %s", args.prompt)

    for ch in chapters:
        summary = summarize_chapter(ch.title, ch.text, cache=cache, compress=args.compress_chapters, model=args.model, prompt_template=prompt_template)
        chapter_summaries.append({"title": ch.title, "summary": summary})

    logger.info("Packing output epub...")
    build_summary_epub(metadata, chapter_summaries, args.output_epub)
    output_path = Path(args.output_epub)
    if output_path.exists():
        logger.info(f"{args.output_epub} successfully written")
    else:
        logger.error(f"Failed to write {args.output_epub}")
    
    logger.info("End")

if __name__ == "__main__":
    main()
