"""Plot evidence-specific cropland and road screening directly from the case JSON."""
import argparse
import json
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties, fontManager
from matplotlib.patches import Patch
from matplotlib.ticker import MultipleLocator

ROOT = Path(__file__).resolve().parent
CATEGORIES = ("both", "sar_only", "optical_only", "unknown")
PALETTE = ("#187d70", "#c38149", "#625088", "#a4acb4")
SCOPES = ("study", "north")


def load():
    evidence = ROOT / "evidence/karla"
    index = json.loads((evidence / "data-index.json").read_text(encoding="utf-8"))
    return {o["scope"]: json.loads((evidence / o["folder"] / "results.json").read_text(encoding="utf-8"))
            for o in index["outputs"] if o["kind"] == "karla_exposure"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-dir", type=Path)
    args = parser.parse_args()
    data = load()
    cn = ROOT / "fonts/SourceHanSansCN-Regular.ttf"
    fontManager.addfont(cn)
    fontManager.addfont(ROOT / "fonts/SourceHanSansCN-Bold.ttf")
    mpl.rcParams.update({"font.family": "sans-serif",
                         "font.sans-serif": [FontProperties(fname=cn).get_name(), "Arial", "DejaVu Sans"],
                         "pdf.fonttype": 42,
                         "svg.fonttype": "none", "font.size": 8, "axes.unicode_minus": False})
    fig, axes = plt.subplots(1, 2, figsize=(6.22, 2.30))
    fig.subplots_adjust(left=.155, right=.975, bottom=.24, top=.745, wspace=.34)
    specs = (("cropland_km2", 1, 100, 20, "a  耕地重叠（km²）"),
             ("road_m", .001, 9, 2, "b  道路重叠（km）"))
    for ax, (field, unit, right, step, title) in zip(axes, specs):
        ax.set_title(title, loc="left", pad=11, weight="bold", fontsize=10, color="#21333e")
        for y, scope in enumerate(SCOPES):
            left = 0
            for name, color in zip(CATEGORIES, PALETTE):
                value = data[scope][field][name] * unit
                ax.barh(y, value, left=left, height=.47, color=color, linewidth=0)
                if name in ("both", "optical_only") and value / right >= .12:
                    ax.text(left + value / 2, y, f"{value:.2f}", ha="center", va="center",
                            color="white", fontsize=8, weight="bold")
                left += value
        ax.set(xlim=(0, right), ylim=(1.55, -.55), yticks=[0, 1], yticklabels=["全研究窗", "北部片区"])
        ax.xaxis.set_major_locator(MultipleLocator(step))
        ax.tick_params(axis="y", length=0, pad=6)
        ax.tick_params(axis="x", labelsize=8, colors="#465861", length=3)
        for name in ("top", "right", "left"):
            ax.spines[name].set_visible(False)
        ax.spines["bottom"].set_color("#98a3a8")
    fig.legend([Patch(facecolor=c) for c in PALETTE],
               ["两期均检出", "仅雷达", "仅光学", "两期不可比"],
               loc="lower center", bbox_to_anchor=(.52, .025), ncol=4, frameon=False,
               handlelength=1.2, columnspacing=1.45, fontsize=8)
    fig.canvas.draw()
    if args.audit_dir:
        from audit_panel_alignment import require_matplotlib_panel_alignment

        args.audit_dir.mkdir(parents=True, exist_ok=True)
        require_matplotlib_panel_alignment(fig, json_out=args.audit_dir / "karla-screening.alignment.json",
                                           tolerance_pt=1.5, gutter_tolerance_pt=1.5, strict=True)
    dest = ROOT / "figures/karla-screening"
    fig.savefig(dest.with_suffix(".pdf"))
    fig.savefig(dest.with_suffix(".svg"))
    fig.savefig(dest.with_suffix(".png"), dpi=300)


if __name__ == "__main__":
    main()
