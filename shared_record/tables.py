"""LaTeX table bodies of the paper, from results/cells.csv and results/contrasts.csv (run `analyze`
first) and the hand-coding agreement.

    python -m shared_record tables      # writes results/tables/*.tex

tab_tests.tex     Table "The six contrasts"
tab_follow.tex    Table "Share of sessions whose entry and private note follow a false instruction"
tab_truth.tex     Table "No source targets, instructions" (true vs false)
tab_variants.tex  Table "Variant cells"
tab_coding.tex    Table "Comment coding by cell" (lead's false instruction)
tab_agree.tex     Table "LLM coders against hand coding"
app_cells_<model>.tex   Appendix "All cells", one per model
"""
from __future__ import annotations

import csv
from pathlib import Path

from .analysis import OUT
from .coding import HANDCODING_CSV, LABELS_CSV, compute_agreement

MODELS = [("gemma-27b", "Gemma 27B"), ("qwen-9b", "Qwen 9B"), ("luna", "Luna")]
GROUPS = [("known", r"\known"), ("no_source", r"\nosrc")]
TIMINGS = [("concurrent", "concurrent"), ("one_before", "one before")]
DESC = {
    1: r"\known, concurrent, false instruction, entry: lead vs.\ peer",
    2: r"\known, concurrent, lead's false instruction: entry vs.\ private note",
    3: r"\known, lead's false instruction, entry: concurrent vs.\ one before",
    4: r"\nosrc, lead's false instruction, entry: concurrent vs.\ one before",
    5: r"Interaction: contrast 3 minus contrast 4 (A, B: the two drops, in points)",
    6: r"\nosrc, one before, false instruction, entry: lead vs.\ peer",
}
ORDER = {
    "known": ["control", "instruction/lead/false", "instruction/peer/false", "opinion/lead/false",
              "opinion/peer/false", "owner", "no_line"],
    "no_source": ["control", "instruction/lead/true", "instruction/lead/false", "instruction/peer/true",
                  "instruction/peer/false", "opinion/lead/true", "opinion/lead/false", "opinion/peer/true",
                  "opinion/peer/false", "owner", "no_line"],
}
NAME = {"owner": "owner (lead, instr., false)", "no_line": "no line (lead, instr., false)", "control": "control"}


def num(s):
    return None if s in ("", None) else float(s)


def pct(s, d=1):
    v = num(s)
    return "--" if v is None else f"{v * 100:.{d}f}"


def pts(s, d=1):
    v = num(s)
    if v is None:
        return "--"
    v = round(v, d) + 0.0
    return f"$+${v:.{d}f}" if v > 0 else (f"$-${-v:.{d}f}" if v < 0 else f"{0:.{d}f}")


def plain(v, d=1):
    v = round(v, d) + 0.0
    return f"$-${-v:.{d}f}" if v < 0 else f"{v:.{d}f}"


def cname(c):
    if c in NAME:
        return NAME[c]
    wording, s, truth = c.split("/")
    return f"{wording}, {s}, {truth}"


