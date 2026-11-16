"""Command line.

    python -m shared_record screen   --model MODEL [--max-cost USD] [--n-knowledge N] [--n-no-source N] [--targets N]
    python -m shared_record sessions --model MODEL [--max-cost USD] [--limit N]
    python -m shared_record export-batches --models M1 M2 ... [--force]
    python -m shared_record code --coder MODEL [--max-cost USD]
    python -m shared_record import-labels
    python -m shared_record export-handcoding --models M1 M2 ...
    python -m shared_record agreement
    python -m shared_record analyze [--models M1 M2 ...]
    python -m shared_record tables
    python -m shared_record figure

--coding-dir (default data/coding) and --out (default results) keep a trial run, e.g. with the mock
model, apart from the released data and results.

MODEL is a key of models.toml. `mock` runs offline. Other models are called through the endpoint in
models.toml (OpenRouter by default) with the key in the environment variable OPENROUTER_API_KEY.
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from . import analysis, coding, figure, items, sessions, tables
from .client import Client, load_models


def _client(name: str, max_cost: float | None) -> Client:
    models = load_models()
    if name not in models:
        raise SystemExit(f"unknown model {name!r}; models.toml has: {', '.join(models)}")
    cfg = models[name]
    oracle = items.oracle() if cfg["id"].startswith("mock") else None
    return Client(cfg, oracle=oracle, max_cost=max_cost)


async def _run(client: Client, coro):
    try:
        return await coro
    finally:
        await client.close()


def main():
    ap = argparse.ArgumentParser(prog="python -m shared_record", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["screen", "sessions", "export-batches", "code", "import-labels",
                                        "export-handcoding", "agreement", "analyze", "tables", "figure"])
    ap.add_argument("--model", help="screen / sessions: the model under test (models.toml key)")
    ap.add_argument("--coder", help="code: the coding model (models.toml key)")
    ap.add_argument("--models", nargs="+", help="export-batches / export-handcoding / analyze: models")
    ap.add_argument("--max-cost", type=float, help="spending cap in US dollars for new calls")
    ap.add_argument("--n-knowledge", type=int, help="screen: only the first N knowledge candidates")
    ap.add_argument("--n-no-source", type=int, help="screen: only the first N no-source candidates")
    ap.add_argument("--targets", type=int, default=items.N_TARGETS, help="screen: targets per group (default 200)")
    ap.add_argument("--limit", type=int, help="sessions: only the first N targets per group")
    ap.add_argument("--coding-dir", default=str(coding.CODING),
                    help="folder of batches/, labels.csv and handcoding.csv (default data/coding)")
    ap.add_argument("--out", default=str(analysis.OUT), help="analyze / tables / figure: results folder")
    ap.add_argument("--force", action="store_true", help="export-batches: replace existing label files")
    a = ap.parse_args()
    cd, out = Path(a.coding_dir), Path(a.out)
    batches, labels, hand = cd / "batches", cd / "labels.csv", cd / "handcoding.csv"
    if a.command in ("screen", "sessions"):
        if not a.model:
            ap.error(f"{a.command} needs --model")
        c = _client(a.model, a.max_cost)
        if a.command == "screen":
            asyncio.run(_run(c, items.run_screening(c, n_knowledge=a.n_knowledge, n_no_source=a.n_no_source,
                                                    targets=a.targets)))
        else:
            asyncio.run(_run(c, sessions.run_sessions(c, limit=a.limit)))
    elif a.command == "export-batches":
        coding.export_batches(a.models or analysis.DEFAULT_MODELS, out=batches, force=a.force)
    elif a.command == "code":
        if not a.coder:
            ap.error("code needs --coder")
        c = _client(a.coder, a.max_cost)
        asyncio.run(_run(c, coding.code_batches(c, batches)))
    elif a.command == "import-labels":
        coding.import_labels(batches, labels)
    elif a.command == "export-handcoding":
        coding.export_handcoding(a.models or analysis.DEFAULT_MODELS, labels, batches, hand)
    elif a.command == "agreement":
        coding.compute_agreement(hand, labels)
    elif a.command == "analyze":
        analysis.main(a.models, out=out, labels_csv=labels)
    elif a.command == "tables":
        tables.main(out, hand, labels)
    else:
        figure.main(out)


if __name__ == "__main__":
    main()
