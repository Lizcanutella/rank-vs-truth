"""Figure "Entries that follow a false instruction, by timing, group and sender" (timing.pdf).

    python -m shared_record figure      # reads results/cells.csv, writes results/figures/timing.pdf and .png

Error bars: 95% Wilson score intervals on the share of the sessions of each cell.
"""
from __future__ import annotations

import csv
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

from .analysis import OUT  # noqa: E402

MODELS = [("gemma-27b", "Gemma 3 27B"), ("qwen-9b", "Qwen 3.5 9B"), ("luna", "GPT-6 Luna")]
LEAD, PEER, INK, GRID = "#7E22CE", "#D97706", "#1E293B", "#CBD5E1"


def wilson(p, n, z=1.959964):
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def main(results: Path = OUT):
    rows = {}
    with open(results / "cells.csv", newline="") as f:
        for r in csv.DictReader(f):
            if r["readout"] == "entry" and r["cell"].startswith("instruction/") and r["truth"] == "false":
                rows[(r["model"], r["group"], r["sender"], r["timing"])] = (float(r["follows"]), int(r["n"]))
    plt.rcParams.update({"font.family": "serif", "font.serif": ["Times New Roman", "Times", "Nimbus Roman", "DejaVu Serif"],
                         "font.size": 8, "axes.edgecolor": INK, "axes.labelcolor": INK, "xtick.color": INK,
                         "ytick.color": INK, "text.color": INK, "pdf.fonttype": 42})
    fig, axes = plt.subplots(1, 3, figsize=(3.35, 2.25), sharey=True)
    X = [0, 1]
    for ax, (m, title) in zip(axes, MODELS):
        for g, ls, filled in (("known", "-", True), ("no_source", (0, (3, 2)), False)):
            for s, col, mk, dx in (("lead", LEAD, "o", -0.04), ("peer", PEER, "s", 0.04)):
                ps = [rows[(m, g, s, t)] for t in ("concurrent", "one_before")]
                y = [100 * p for p, _ in ps]
                lo = [max(0.0, 100 * (p - wilson(p, n)[0])) for p, n in ps]
                hi = [max(0.0, 100 * (wilson(p, n)[1] - p)) for p, n in ps]
                ax.errorbar([x + dx for x in X], y, yerr=[lo, hi], color=col, ls=ls, lw=1.3, marker=mk, ms=4.2,
                            mfc=col if filled else "white", mec=col, mew=1.0, capsize=1.8, elinewidth=0.8, zorder=3)
        ax.set_title(title, fontsize=8, pad=3)
        ax.set_xticks(X)
        ax.set_xticklabels(["concur-\nrent", "one\nbefore"], fontsize=7)
        ax.set_xlim(-0.35, 1.35)
        ax.set_ylim(-3, 103)
        ax.set_yticks([0, 25, 50, 75, 100])
        ax.grid(axis="y", color=GRID, lw=0.5, zorder=0)
        ax.tick_params(length=2, width=0.5)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        for sp in ("left", "bottom"):
            ax.spines[sp].set_linewidth(0.5)
    axes[0].set_ylabel("Entries that follow (%)")
    handles = [Line2D([], [], color=LEAD, marker="o", ms=4, lw=1.3, label="lead"),
               Line2D([], [], color=PEER, marker="s", ms=4, lw=1.3, label="peer"),
               Line2D([], [], color=INK, marker="o", ms=4, lw=1.3, ls="-", label="Known"),
               Line2D([], [], color=INK, marker="o", ms=4, lw=1.3, ls=(0, (3, 2)), mfc="white", label="No source")]
    fig.legend(handles=handles, loc="lower center", ncol=4, frameon=False, fontsize=7, handlelength=2.4,
               columnspacing=1.0, bbox_to_anchor=(0.5, -0.01))
    fig.subplots_adjust(left=0.135, right=0.995, top=0.92, bottom=0.30, wspace=0.12)
    d = results / "figures"
    d.mkdir(parents=True, exist_ok=True)
    fig.savefig(d / "timing.pdf")
    fig.savefig(d / "timing.png", dpi=250)
    print(f"wrote {d / 'timing.pdf'}")
