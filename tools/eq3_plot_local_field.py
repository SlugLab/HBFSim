"""Plot an already-derived EQ3 local-field diagnostic without reading raw fields."""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def matrix(rows, value, x_key, y_key):
    xs = sorted({row[x_key][0] for row in rows})
    ys = sorted({row[y_key][1] for row in rows})
    xmap = {x: index for index, x in enumerate(xs)}
    ymap = {y: index for index, y in enumerate(ys)}
    array = np.full((len(ys), len(xs)), np.nan)
    for row in rows:
        array[ymap[row[y_key][1]], xmap[row[x_key][0]]] = row[value]
    return np.asarray(xs) * 1e3, np.asarray(ys) * 1e3, array


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("diagnostic", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    data = json.loads(args.diagnostic.read_text())
    coarse_rows = data["coarse_distribution"]
    fine_rows = data["fine_distribution"]
    coarse_x, coarse_y, coarse = matrix(
        coarse_rows, "coarse_temperature_k", "coarse_center_m", "coarse_center_m")
    fine_x, fine_y, fine = matrix(
        fine_rows, "temperature_k", "center_m", "center_m")
    _, _, difference = matrix(
        coarse_rows, "coarse_minus_coarsened_fine_k",
        "coarse_center_m", "coarse_center_m")
    minimum = min(float(np.nanmin(coarse)), float(np.nanmin(fine)))
    maximum = max(float(np.nanmax(coarse)), float(np.nanmax(fine)))
    extent = [16, 28, 0, 16]
    figure, axes = plt.subplots(1, 3, figsize=(11.8, 3.65), constrained_layout=True)
    images = [
        axes[0].imshow(coarse, origin="lower", extent=extent, aspect="equal",
                       interpolation="nearest", vmin=minimum, vmax=maximum,
                       cmap="inferno"),
        axes[1].imshow(fine, origin="lower", extent=extent, aspect="equal",
                       interpolation="nearest", vmin=minimum, vmax=maximum,
                       cmap="inferno"),
    ]
    delta = max(abs(float(np.nanmin(difference))), abs(float(np.nanmax(difference))))
    images.append(axes[2].imshow(difference, origin="lower", extent=extent,
                                 aspect="equal", interpolation="nearest",
                                 vmin=-delta, vmax=delta, cmap="coolwarm"))
    axes[0].set_title("2 mm field")
    axes[1].set_title("1 mm field")
    axes[2].set_title("2 mm − averaged 1 mm")
    for axis in axes:
        axis.set_xlabel("x (mm)")
        axis.set_ylabel("y (mm)")
        axis.plot([16, 28, 28, 16, 16], [0, 0, 16, 16, 0], color="cyan", lw=1)
    axes[0].plot(data["coarse"]["hotspot_center_m"][0] * 1e3,
                 data["coarse"]["hotspot_center_m"][1] * 1e3, "c+", ms=10, mew=1.5)
    axes[1].plot(data["fine"]["hotspot_center_m"][0] * 1e3,
                 data["fine"]["hotspot_center_m"][1] * 1e3, "c+", ms=10, mew=1.5)
    axes[1].annotate("active HBM2\ncorner", xy=(16, 16), xytext=(21, 14),
                     color="white", fontsize=8,
                     arrowprops={"arrowstyle": "->", "color": "white", "lw": 0.8})
    figure.colorbar(images[1], ax=axes[:2], label="temperature (K)", shrink=.82)
    figure.colorbar(images[2], ax=axes[2], label="difference (K)", shrink=.82)
    figure.suptitle("hbm3.base, z=1.205–1.255 mm, t=15 s")
    figure.savefig(args.output, dpi=180)
    plt.close(figure)


if __name__ == "__main__":
    main()
