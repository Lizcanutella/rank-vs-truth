"""Sessions of the main experiment (paper, "Session and timing", "Cells", Table 1).

A session is one conversation with three user turns, each answered by a model call:

    turn 1 filler | turn 2 filler | turn 3 target      + private note on a copy after turn 3

The private note is asked on a copy of the conversation and never enters the session. The two fillers
are Known questions of the same model, drawn per target from the 40 fillers with
random.Random(f"{SEED}-{target id}").sample(pool, 2) and identical across all cells of that target.

Timing: `concurrent` = the message is in turn 3's inbox; `one_before` = in turn 2's inbox (turn 3 then
reads `[messages] none`). Cells per target:
  known      control; instruction lead / peer false; opinion lead / peer false          x both timings
  no_source  control; instruction and opinion, lead / peer, true and false              x both timings
  both       owner    (lead's false instruction, concurrent, the owner line in the system prompt)
             no_line  (lead's false instruction, one_before, no `[messages] none` line in turns 1 and 3)
The control message comes from the lead and is placed at the same turn as the message it is compared
with. A true message names the keyed option; a false one names the selection's `false_option`.

Output: one row per session, appended as each completes, to data/runs/<model>/sessions.jsonl; a re-run
skips sessions whose `session_id` is already in the file.
"""
from __future__ import annotations

import asyncio
import json
import random
from dataclasses import dataclass
from pathlib import Path

from .client import Client, SpendingCapReached
from .items import RUNS, load_selection
from .parse import answer_text, parse_reply
from .prompts import CONTROL, INSTRUCTION, LETTERS, OPINION, PRIVATE_NOTE, message, sender_line, system_prompt, turn_block

SEED = 20261118
GROUPS = ("known", "no_source")
TIMINGS = ("concurrent", "one_before")
MESSAGE_TURN = {"concurrent": 3, "one_before": 2}
TARGET_TURN, N_TURNS, N_FILLERS = 3, 3, 2
VARIANT_CELLS = {"owner": "concurrent", "no_line": "one_before"}  # cell -> its only timing


@dataclass
class Cell:
    item: dict
    name: str                  # control | instruction/lead/false | ... | owner | no_line
    wording: str               # control | instruction | opinion
    sender: str                # lead | peer
    truth: str | None          # true | false (None for control)
    named_option: str | None   # the option letter the message names
    text: str                  # the message


def make_cell(t: dict, wording: str, sender: str = "lead", truth: str | None = None, name: str | None = None) -> Cell:
    if wording == "control":
        return Cell(t, "control", "control", "lead", None, None, CONTROL)
    X = t["false_option"] if truth == "false" else t["answer"]
    tpl = INSTRUCTION if wording == "instruction" else OPINION
    return Cell(t, name or f"{wording}/{sender}/{truth}", wording, sender, truth, X, message(tpl, t, X))


def target_cells(t: dict) -> list[tuple[Cell, str]]:
    """(cell, timing) of every session of target `t`, in run order."""
    truths = ("false",) if t["group"] == "known" else ("false", "true")
    base = [make_cell(t, "control")] + [make_cell(t, w, s, tr) for w in ("instruction", "opinion")
                                        for tr in truths for s in ("lead", "peer")]
    out = [(c, tm) for tm in TIMINGS for c in base]
    out += [(make_cell(t, "instruction", "lead", "false", name=v), tm) for v, tm in VARIANT_CELLS.items()]
    return out


@dataclass
class Session:
    cell: Cell
    timing: str
    fillers: list[dict]   # turns 1 and 2

    @property
    def target(self) -> dict:
        return self.cell.item

    @property
    def id(self) -> str:
        return f"{self.target['id']}|{self.cell.name}|{self.timing}|{','.join(f['id'] for f in self.fillers)}"

    def system(self) -> dict:
        return {"role": "system", "content": system_prompt(self.target, owner_line=self.cell.name == "owner")}

    def user(self, turn: int) -> dict:
        msgs = [(self.cell.sender, self.cell.text)] if turn == MESSAGE_TURN[self.timing] else []
        it = self.target if turn == TARGET_TURN else self.fillers[turn - 1]
        return {"role": "user", "content": turn_block(it, self.target, msgs, inbox_line=self.cell.name != "no_line")}

    def note_prompt(self) -> dict:
        return {"role": "user", "content": PRIVATE_NOTE.format(qid=self.target["qid"])}


def plan(selected: list[dict], limit: int | None = None) -> dict:
    by = {g: [s for s in selected if s["group"] == g] for g in ("known", "filler", "no_source")}
    targets = {g: by[g][:limit] for g in GROUPS}
    pool = by["filler"]
    if len(pool) < N_FILLERS:
        raise SystemExit(f"only {len(pool)} fillers in the selection (need at least {N_FILLERS})")
    fillers = {t["id"]: random.Random(f"{SEED}-{t['id']}").sample(pool, N_FILLERS)
               for g in GROUPS for t in targets[g]}
    return {"targets": targets, "pool": pool, "fillers": fillers}


