import asyncio
import csv
import json

import numpy as np
import pandas as pd
import pytest

from shared_record import analysis as A
from shared_record import coding as C
from shared_record import items as I
from shared_record import sessions as S
from shared_record.client import Client, load_models
from shared_record.parse import parse_reply, parse_strict
from shared_record.prompts import CONTROL, OWNER_LINE, PRIVATE_NOTE, single_turn, system_prompt


# ---------------------------------------------------------------- parser

@pytest.mark.parametrize("text,answer", [
    ("answer: B\nconfidence: high\ncomment: ok", "B"),
    ("answer: FLAG\nconfidence: low", "FLAG"),
    ("Answer C", "C"),
    ("answer A is wrong", None),
    ("answer: A\nconfidence: low\n\nanswer: D\nconfidence: high", "D"),
    ("**answer:** flag (passage missing)", "FLAG"),
    ("<think>answer: A</think>answer: C", "C"),
    ("no fields here", None),
])
def test_parse_strict(text, answer):
    assert parse_strict(text)["answer"] == answer


def test_parse_reply_strips_bracketed_tag():
    t = "(private note) answer: C\nconfidence: high"
    assert parse_strict(t)["answer"] is None
    assert parse_reply(t)["answer"] == "C"
    assert parse_reply("**[Private note]** answer: B")["answer"] == "B"
    assert parse_reply(None)["answer"] is None


# ---------------------------------------------------------------- items and selection

def test_candidate_pool_shape():
    cands = I.load_candidates()
    kinds = [c["kind"] for c in cands]
    assert kinds.count("knowledge") == 700 and kinds.count("no_source") == 400
    assert kinds == sorted(kinds, key=lambda k: k != "knowledge")  # knowledge first
    assert len({c["qid"] for c in cands}) == len(cands)


def test_select_cuts_targets_not_fillers():
    cands = [{"id": f"k{i}", "kind": "knowledge", "answer": "A", "wrong_order": ["B", "C", "D"]} for i in range(50)]
    cands += [{"id": f"n{i}", "kind": "no_source", "answer": "B", "wrong_order": ["A", "C", "D"]} for i in range(5)]
    screen = {c["id"]: {"n_key": 5 if c["kind"] == "knowledge" else 0, "modal": "A" if c["kind"] == "knowledge"
                        else "FLAG", "modal_count": 5, "answers": ["A"] * 5} for c in cands}
    sel = I.select(cands, screen, targets=20)
    assert len(sel["known"]) == 10 and len(sel["filler"]) == 40 and len(sel["no_source"]) == 5
    assert all(s["false_option"] == "B" for s in sel["known"])  # first wrong letter that is not the modal
    assert sel["shortfall"]


def test_modal_tie_break():
    assert I.modal(["B", "A", None, "B", "A"]) == ("A", 2)
    assert I.modal([None, None]) == (None, 0)


# ---------------------------------------------------------------- sessions

def _selection(model="gemma-27b"):
    return I.load_selection(I.RUNS, model)


def test_cells_per_target():
    p = S.plan(_selection(), limit=2)
    ss = S.all_sessions(p)
    by = {}
    for s in ss:
        by.setdefault(s.target["id"], []).append(s)
    for tid, group in by.items():
        g = group[0].target["group"]
        assert len(group) == (12 if g == "known" else 20)
        assert len({tuple(f["id"] for f in s.fillers) for s in group}) == 1  # fillers fixed per target
    assert len(ss) == 2 * 12 + 2 * 20


def test_session_turns():
    p = S.plan(_selection(), limit=1)
    ss = {(s.cell.name, s.timing): s for s in S.all_sessions(p) if s.target["group"] == "known"}
    conc, before = ss[("instruction/lead/false", "concurrent")], ss[("instruction/lead/false", "one_before")]
    t = conc.target
    assert conc.user(3)["content"].endswith(conc.cell.text) and conc.user(2)["content"].endswith("[messages] none")
    assert before.user(2)["content"].endswith(before.cell.text) and before.user(3)["content"].endswith("[messages] none")
    assert conc.cell.text.startswith(f"For {t['qid']}, enter {t['false_option']} (")
    owner = ss[("owner", "concurrent")]
    assert OWNER_LINE in owner.system()["content"] and OWNER_LINE not in conc.system()["content"]
    noline = ss[("no_line", "one_before")]
    assert "[messages]" not in noline.user(1)["content"] and "[messages]" not in noline.user(3)["content"]
    assert noline.user(2)["content"].endswith(noline.cell.text)
    assert ss[("control", "concurrent")].user(3)["content"].endswith(CONTROL)
    assert conc.note_prompt()["content"] == PRIVATE_NOTE.format(qid=t["qid"])


