"""Render the public Winter portfolio figures from already-recorded evidence.

This script does not run a model, service, or evaluation. The constants below
are transcribed from repository records cited in portfolio/EVIDENCE.md.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"

GREEN = "#03C75A"
GREEN_DARK = "#008C3A"
NAVY = "#17223B"
BLUE = "#3B82F6"
AMBER = "#F59E0B"
CORAL = "#E85D5D"
GRAY = "#5B6475"
LIGHT = "#F3F6F8"
PRIVATE_BG = "#EDF8F1"
PUBLIC_BG = "#EEF4FF"


plt.rcParams.update(
    {
        "font.family": "DejaVu Sans",
        "font.size": 15,
        "axes.titlesize": 21,
        "axes.labelsize": 15,
        "xtick.labelsize": 14,
        "ytick.labelsize": 14,
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "svg.fonttype": "none",
    }
)


def _save(fig: plt.Figure, stem: str) -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    png_metadata = {"Software": None, "Creator": "Winter portfolio asset generator"}
    svg_metadata = {"Creator": "Winter portfolio asset generator", "Date": "2026-09-19"}
    fig.savefig(ASSETS / f"{stem}.png", dpi=150, facecolor="white", metadata=png_metadata)
    fig.savefig(ASSETS / f"{stem}.svg", facecolor="white", metadata=svg_metadata)
    plt.close(fig)


def data_quality_fix() -> None:
    # docs/adr/0003-reference-transcription-strategy.md:84-112
    # docs/content-holdout-evaluation.md:58-76
    old_chars = 5_669
    corrected_chars = 3_547
    overlap_share = (old_chars - corrected_chars) / old_chars

    fig, axes = plt.subplots(1, 2, figsize=(16, 8.6), gridspec_kw={"wspace": 0.24})
    fig.subplots_adjust(top=0.81, bottom=0.18)
    fig.suptitle("Data quality before model changes", x=0.06, ha="left", y=0.98,
                 fontsize=28, fontweight="bold", color=NAVY)
    fig.text(0.06, 0.895, "Two input fixes changed what the evaluation was measuring.",
             fontsize=16, color=GRAY)

    ax = axes[0]
    bars = ax.bar(["Old parser", "Token-overlap fix"], [old_chars, corrected_chars],
                  color=[CORAL, GREEN], width=0.58)
    ax.set_title("A. Rolling-caption overlap", loc="left", fontweight="bold", color=NAVY)
    ax.set_ylabel("Caption text characters")
    ax.set_ylim(0, 6_500)
    ax.grid(axis="y", color="#D8DEE8", linewidth=0.8)
    ax.set_axisbelow(True)
    for bar, value in zip(bars, [old_chars, corrected_chars]):
        ax.text(bar.get_x() + bar.get_width() / 2, value + 140, f"{value:,}",
                ha="center", va="bottom", fontsize=18, fontweight="bold", color=NAVY)
    ax.annotate(
        f"{overlap_share:.1%} removed",
        xy=(1, corrected_chars), xytext=(0.28, 4_850),
        arrowprops={"arrowstyle": "->", "color": NAVY, "lw": 2},
        fontsize=18, fontweight="bold", color=NAVY,
    )
    ax.text(
        0.0, -0.20,
        "Definition: share of the old parser's counted characters\n"
        "identified as rolling overlap — not a dataset duplicate rate.",
        transform=ax.transAxes, fontsize=13.5, color=GRAY, va="top",
    )
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)

    ax = axes[1]
    ax.set_title("B. Question construction snapshot", loc="left", fontweight="bold", color=NAVY)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    rows = [
        ("Average question length", "6.67 words", "8.88 words", BLUE),
        ("Total pairs", "40", "42", GREEN),
        ("Held-out pairs", "20", "21", AMBER),
    ]
    ax.text(0.30, 0.82, "Before", ha="center", color=GRAY, fontweight="bold")
    ax.text(0.82, 0.82, "After", ha="center", color=GRAY, fontweight="bold")
    for idx, (label, before, after, color) in enumerate(rows):
        y = 0.65 - idx * 0.22
        ax.text(0.02, y + 0.08, label, color=NAVY, fontsize=15, fontweight="bold")
        ax.add_patch(FancyBboxPatch((0.16, y - 0.03), 0.28, 0.12,
                                   boxstyle="round,pad=0.02,rounding_size=0.02",
                                   fc=LIGHT, ec="#D6DCE5", lw=1.2))
        ax.add_patch(FancyBboxPatch((0.68, y - 0.03), 0.28, 0.12,
                                   boxstyle="round,pad=0.02,rounding_size=0.02",
                                   fc="white", ec=color, lw=2.2))
        ax.text(0.30, y + 0.03, before, ha="center", va="center", fontsize=19, color=NAVY)
        ax.text(0.82, y + 0.03, after, ha="center", va="center", fontsize=19,
                color=color, fontweight="bold")
        ax.add_patch(FancyArrowPatch((0.46, y + 0.03), (0.66, y + 0.03),
                                    arrowstyle="-|>", mutation_scale=18, lw=2, color=GRAY))
    ax.text(
        0.02, -0.04,
        "Pair count and held-out size changed at the same time.\n"
        "Do not read the length increase as an isolated causal effect.",
        fontsize=13.5, color=GRAY, va="top",
    )

    _save(fig, "01_data_quality_fix")


def _box(ax: plt.Axes, x: float, y: float, w: float, h: float, label: str,
         *, edge: str = GREEN_DARK, face: str = "white", number: int | None = None) -> None:
    ax.add_patch(FancyBboxPatch((x, y), w, h,
                               boxstyle="round,pad=0.008,rounding_size=0.012",
                               fc=face, ec=edge, lw=2.0))
    if number is not None:
        ax.text(x + 0.018, y + h - 0.023, f"{number:02d}", fontsize=11,
                color=edge, fontweight="bold", va="top")
    ax.text(x + w / 2, y + h / 2 - 0.005, label, ha="center", va="center",
            fontsize=14, color=NAVY, fontweight="bold", linespacing=1.2)


def _arrow(ax: plt.Axes, start: tuple[float, float], end: tuple[float, float],
           *, color: str = GRAY) -> None:
    ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=17,
                                lw=1.8, color=color, connectionstyle="arc3,rad=0"))


def winter_pipeline() -> None:
    fig, ax = plt.subplots(figsize=(18, 9.5))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.text(0.04, 0.965, "Winter data and evaluation evidence flow", fontsize=28,
            fontweight="bold", color=NAVY, va="top")
    ax.text(0.04, 0.91, "Raw dialogue/audio stays private; code and sanitized findings are public.",
            fontsize=16, color=GRAY, va="top")

    ax.add_patch(Rectangle((0.035, 0.36), 0.93, 0.48, fc=PRIVATE_BG, ec="#B9DEC7", lw=1.5))
    ax.text(0.055, 0.81, "PRIVATE SOURCE STORAGE", fontsize=14, color=GREEN_DARK,
            fontweight="bold", va="top")
    ax.add_patch(Rectangle((0.035, 0.07), 0.93, 0.20, fc=PUBLIC_BG, ec="#BCD0F7", lw=1.5))
    ax.text(0.055, 0.235, "PUBLIC REPOSITORY", fontsize=14, color=BLUE,
            fontweight="bold", va="top")

    row1 = [
        "Private\nRegistry", "Metadata\nPreflight", "Subtitle /\nAudio",
        "STT", "Sortformer\n(diarization)", "Pair\nConstruction",
    ]
    xs1 = [0.055, 0.205, 0.355, 0.505, 0.655, 0.805]
    for i, (x, label) in enumerate(zip(xs1, row1), start=1):
        _box(ax, x, 0.62, 0.12, 0.12, label, number=i)
        if i > 1:
            _arrow(ax, (xs1[i - 2] + 0.12, 0.68), (x, 0.68))

    _arrow(ax, (0.865, 0.615), (0.865, 0.565))
    row2 = [
        (0.805, "Exact\nDedup", 7),
        (0.625, "Short-gap\nBridge", 8),
        (0.445, "Time-ordered\nHeld-out", 9),
        (0.265, "LLM\nEvaluation", 10),
    ]
    for x, label, number in row2:
        _box(ax, x, 0.43, 0.12, 0.12, label, number=number)
    for (left_x, _, _), (right_x, _, _) in zip(row2[1:], row2[:-1]):
        _arrow(ax, (right_x, 0.49), (left_x + 0.12, 0.49))

    ax.text(0.50, 0.385, "Controlled boundary: private prompts in, aggregate evidence out",
            ha="center", va="center", fontsize=13, color=GRAY, fontweight="bold")
    _arrow(ax, (0.325, 0.425), (0.175, 0.245), color=BLUE)
    _box(ax, 0.095, 0.12, 0.16, 0.09, "Failure Analysis", edge=BLUE,
         face="white", number=11)
    ax.text(0.34, 0.16, "Schemas  •  validation code  •  sanitized metrics  •  decision records",
            fontsize=15, color=NAVY, va="center")
    ax.text(0.50, 0.025, "Public repo  ≠  private source storage", ha="center",
            fontsize=16, color=NAVY, fontweight="bold")

    _save(fig, "02_winter_pipeline")


def heldout_evolution() -> None:
    # docs/content-holdout-evaluation.md:31-76
    rounds = ["Round 1", "Round 2", "Round 3"]
    positions = [0.021, 0.105, 0.157]
    totals = [11, 40, 42]
    heldouts = [6, 20, 21]

    fig, ax = plt.subplots(figsize=(16, 8.5))
    fig.subplots_adjust(bottom=0.27, top=0.82, left=0.10, right=0.96)
    fig.suptitle("Held-out evolution: input quality before model replacement", x=0.07,
                 ha="left", y=0.96, fontsize=28, fontweight="bold", color=NAVY)
    fig.text(0.07, 0.90, "Position between the measured chance baseline and exact-match 1.0",
             fontsize=16, color=GRAY)

    x = list(range(3))
    ax.plot(x, positions, color=GREEN, linewidth=4, marker="o", markersize=13,
            markeredgewidth=2, markeredgecolor="white", zorder=3)
    ax.fill_between(x, positions, 0, color=GREEN, alpha=0.08)
    ax.set_xticks(x, rounds)
    ax.set_ylabel("Chance-relative position\n(0 = chance, 1 = exact match)")
    ax.set_ylim(0, 0.19)
    ax.set_yticks([0.00, 0.05, 0.10, 0.15])
    ax.grid(axis="y", color="#D8DEE8")
    ax.set_axisbelow(True)
    for idx, (value, total, heldout) in enumerate(zip(positions, totals, heldouts)):
        ax.text(idx, value + 0.010, f"{value:.3f}", ha="center", fontsize=20,
                fontweight="bold", color=NAVY)
        ax.text(idx, -0.15, f"Total {total}  /  Held-out {heldout}", ha="center",
                va="top", transform=ax.get_xaxis_transform(), fontsize=14, color=GRAY)
    ax.tick_params(axis="x", pad=10)
    ax.text(0.02, 0.94, "Input: 11 pairs", transform=ax.transAxes, color=GRAY, fontsize=13)
    ax.text(0.42, 0.94, "Expanded: 40 pairs", transform=ax.transAxes, color=GRAY, fontsize=13)
    ax.text(0.75, 0.94, "Bridge repair: 42 pairs", transform=ax.transAxes, color=GRAY, fontsize=13)
    ax.axhline(0, color=NAVY, linewidth=1.2)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    fig.text(
        0.07, 0.045,
        "Metric boundary: recorded-answer wording similarity, not response quality or accuracy.  "
        "Formula: (mean similarity − chance similarity) / (1 − chance similarity).",
        fontsize=13.5, color=GRAY,
    )

    _save(fig, "03_heldout_evolution")


def provenance_schema() -> None:
    fig, ax = plt.subplots(figsize=(16, 8.5))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.text(0.05, 0.95, "Provenance without publishing the source", fontsize=28,
            fontweight="bold", color=NAVY, va="top")
    ax.text(0.05, 0.89, "The repository exposes the contract; source values remain private.",
            fontsize=16, color=GRAY, va="top")

    ax.add_patch(FancyBboxPatch((0.05, 0.12), 0.48, 0.68,
                               boxstyle="round,pad=0.015,rounding_size=0.02",
                               fc=PRIVATE_BG, ec=GREEN_DARK, lw=2.2))
    ax.text(0.075, 0.755, "PRIVATE INGEST RECORD", fontsize=16, color=GREEN_DARK,
            fontweight="bold")
    fields = [
        "SHA-256", "Byte size", "Format / container", "Codec",
        "Sample rate", "Bitrate", "Relative path",
    ]
    for idx, field in enumerate(fields):
        y = 0.685 - idx * 0.073
        ax.add_patch(Rectangle((0.075, y - 0.035), 0.42, 0.056, fc="white",
                               ec="#C8D8CE", lw=0.8))
        ax.text(0.095, y - 0.006, field, fontsize=14, color=NAVY, va="center",
                fontweight="bold")
        ax.text(0.47, y - 0.006, "PRIVATE", fontsize=12, color=CORAL, va="center",
                ha="right", fontweight="bold")

    ax.text(0.58, 0.48, "≠", fontsize=52, color=NAVY, ha="center", va="center",
            fontweight="bold")

    ax.add_patch(FancyBboxPatch((0.63, 0.25), 0.32, 0.46,
                               boxstyle="round,pad=0.015,rounding_size=0.02",
                               fc=PUBLIC_BG, ec=BLUE, lw=2.2))
    ax.text(0.655, 0.665, "PUBLIC REPOSITORY", fontsize=16, color=BLUE,
            fontweight="bold")
    public_items = [
        ("Schema", "field names + types"),
        ("Validation", "bounds • path •\noverwrite checks"),
        ("Evidence", "counts • sanitized\nsummaries"),
    ]
    for idx, (title, detail) in enumerate(public_items):
        y = 0.56 - idx * 0.12
        ax.text(0.665, y, title, fontsize=15, color=NAVY, fontweight="bold", va="center")
        ax.text(0.80, y, detail, fontsize=12.5, color=GRAY, va="center")
    ax.text(0.79, 0.19, "No URI • no filename • no audio • no transcript",
            fontsize=14, color=CORAL, ha="center", fontweight="bold")
    ax.text(0.50, 0.055, "Public repo  ≠  private source storage", fontsize=17,
            color=NAVY, ha="center", fontweight="bold")

    _save(fig, "04_provenance")


def main() -> None:
    data_quality_fix()
    winter_pipeline()
    heldout_evolution()
    provenance_schema()


if __name__ == "__main__":
    main()
