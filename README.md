# InnoWing Chatbot Challenge 2026

Materials for the InnoWing Chatbot Challenge — building a retrieval-augmented
(RAG) chatbot about the Tam Wing Fan Innovation Wing.

## Development status

> **Internal development:** the repository is not submission-ready yet. The
> crawler, text indexing utilities and baseline RAG components exist, but the
> runnable submission entry point, packaged dependencies, production corpus and
> production Chroma index are still missing.

See **[DEVELOPMENT_STATUS_ZH.md](DEVELOPMENT_STATUS_ZH.md)** for the visual
project dashboard, complete task list, expected results, dependencies and
current status. Contributors and coding agents should update that file in the
same pull request as the implementation it tracks.

## 👉 Start here: [`submission_repo/`](submission_repo/)

**Your bot lives in [`submission_repo/`](submission_repo/).** That folder is the
scaffold you fork, build on, and submit. Everything else in this repository is
supporting material.

## Repository layout

| Path | What it is |
|---|---|
| [`submission_repo/`](submission_repo/) | **The submission scaffold — start here.** The intended package is `main.py` + `bot/` + `build/`; current missing files and artifacts are tracked in [`DEVELOPMENT_STATUS_ZH.md`](DEVELOPMENT_STATUS_ZH.md). |
| [`Labs/`](Labs/) | Workshop lab notebooks (`lab1`–`lab5`) with a sample corpus and prebuilt index for practice. Not your submission. |
| [`Slides/`](Slides/) | Workshop decks: W0 (setup & rules), W1 (RAG pipeline), W2 (visual and physical data). |
| [`deprecated/`](deprecated/) | Old monolithic prototype, kept for reference only. **Do not build against it.** |

## Getting started

Requirements: Git, Python **3.9+**, and a code editor (VS Code recommended).

> The commands below describe the intended workflow. On the current `main`
> branch, `submission_repo/main.py`, `submission_repo/requirements.txt` and
> `submission_repo/.env.example` must be restored before this workflow can run
> from a fresh clone.

1. **Fork** this repository, then clone your fork.
2. Work inside [`submission_repo/`](submission_repo/):

   ```bash
   cd submission_repo
   python -m venv .venv
   # macOS & Linux:
   source .venv/bin/activate
   # Windows:
   .venv\Scripts\activate

   pip install -r requirements.txt
   ```

3. Add your credentials:

   ```bash
   cp .env.example .env   # then fill in your key
   python check_setup.py  # verify your setup before Workshop 1
   ```

4. Build your bot — edit `bot/answer.py` and the files under `build/`
   (`main.py`, `bot/llm.py`, `bot/store.py` are given), build your index, then run:

   ```bash
   python main.py "your question"
   ```

   That is the same command the grader uses.

## Workshops

Work through the notebooks in [`Labs/`](Labs/) alongside the decks in
[`Slides/`](Slides/). The labs are practice exercises and are **not** your
submission — see [`Labs/Notebook_Guide.md`](Labs/Notebook_Guide.md) for how they
map to the two workshops.