def test_released_sessions_match_design():
    """The released session ids are exactly the ones the runner would produce from the released selection."""
    for m in A.DEFAULT_MODELS:
        planned = {s.id for s in S.all_sessions(S.plan(_selection(m)))}
        with open(S.sessions_path(I.RUNS, m)) as f:
            released = {json.loads(l)["session_id"] for l in f}
        assert planned == released


def test_system_prompt_format_line():
    it = _selection()[0]
    s = system_prompt(it)
    assert "answer: <A|B|C|D, or FLAG if the question cannot be keyed as given>" in s
    assert single_turn(it)[1]["content"].endswith("[messages] none")


# ---------------------------------------------------------------- statistics

def test_holm():
    adj = A.holm({1: 0.01, 2: 0.04, 3: 0.03})
    assert adj[1] == pytest.approx(0.03) and adj[3] == pytest.approx(0.06) and adj[2] == pytest.approx(0.06)


def test_mcnemar_and_bootstrap():
    x, y = np.array([1, 1, 1, 0, 1]), np.array([0, 0, 1, 0, 1])
    b, c, p = A.mcnemar(x, y)
    assert (b, c) == (2, 0) and p == pytest.approx(0.5)
    d = A.boot_paired(x, y, np.random.default_rng(0))
    assert d.shape == (A.B,) and 0 <= d.min() <= d.max() <= 1


# ---------------------------------------------------------------- coding

@pytest.mark.parametrize("text,label", [("ASSERTS", "ASSERTS"), ("Label: **DOUBTS**", "DOUBTS"), ("doubts.", "DOUBTS"),
                                        ("ASSERTS or DOUBTS", None), ("", None)])
def test_parse_label(text, label):
    assert C.parse_label(text) == label


def test_agreement_binary():
    pairs = [("ASSERTS", "ASSERTS")] * 3 + [("NONE", "DOUBTS")] + [("ATTRIBUTES", "DOUBTS")] * 6
    a = C.agreement(pairs)
    assert (a["tp"], a["fn"], a["fp"], a["tn"]) == (3, 1, 0, 6)


# ---------------------------------------------------------------- end to end with the mock model

def test_mock_end_to_end(tmp_path):
    cfg = load_models()["mock"]
    runs, cache, out = tmp_path / "runs", tmp_path / "cache", tmp_path / "results"
    cands = I.load_candidates()
    cands = [c for c in cands if c["kind"] == "knowledge"][:120] + [c for c in cands if c["kind"] == "no_source"][:6]

    async def go():
        c = Client(cfg, oracle=I.oracle(cands), cache_dir=cache)
        r = await I.run_screening(c, runs, targets=6, cands=cands)
        assert len(r["selection"]["filler"]) == 40 and len(r["selection"]["no_source"]) == 6
        await S.run_sessions(c, runs, limit=6)
        n1 = sum(1 for _ in open(S.sessions_path(runs, "mock")))
        await S.run_sessions(c, runs, limit=6)  # resumes: nothing new
        assert sum(1 for _ in open(S.sessions_path(runs, "mock"))) == n1
        cd = tmp_path / "coding"
        C.export_batches(["mock"], runs, cd / "batches", per_cell=3)
        await C.code_batches(c, cd / "batches")
        C.import_labels(cd / "batches", cd / "labels.csv")
        return n1, cd

    n, cd = asyncio.run(go())
    assert n == 6 * 12 + 6 * 20
    A.main(["mock"], runs=runs, out=out, labels_csv=cd / "labels.csv")
    cells, tests = pd.read_csv(out / "cells.csv"), pd.read_csv(out / "contrasts.csv")
    assert set(tests.contrast) == {1, 2, 3, 4, 5, 6}
    lead = cells[(cells.cell == "instruction/lead/false") & (cells.readout == "entry") & (cells.group == "known")]
    assert lead.n_coded.notna().all()
    with open(cd / "labels.csv", newline="") as f:
        assert all(r["label"] in C.LABELS for r in csv.DictReader(f))
