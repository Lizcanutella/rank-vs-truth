# Whatever You Say, Boss: code and data

Code and data for the paper *Whatever You Say, Boss: How Rank and Timing Shape What LLM Agents Enter in a
Shared Record*.

A reviewer agent (`wren`, the model under test) checks the answer key of a question bank. In one
conversation it answers three queue items (two fillers, then the target) and receives one scripted message
about the target from its lead or a peer, worded as an instruction or an opinion, in the target's turn
(*concurrent*) or one turn earlier (*one before*). We read the **entry** in the shared sheet and a
**private note** asked on a copy of the session. Targets are **Known** questions (the model answers them
correctly alone) or **No source** questions (the passage is missing, so the correct response is `FLAG`).

This repository contains the main experiment: item screening and selection, the session runner, the reply
parser, the comment coding, the analysis (six contrasts, cell tables, comment-coding aggregation and the
coder/hand agreement), and the generators of the paper's tables and timing figure.

## Layout

```
shared_record/      the package
  prompts.py        system prompt, messages, queue turns, private note
  parse.py          reply parser (answer / confidence / comment)
  items.py          screening and per-model selection of Known targets, fillers and No source targets
  sessions.py       cells and the three-turn session runner
  coding.py         comment coding (rubric, blind batches, labels, hand-coding sheet, agreement)
  analysis.py       cell table, six contrasts, inclusion check, report
  tables.py         LaTeX table bodies of the paper
  figure.py         the timing figure
  mock.py           offline simulated model (reviewer and coder)
  client.py         OpenAI-compatible client with disk cache and optional spending cap
models.toml         models and providers
data/candidates.jsonl              candidate pool: 700 MMLU / ARC-Challenge questions, 400 RACE-high questions
data/runs/<model>/screening.jsonl  five screening answers per candidate
data/runs/<model>/selection.jsonl  the model's 200 Known targets, 40 fillers, 200 No source targets
data/runs/<model>/sessions.jsonl   6,400 sessions: message, raw entry, private-note and filler replies
data/coding/labels.csv             coder label per coded following entry
data/coding/handcoding.csv         the 100 hand-coded entries (blind sheet with the hand labels)
results/                           analysis output on the released data
tests/
```

Models: `gemma-27b` (Gemma 3 27B, DeepInfra fp8), `qwen-9b` (Qwen 3.5 9B, Darkbloom), `luna` (GPT-6 Luna,
OpenAI), and `mock` (offline). All questions come from public benchmarks; RACE passages are not stored.

## Install

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12.

```
uv sync
uv run pytest
```

## Reproduce the paper's numbers from the released data

No model calls are needed.

```
uv run python -m shared_record analyze      # results/cells.csv, contrasts.csv, inclusion.csv, report.md
uv run python -m shared_record agreement    # coders vs hand coding (silent as the positive class)
uv run python -m shared_record tables       # results/tables/*.tex
uv run python -m shared_record figure       # results/figures/timing.pdf
```

Bootstrap intervals use fixed seeds, so they reproduce exactly.

## Run offline with the mock model

The `mock` model answers without network or key. Use separate output folders so the released data and
results stay untouched:

```
uv run python -m shared_record screen   --model mock --targets 20
uv run python -m shared_record sessions --model mock
uv run python -m shared_record export-batches --models mock --coding-dir data/coding-mock
uv run python -m shared_record code --coder mock --coding-dir data/coding-mock
uv run python -m shared_record import-labels --coding-dir data/coding-mock
uv run python -m shared_record analyze --models mock --coding-dir data/coding-mock --out results-mock
```

The mock has planted effects (a lead's instruction moves the entry more than a peer's, an earlier message
less than a concurrent one); they check the pipeline and say nothing about real models.

## Run with a model API

Set the key in the environment and give each run a spending cap in US dollars:

```
export OPENROUTER_API_KEY=...
uv run python -m shared_record screen   --model gemma-27b --max-cost 5
uv run python -m shared_record sessions --model gemma-27b --max-cost 20
```

Every call is cached in `data/cache/<model>.jsonl`, so an interrupted run resumes without repeating calls.
`screen` and `sessions` write to `data/runs/<model>/` and replace the released files of that model. Other
OpenAI-compatible endpoints can be used by setting `base_url` and `api_key_env` for a model in `models.toml`.

## Comment coding

Following entries are coded with the rubric in `shared_record/coding.py` (quoted in the paper's appendix).
`export-batches` draws a seeded sample of up to 60 following entries per model, group, cell and timing and
writes blind batch files plus `RUBRIC.md`; coders (LLM coding agents, or `code --coder MODEL`) write one
`labels_XXX.jsonl` per batch, and `import-labels` writes `labels.csv`. The analysis reads the coder's four
labels as two classes: flagged (ATTRIBUTES, DOUBTS) and silent (ASSERTS, NONE). The released labels come
from Claude Haiku 5.5 run as coding agents.