def main(results: Path = OUT, hand_csv: Path = HANDCODING_CSV, labels_csv: Path = LABELS_CSV):
    gen = results / "tables"
    gen.mkdir(parents=True, exist_ok=True)
    cells, tests = {}, {}
    with open(results / "cells.csv", newline="") as f:
        for r in csv.DictReader(f):
            cells[(r["model"], r["group"], r["cell"], r["timing"], r["readout"])] = r
    with open(results / "contrasts.csv", newline="") as f:
        for r in csv.DictReader(f):
            tests[(r["model"], int(r["contrast"]))] = r
    cell = lambda m, g, c, t, ro="entry": cells[(m, g, c, t, ro)]

    def write(name, lines):
        (gen / name).write_text("\n".join(lines) + "\n")

    out = []
    for k in range(1, 7):
        for i, (m, mname) in enumerate(MODELS):
            r = tests[(m, k)]
            lo, hi, p = float(r["ci_low"]), float(r["ci_high"]), float(r["p_holm"])
            sup = "yes" if (p < 0.05 and (lo > 0 or hi < 0)) else "no"
            ptxt = "$<$0.001" if p < 0.001 else f"{p:.3f}"
            head = (rf"\multirow{{3}}{{*}}{{{k}}} & \multirow{{3}}{{*}}{{\parbox{{4.1cm}}{{{DESC[k]}}}}}"
                    if i == 0 else " &")
            n = r["n"].replace(" + ", r"\,+\,")
            out.append(f"{head} & {mname} & {n} & {plain(float(r['share_a']) * 100)} & "
                       f"{plain(float(r['share_b']) * 100)} & {pts(r['diff_pts'])} & [{plain(lo)}, {plain(hi)}] & "
                       f"{ptxt} & {sup} \\\\")
        if k < 6:
            out.append(r"\midrule")
    write("tab_tests.tex", out)

    out = []
    for gi, (g, gname) in enumerate(GROUPS):
        first = True
        for s in ("lead", "peer"):
            for t, tname in TIMINGS:
                c = f"instruction/{s}/false"
                vals = [v for m, _ in MODELS for v in (pct(cell(m, g, c, t)["follows"]),
                                                      pct(cell(m, g, c, t, "private note")["follows"]))]
                out.append(f"{gname if first else ''} & {s} & {tname} & " + " & ".join(vals) + r" \\")
                first = False
        vals = [v for m, _ in MODELS for v in (pct(cell(m, g, "instruction/lead/false", "concurrent")["ctrl"]),
                                              pct(cell(m, g, "instruction/lead/false", "concurrent", "private note")["ctrl"]))]
        out.append(r" & \multicolumn{2}{l}{control (same option, no claim)} & " + " & ".join(vals) + r" \\")
        if gi == 0:
            out.append(r"\midrule")
    write("tab_follow.tex", out)

    out = []
    for s in ("lead", "peer"):
        for t, tname in (("concurrent", "conc."), ("one_before", "before")):
            for truth in ("true", "false"):
                vals = [v for m, _ in MODELS for v in (pct(cell(m, "no_source", f"instruction/{s}/{truth}", t)["follows"]),
                                                      pts(cell(m, "no_source", f"instruction/{s}/{truth}", t)["delta_key"]))]
                out.append(f"{f'{s}, {tname}' if truth == 'true' else ''} & {truth} & " + " & ".join(vals) + r" \\")
    write("tab_truth.tex", out)

    out = []
    for gi, (g, gname) in enumerate(GROUPS):
        rows = [("concurrent", "instruction/lead/false", "concurrent"), (r"\quad + owner line", "owner", "concurrent"),
                ("one before", "instruction/lead/false", "one_before"), (r"\quad + no line", "no_line", "one_before")]
        for i, (lab, c, t) in enumerate(rows):
            out.append(f"{gname if i == 0 else ''} & {lab} & " + " & ".join(pct(cell(m, g, c, t)["follows"])
                                                                            for m, _ in MODELS) + r" \\")
        if gi == 0:
            out.append(r"\midrule")
    write("tab_variants.tex", out)

    out = []
    for mi, (m, mname) in enumerate(MODELS):
        first = True
        for g, gname in GROUPS:
            for ti, (t, tname) in enumerate(TIMINGS):
                r = cell(m, g, "instruction/lead/false", t)
                coded = num(r["n_coded"])
                rest = (["--"] * 4 if not coded else
                        [f"{int(coded)}", pct(r["flagged"]), pct(r["silent"]), pct(r["silent_false_rate"])])
                out.append(f"{mname if first else ''} & {gname if ti == 0 else ''} & {tname} & "
                           f"{int(float(r['n_following']))} & " + " & ".join(rest) + r" \\")
                first = False
        if mi < len(MODELS) - 1:
            out.append(r"\midrule")
    write("tab_coding.tex", out)

    if hand_csv.exists() and labels_csv.exists():
        a = compute_agreement(hand_csv, labels_csv)
        write("tab_agree.tex", [rf"Hand: silent & {a['tp']} (TP) & {a['fn']} (FN) \\",
                                rf"Hand: flagged & {a['fp']} (FP) & {a['tn']} (TN) \\"])

    for m, _ in MODELS:
        out = []
        for gi, (g, gname) in enumerate(GROUPS):
            first = True
            for t, tname in TIMINGS:
                for c in ORDER[g]:
                    if (m, g, c, t, "entry") not in cells:
                        continue
                    vals = []
                    for r in (cell(m, g, c, t), cell(m, g, c, t, "private note")):
                        vals += (["--", "--", pct(r["flag"]), pct(r["key"]), "--"] if c == "control" else
                                 [pct(r["follows"]), pct(r["ctrl"]), pct(r["flag"]), pct(r["key"]), pts(r["delta_key"])])
                    out.append(f"{gname if first else ''} & {tname} & {cname(c)} & " + " & ".join(vals) + r" \\")
                    first = False
            if gi == 0:
                out.append(r"\midrule")
        out.append(r"\bottomrule")
        write(f"app_cells_{m}.tex", out)
    print(f"wrote {gen}")
