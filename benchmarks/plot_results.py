"""Render acceptance data; requires matplotlib only for this optional report."""
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

root = Path(__file__).resolve().parent
samples = json.loads((root / "lifecycle-after.json").read_text())["samples"]
rounds = [sample["created"] for sample in samples]
figure, axes = plt.subplots(2, 1, figsize=(8, 6), sharex=True, layout="constrained")
axes[0].plot(rounds, [sample["private_bytes"] / 2**20 for sample in samples], marker="o")
axes[0].set_ylabel("Process private memory (MiB)")
axes[0].set_title("Page create/close: collection every 100 rounds")
axes[1].plot(rounds, [sample["python_bytes_delta"] / 1024 for sample in samples],
             marker="o", color="tab:green")
axes[1].set_ylabel("Python allocation delta (KiB)")
axes[1].set_xlabel("Completed page lifecycles")
for axis in axes:
    axis.grid(alpha=0.25)
figure.savefig(root / "lifecycle-after.png", dpi=160)
figure.savefig(root / "lifecycle-after.svg")
