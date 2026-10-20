"""Лаба 8: спільні функції для soak_report.py і stress_report.py.

Читання CSV від Locust, розбиття за хвилинами, лінійний тренд, оформлення графіків.
"""

import csv
import re
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

# Палітра та сама, що в лабі 7 (loadtests/make_report.py).
SURFACE = "#fcfcfb"
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRID = "#e6e5e1"
BLUE_LIGHT = "#86b6ef"
BLUE = "#3987e5"
BLUE_DARK = "#1c5cab"
BLUE_DEEP = "#0d366b"
RED = "#c8321f"
ORANGE = "#e07b00"
GREEN = "#2a9d8f"
PURPLE = "#b4458b"

SERVICE_STYLE = {
    "web1": (BLUE, "-"),
    "web2": (BLUE_DARK, "--"),
    "web3": (BLUE_LIGHT, ":"),
    "db": (ORANGE, "-"),
    "redis": (PURPLE, "-"),
    "nginx": (GREEN, "-"),
}


def number(text):
    """Число з CSV або None, якщо там N/A чи порожньо."""
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def fmt(value, digits=0):
    """Українська типографіка: пробіл між тисячами, кома в десяткових."""
    if value is None:
        return "-"
    text = ("{:,.%df}" % digits).format(value)
    return text.replace(",", " ").replace(".", ",")


def read_history(path):
    """Рядки Aggregated з *_stats_history.csv (по одному на секунду)."""
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["Name"] != "Aggregated":
                continue
            rows.append(
                {
                    "ts": int(r["Timestamp"]),
                    "users": int(r["User Count"]),
                    "rps": number(r["Requests/s"]),
                    "fps": number(r["Failures/s"]),
                    "p50": number(r["50%"]),
                    "p95": number(r["95%"]),
                    "p99": number(r["99%"]),
                    "count": int(r["Total Request Count"]),
                    "fails": int(r["Total Failure Count"]),
                    "avg": number(r["Total Average Response Time"]) or 0.0,
                }
            )
    return rows


def read_stats(path):
    """Рядки з *_stats.csv за назвою ендпоінта (Aggregated теж там)."""
    with open(path, newline="", encoding="utf-8") as f:
        return {row["Name"]: row for row in csv.DictReader(f)}


def read_failures(path):
    if not Path(path).exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_resources(path, t0):
    """Читає CSV моніторингу; час у хвилинах від старту Locust."""
    columns = {}
    times = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            stamp = datetime.fromisoformat(row["timestamp"]).timestamp()
            times.append((stamp - t0) / 60.0)
            for key, value in row.items():
                if key in ("timestamp",):
                    continue
                try:
                    columns.setdefault(key, []).append(float(value))
                except ValueError:
                    columns.setdefault(key, []).append(None)
    return times, columns


def cut(times, columns, end_min):
    """Лишає виміри між стартом і завершенням навантаження."""
    keep = [i for i, t in enumerate(times) if 0 <= t <= end_min + 0.5]
    return (
        [times[i] for i in keep],
        {k: [v[i] for i in keep] for k, v in columns.items()},
    )


def mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def percentile(values, q):
    values = [v for v in values if v is not None]
    return float(np.percentile(values, q)) if values else None


def per_minute(rows, t0, min_samples=30):
    """Зводить посекундну історію Locust у рядки по хвилинах.

    avg рахується за хвилину з накопичувальних лічильників (Locust пише середнє
    за весь час), а p50/p95/p99 це середнє значень «за останні 10 с» у цій хвилині
    (потрібен прогін з --csv-full-history).
    Остання неповна хвилина відкидається.
    """
    buckets = {}
    for r in rows:
        minute = (r["ts"] - t0) // 60
        if minute >= 0:
            buckets.setdefault(minute, []).append(r)
    out = []
    prev_count, prev_sum, prev_fails = 0, 0.0, 0
    for minute in sorted(buckets):
        bucket = buckets[minute]
        last = bucket[-1]
        if len(bucket) < min_samples:
            break
        dn = last["count"] - prev_count
        dfail = last["fails"] - prev_fails
        total_sum = last["avg"] * last["count"]
        out.append(
            {
                "minute": minute + 0.5,
                "users": last["users"],
                "rps": mean([r["rps"] for r in bucket]) or 0.0,
                "requests": dn,
                "failures": dfail,
                "error_pct": 100.0 * dfail / dn if dn > 0 else 0.0,
                "avg": (total_sum - prev_sum) / dn if dn > 0 else None,
                "p50": mean([r["p50"] for r in bucket if r["p50"]]),
                "p95": mean([r["p95"] for r in bucket if r["p95"]]),
                "p99": mean([r["p99"] for r in bucket if r["p99"]]),
            }
        )
        prev_count, prev_sum, prev_fails = last["count"], total_sum, last["fails"]
    return out


def trend(xs, ys):
    """Лінійна регресія. Повертає (нахил на одиницю x, R²)."""
    pairs = [(x, y) for x, y in zip(xs, ys) if y is not None]
    if len(pairs) < 3:
        return 0.0, 0.0
    x = np.array([p[0] for p in pairs], dtype=float)
    y = np.array([p[1] for p in pairs], dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    predicted = slope * x + intercept
    ss_res = float(np.sum((y - predicted) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return float(slope), r2


def window_means(xs, ys, start, end):
    return mean([y for x, y in zip(xs, ys) if start <= x < end])


def style_axes(ax):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=TEXT_SECONDARY, labelsize=9, length=0)
    ax.grid(axis="y", color=GRID, linewidth=1, linestyle="-")
    ax.set_axisbelow(True)


def new_figure(rows=1, cols=1, height=4.6, width=8.6, sharex=False):
    plt.rcParams["font.family"] = "DejaVu Sans"
    fig, axes = plt.subplots(
        rows, cols, figsize=(width, height), dpi=160, sharex=sharex, squeeze=False
    )
    fig.patch.set_facecolor(SURFACE)
    for ax in axes.flat:
        style_axes(ax)
    return fig, axes


def finish(fig, ax_for_legend, out_path, title=None, legend=True, loc="best"):
    if title:
        fig.suptitle(title, x=0.01, ha="left", color=TEXT_PRIMARY, fontsize=12)
    if legend and ax_for_legend is not None:
        handles, labels = ax_for_legend.get_legend_handles_labels()
        if handles:
            lg = ax_for_legend.legend(frameon=False, fontsize=9, loc=loc)
            for text in lg.get_texts():
                text.set_color(TEXT_SECONDARY)
    fig.tight_layout()
    fig.savefig(out_path, facecolor=SURFACE)
    plt.close(fig)


def label_axis(ax, xlabel=None, ylabel=None):
    if xlabel:
        ax.set_xlabel(xlabel, color=TEXT_SECONDARY, fontsize=10)
    if ylabel:
        ax.set_ylabel(ylabel, color=TEXT_SECONDARY, fontsize=10)


def update_readme_block(readme_path, marker, content):
    """Замінює текст між <!-- MARKER:START --> і <!-- MARKER:END -->."""
    path = Path(readme_path)
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"(<!-- %s:START -->\n).*?(\n<!-- %s:END -->)" % (marker, marker), re.S
    )
    if not pattern.search(text):
        raise SystemExit(
            "У %s немає маркерів %s:START / %s:END" % (path, marker, marker)
        )
    path.write_text(
        pattern.sub(lambda m: m.group(1) + content.rstrip("\n") + m.group(2), text),
        encoding="utf-8",
    )