def all_sessions(p: dict) -> list[Session]:
    return [Session(c, tm, p["fillers"][t["id"]]) for g in GROUPS for t in p["targets"][g] for c, tm in target_cells(t)]


class _Shared:
    """Identical requests in flight at the same time are sent once (cells of a target share turns 1-2)."""

    def __init__(self, client: Client):
        self.client, self.tasks = client, {}

    async def chat(self, messages: list[dict]) -> dict:
        k = self.client.key(messages)
        t = self.tasks.get(k)
        if t is None:
            t = self.tasks[k] = asyncio.ensure_future(self.client.chat(messages))
            t.add_done_callback(lambda _: self.tasks.pop(k, None))
        return await asyncio.shield(t)


async def run_session(sh: _Shared, s: Session) -> dict:
    t, c = s.target, s.cell
    hist = [s.system()]
    replies = []
    for k in range(1, N_TURNS + 1):
        hist = hist + [s.user(k)]
        o = await sh.chat(hist)
        replies.append(o["content"])
        hist = hist + [{"role": "assistant", "content": o["content"]}]
    note = await sh.chat(hist + [s.note_prompt()])
    opts = t["options"]
    e, n = parse_reply(replies[2]), parse_reply(note["content"])
    return {"model": sh.client.cfg["name"], "session_id": s.id, "group": t["group"], "target_id": t["id"],
            "qid": t["qid"], "cell": c.name, "wording": c.wording, "sender": c.sender, "truth": c.truth,
            "timing": s.timing, "message_turn": MESSAGE_TURN[s.timing], "sender_line": sender_line(t, c.sender),
            "message": c.text, "named_option": c.named_option,
            "named_text": opts[LETTERS.index(c.named_option)] if c.named_option else None,
            "key": t["answer"], "key_text": opts[LETTERS.index(t["answer"])], "question": t["question"],
            "options": opts, "filler_ids": [f["id"] for f in s.fillers], "filler_keys": [f["answer"] for f in s.fillers],
            "entry_reply": replies[2], "note_reply": note["content"], "filler_replies": replies[:2],
            "entry_answer": answer_text(opts, e["answer"]), "entry_confidence": e["confidence"],
            "note_answer": answer_text(opts, n["answer"])}


def done_ids(path: Path) -> tuple[set[str], bool]:
    """Session ids already in the output file, and whether the file lacks a final newline."""
    ids, last = set(), b"\n"
    if not path.exists():
        return ids, False
    with path.open("rb") as f:
        for raw in f:
            last = raw
            try:
                ids.add(json.loads(raw)["session_id"])
            except (ValueError, KeyError, TypeError):
                pass  # a line cut by a crash; that session is run again
    return ids, not last.endswith(b"\n")


def sessions_path(runs: Path, model: str) -> Path:
    return runs / model / "sessions.jsonl"


async def run_sessions(client: Client, runs: Path = RUNS, limit: int | None = None) -> Path:
    name = client.cfg["name"]
    p = plan(load_selection(runs, name), limit)
    ss = all_sessions(p)
    out = sessions_path(runs, name)
    out.parent.mkdir(parents=True, exist_ok=True)
    done, need_nl = done_ids(out)
    todo = [s for s in ss if s.id not in done]
    print(f"{name} sessions: targets " + ", ".join(f"{g} {len(p['targets'][g])}" for g in GROUPS)
          + f"; {len(ss)} sessions, {len(ss) - len(todo)} already in {out.name}", flush=True)
    sh = _Shared(client)
    gate = asyncio.Semaphore(2 * client.cfg["concurrency"])
    written = 0
    with out.open("a") as fh:
        if need_nl and todo:
            fh.write("\n")

        async def one(s: Session):
            nonlocal written
            async with gate:
                row = await run_session(sh, s)
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            written += 1

        res = await asyncio.gather(*(one(s) for s in todo), return_exceptions=True)
    errors = [r for r in res if isinstance(r, BaseException)]
    if errors:
        if all(isinstance(e, SpendingCapReached) for e in errors):
            print(f"{name} sessions: STOPPED, {errors[0]}. {client.stats()}. {written} sessions written; every "
                  f"fetched call is cached. Re-run with a higher --max-cost to continue.")
            raise SystemExit(2)
        raise errors[0]
    print(f"{name} sessions: {written} written, {len(ss)} of {len(ss)} complete; {client.stats()} -> {out}")
    return out
