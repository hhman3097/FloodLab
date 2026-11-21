import csv
import json
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from matplotlib.colors import ListedColormap
from matplotlib.patches import Patch

from floodlab import DATA, METHODS, OUT, ROOT, dump, read_image, read_label, read_split


DEST = ROOT / "report" / "figures"
plt.rcParams.update({"font.family": "sans-serif", "font.sans-serif": ["Arial", "DejaVu Sans"],
                     "font.size": 8, "pdf.fonttype": 42,
                     "svg.fonttype": "none", "axes.spines.top": False, "axes.spines.right": False})
COLORS = {"otsu": "#787f88", "fcn": "#006c79"}
LABELS = {"otsu": "VH Otsu", "fcn": "FCN"}


def export(fig, stem):
    fig.canvas.draw()
    if os.getenv("FIGURE_ALIGNMENT_AUDIT"):
        from audit_panel_alignment import require_matplotlib_panel_alignment
        require_matplotlib_panel_alignment(fig, json_out=DEST / f"{stem}.alignment.json",
                                           strict=True)
    fig.savefig(DEST / f"{stem}.pdf", dpi=300)
    fig.savefig(DEST / f"{stem}.svg", dpi=300)
    fig.savefig(DEST / f"{stem}.png", dpi=300)
    plt.close(fig)


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    with (OUT / "test" / "per_chip.csv").open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    metrics = {(r["chip"], r["method"]): r for r in rows}
    stats = json.loads((OUT / "test" / "summary.json").read_text())
    events = sorted({row["event"] for row in stats["groups"]} - {"all"})
    groups = {(r["event"], r["method"]): r for r in stats["groups"]}
    events.sort(key=lambda e: groups[e, "fcn"]["iou"] - groups[e, "otsu"]["iou"])
    fig, ax = plt.subplots(figsize=(6.25, 3.4))
    fig.subplots_adjust(left=.22, right=.96, bottom=.16, top=.88)
    for index, event in enumerate(events):
        a, b = [groups[event, m]["iou"] for m in METHODS]
        ax.plot((a, b), (index, index), linewidth=1.2, color="#c8ced0")
    for method in METHODS:
        ax.scatter([groups[e, method]["iou"] for e in events], range(len(events)), s=22,
                   color=COLORS[method], label=LABELS[method], zorder=3)
    ax.set_yticks(range(len(events)), [f"{e} ({groups[e, 'fcn']['evaluated_chips']})" for e in events])
    ax.set_xticks(np.linspace(0, 1, 6))
    ax.set_xlim(0, 1)
    ax.set_xlabel("Water IoU (pooled within event)")
    ax.grid(axis="x", color="#e3e7e8")
    ax.set_axisbelow(True)
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.01), ncol=2, frameon=False)
    export(fig, "event-iou")

    chips = [k for k, _, _ in read_split("test") if metrics[k, "fcn"]["iou"] != ""
             and metrics[k, "otsu"]["iou"] != ""]
    delta = lambda k: float(metrics[k, "fcn"]["iou"]) - float(metrics[k, "otsu"]["iou"])
    examples = [max(chips, key=delta), min(chips, key=delta)]
    sources = {k: (i, l) for k, i, l in read_split("test")}
    fig, ax = plt.subplots(2, 4, figsize=(6.25, 3.65))
    fig.subplots_adjust(left=.035, right=.985, top=.92, bottom=.15, wspace=.10, hspace=.42)
    mask_cmap = ListedColormap(["#ecedec", "#178298", "#c2b59b"])
    error_cmap = ListedColormap(["#ecedec", "#178298", "#d4773c", "#613a77", "#c2b59b"])
    for row, key in enumerate(examples):
        img, valid, profile = read_image(DATA / sources[key][0])
        label, label_valid = read_label(DATA / sources[key][1], profile)
        joint = valid & label_valid
        ax[row, 0].imshow(np.ma.masked_where(~valid, img[1]), vmin=-32, vmax=-12,
                          cmap="gray", interpolation="nearest")
        ax[row, 1].imshow(np.where(joint, label, 2), cmap=mask_cmap, vmin=0, vmax=2,
                          interpolation="nearest")
        for col, m in enumerate(METHODS, 2):
            with rasterio.open(ROOT / metrics[key, m]["mask"]) as ds:
                predicted = ds.read(1) == 1
            water = label == 1
            codes = np.where(joint, np.where(predicted, np.where(water, 1, 2),
                                             np.where(water, 3, 0)), 4)
            ax[row, col].imshow(codes, cmap=error_cmap, vmin=0, vmax=4, interpolation="nearest")
        names = [f"{key}\nVH backscatter", "Reference\nwater mask",
                 f"VH Otsu\nIoU {float(metrics[key, 'otsu']['iou']):.3f}",
                 f"FCN\nIoU {float(metrics[key, 'fcn']['iou']):.3f}"]
        for col, panel in enumerate(ax[row]):
            panel.set_axis_off()
            panel.set_title(names[col], fontsize=7.5, loc="left", pad=4)
    fig.legend([Patch(facecolor=c) for c in error_cmap.colors],
               ["Non-water", "Water / TP", "False positive", "False negative", "No reference"],
               loc="lower center", bbox_to_anchor=(.5, .025), ncol=5, frameon=False, fontsize=7)
    export(fig, "cases")
    dump(DEST / "case-selection.json", {
        "rule": "Maximum and minimum of FCN minus VH Otsu water IoU over chips with both defined",
        "candidate_chips": len(chips),
        "fcn_wins": sum(delta(k) > 0 for k in chips),
        "fcn_losses": sum(delta(k) < 0 for k in chips),
        "ties": sum(delta(k) == 0 for k in chips),
        "examples": [{m: metrics[k, m] for m in METHODS} for k in examples],
        "display": "Full 512 x 512 chips; uniform VH stretch [-32,-12] dB; nearest display",
    })

    old = json.loads((OUT / "agent" / "legacy-transfer.json").read_text())["records"]
    new = json.loads((OUT / "agent" / "benchmark.json").read_text())["records"]
    new = [r for r in new if r["id"].startswith("B")]
    assert [r["id"] for r in old] == [r["id"] for r in new]
    array = [[int(r["passed"]) for r in group] for group in (old, new)]
    fig, ax = plt.subplots(figsize=(6.25, 1.55))
    fig.subplots_adjust(left=.205, right=.975, bottom=.32, top=.81)
    ax.imshow(array, cmap=ListedColormap(["#d2d4d7", "#006c79"]), vmin=0, vmax=1,
              aspect="auto", interpolation="nearest")
    ax.set_yticks([0, 1], [f"Loop ({sum(array[0])}/14)", f"Action ({sum(array[1])}/14)"])
    ax.set_xticks(range(len(new)), [r["id"] for r in new], fontsize=7)
    ax.tick_params(length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.legend(handles=[Patch(color="#006c79", label="Pass"), Patch(color="#d2d4d7", label="Fail")],
              loc="lower left", bbox_to_anchor=(-.02, 1.0), frameon=False, ncol=2)
    export(fig, "agent-tasks")


if __name__ == "__main__":
    main()
