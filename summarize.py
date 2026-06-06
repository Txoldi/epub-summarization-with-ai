# summarize.py
from __future__ import annotations

import logging

import hashlib
import os
import re
import time
import requests
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"

# ------------- Handle Prompt Files -----------------

def load_prompt_template(prompt_name: str) -> str:
    path = PROMPTS_DIR / f"{prompt_name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8")

def render_prompt(template: str, *, title: str, text: str) -> str:
    """
    Renders the template using str.format placeholders.
    """
    return template.format(title=title, text=text).strip()

# ---------- Caching ----------

def stable_hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()

class DiskCache:
    def __init__(self, cache_dir: str = ".cache_summaries"):
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)

    def get(self, key: str) -> Optional[str]:
        path = os.path.join(self.cache_dir, f"{key}.txt")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        return None

    def set(self, key: str, value: str) -> None:
        path = os.path.join(self.cache_dir, f"{key}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(value)

def sample_middle(
    text: str,
    *,
    every: int = 12,
    max_samples: int = 8,
    min_words: int = 40,
) -> str:
    """
    Selects a few representative middle paragraphs to recover narrative flow.
    """
    paragraphs = [
        p.strip()
        for p in text.splitlines()
        if len(p.split()) >= min_words
    ]

    if len(paragraphs) <= max_samples:
        return "\n".join(paragraphs)

    samples = paragraphs[::every][:max_samples]
    return "\n".join(samples)


# ---------- LLM backend interface ----------

def call_llm(model: str, prompt: str, *, num_predict: int = 280) -> str:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "num_predict": num_predict,  # hard cap output tokens
            "temperature": 0.2,
            "top_p": 0.9,
        },
    }
    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=(10, 600))
    except requests.exceptions.ConnectionError as e:
        raise RuntimeError(
            f"Ollama is unreachable at {OLLAMA_URL}. Make sure Ollama is running with `ollama serve`."
        ) from e
    except requests.exceptions.Timeout as e:
        raise RuntimeError(
            f"Ollama timed out while generating with model '{model}'. Try again, use a smaller model, or enable --compress-chapters."
        ) from e

    try:
        r.raise_for_status()
    except requests.exceptions.HTTPError as e:
        detail = r.text.strip()
        message = f"Ollama returned HTTP {r.status_code} for model '{model}'."
        if r.status_code == 404:
            message += f" The model may be missing; try `ollama pull {model}`."
        if detail:
            message += f" Response: {detail[:500]}"
        raise RuntimeError(message) from e

    try:
        data = r.json()
    except ValueError as e:
        raise RuntimeError("Ollama returned a non-JSON response.") from e

    response = data.get("response")
    if response is None:
        raise RuntimeError(f"Ollama response did not include a 'response' field: {data}")

    return response.strip()

def call_llm_with_retry(model: str, prompt: str, *, num_predict: int = 280, max_retries: int = 4, base_sleep: float = 1.5) -> str:
    last_err = None
    for attempt in range(max_retries):
        try:
            return call_llm(model, prompt, num_predict=num_predict)
        except Exception as e:
            last_err = e
            if attempt < max_retries - 1:
                sleep_for = base_sleep * (2 ** attempt)
                logger.warning(
                    "LLM call failed on attempt %d/%d; retrying in %.1fs: %s",
                    attempt + 1,
                    max_retries,
                    sleep_for,
                    e,
                )
                time.sleep(sleep_for)
    raise RuntimeError(f"LLM call failed after {max_retries} retries: {last_err}") from last_err

# ---------- Chapter compression (one call per chapter) ----------

def compress_chapter(
    text: str,
    *,
    head_words: int = 320,
    tail_words: int = 320,
    max_signal_lines: int = 18,
    max_line_len: int = 220,
    include_middle: bool = True,
) -> str:
    """
    Compresses a chapter to reduce tokens dramatically while preserving:
    - opening context (head)
    - closing context (tail)
    - a few high-signal lines (dates/numbers/proper nouns)
    """
    words = text.split()
    if len(words) <= head_words + tail_words + 250:
        return text

    head = " ".join(words[:head_words])
    tail = " ".join(words[-tail_words:])

    signal_lines: List[str] = []
    for line in text.splitlines():
        line = line.strip()
        if len(line) < 60:
            continue

        # Heuristics: years, numbers, or likely proper nouns
        has_year = re.search(r"\b(1[6-9]\d{2}|20\d{2})\b", line) is not None
        has_number = re.search(r"\b\d+\b", line) is not None
        has_proper = re.search(r"\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,}\b", line) is not None

        if has_year or has_number or has_proper:
            # prevent extremely long lines from bloating the prompt
            if len(line) > max_line_len:
                line = line[:max_line_len].rstrip() + "…"
            signal_lines.append(line)

        if len(signal_lines) >= max_signal_lines:
            break

    signal = "\n".join(signal_lines)

    middle = ""
    if include_middle:
        middle = sample_middle(text)
    
    parts = [
        "[INICIO]",
        head,
    ]

    if middle:
        parts.extend(["", "[MEDIO]", middle])

    if signal:
        parts.extend(["", "[SEÑALES]", signal])

    parts.extend(["", "[FINAL]", tail])

    return "\n".join(parts)

# ---------- Summarization logic (one call per chapter) ----------

def summarize_chapter(
    title: str,
    text: str,
    *,
    compress: bool,
    cache: DiskCache,
    model: str,
    prompt_template: str,
    chapter_number: Optional[int] = None,
    total_chapters: Optional[int] = None,
) -> str:
    template_hash = stable_hash(prompt_template)
    # Cache keyed by model + prompt template hash (prompt content) + chapter title + chapter text
    key = stable_hash(f"{model}|{template_hash}|{title}|{stable_hash(text)}")
    chapter_label = (
        f"chapter {chapter_number}/{total_chapters}"
        if chapter_number is not None and total_chapters is not None
        else "chapter"
    )

    cached = cache.get(key)
    if cached:
        logger.info("Cache hit for %s: %s", chapter_label, title)
        return cached
    
    logger.info("Cache miss for %s: %s", chapter_label, title)
    start_time = time.monotonic()
    wc_full = len(text.split())

    if compress:
        text = compress_chapter(text)
        wc_comp = len(text.split())
        logger.info(
            "Summarizing %s '%s' (compressed %d words from %d)...",
            chapter_label,
            title,
            wc_comp,
            wc_full,
        )
    else:
        logger.info("Summarizing %s '%s' full text (%d words)...", chapter_label, title, wc_full)

    prompt = render_prompt(prompt_template, title=title, text=text)

    # Slightly higher output cap for the final chapter summary
    summary = call_llm_with_retry(model, prompt, num_predict=360)
    cache.set(key, summary)
    elapsed = time.monotonic() - start_time
    logger.info("Finished %s in %.1fs: %s", chapter_label, elapsed, title)
    return summary
