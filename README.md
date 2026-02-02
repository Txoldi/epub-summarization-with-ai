# EPUB Summarizer

A Python tool that extracts chapters from an EPUB ebook, summarizes each chapter using a local LLM (via **Ollama**), and produces a new EPUB containing the summaries.

The project is designed to be:
- **offline-first** (local models via Ollama)
- **restartable** (disk cache)
- **prompt-driven** (prompts stored as external files)
- **reasonably fast** even on modest hardware

---

## Features

- Extracts chapters from EPUB files in reading order
- Filters out non-chapters (cover, TOC, prefaces, bibliography, etc.)
- Summarizes **one chapter per LLM call** (fast and robust)
- Optional chapter compression to speed up inference
- Prompt templates stored as `.txt` files
- Disk cache to avoid recomputation
- Outputs a new EPUB with one summary per chapter
- Works with **any Ollama model**

---

## Requirements

- Python **3.10+**
- [Ollama](https://ollama.com/) installed and running
- A local Ollama model (e.g. `qwen2.5:3b`)

## Installation

Clone the repository:

```bash
git clone https://github.com/Txoldi/epub-summarization-with-ai.git
cd epub-summarization-with-ai

### Create a virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate   # Linux / macOS
.venv\Scripts\activate      # Windows

pip install -r requirements.txt # Install dependencies

## Ollama setup

- Download and install Ollama ()
- Make sure Ollama is running => Ollama serve
- Pull the model you want to use => Ollama pull qwen2.5:3b

### GPU acceleration on Windows (important)

On Windows, Ollama uses Vulkan for GPU acceleration. Enable it before use:

setx OLLAMA_VULKAN 1

Then start Ollama

ollama serve

Verify usage with the command below:

nvidia-smi -l 1

## Usage

- Basic usage:

python -m main "input.epub" "output.epub"

- Full command with all input options:

python -m main input.epub output.epub \
  --model qwen2.5:3b \
  --prompt summarizr_chapter_en_v2 \
  --min-words 300 \
  --compress-chapters \
  --logfile

- Command line arguments:

input_epub: Path to the input EPUB file
output_epub: Path to the generated summary EPUB
--model: Ollama model to use (default: qwen2.5:3b)
--prompt: Prompt filename (without .txt) located in prompts/
--min-words: Minimum word count for a chapter to be summarized
--compress-chapters: Compress chapter text before summarization
--logfile: Write a detailed logfile next to the input EPUB

## Prompts

Prompts are stored as plain text files in the prompts/ directory. This model allows playing around
with different prompts and easily compare the results. 

prompts/
  summarize_chapter_en_v2.txt
  summarize_chapter_en_v3.txt
  summarize_and_comment_en_v1.txt

Prompt files use Python str.format placeholders:

Title of the chapter: {title}

Text to be summarized:
{text}

**Changing a prompt automatically invalidates the cache for affected chapters.**

## Caching

Summaries are cached on disk to:

- allow safe restarts
- avoid re-summarizing chapters
- speed up experimentation

The cache key depends on:

- model
- prompt contents
- chapter title
- chapter text

The cache is not automatically cleared.

## Output

The output EPUB contains:

- one section per chapter
- the chapter title
- the generated summary

It can be read in any standard EPUB reader.

## License

This project is released under the MIT License.

You are free to:

- use
- modify
- distribute
- include it in commercial projects

See the LICENSE file for details.