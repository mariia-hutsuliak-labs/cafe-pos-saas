"""Лаба 7: порівняльні таблиці й графіки «до / після» за результатами Locust.

Читає results/lab7/<мітка>_u<N>_{stats,resources}.csv і пише:
  docs/lab7/rps.png, latency_avg.png, latency_p99.png, endpoints_300.png
  docs/lab7/tables.md  (таблиці для README)
  docs/lab7/explain.md (час запитів з explain_before/after.txt, якщо файли є)

Запуск (потрібен matplotlib):
  python loadtests/make_report.py
  python loadtests/make_report.py --results results/lab7 --out docs/lab7
"""

import argparse
import csv
import re
import statistics
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

USERS = [100, 300, 600, 1000]
DURATION_S = 120  # тривалість кожного прогону, секунд

# Етапи оптимізації: мітка файлів, підпис, колір (одна синя гама, від світлого до темного).
STAGES = [
    ("before_100k", "До оптимізації", "#86b6ef"),
    ("after_idx_100k", "+ індекси й SQL", "#3987e5"),
    ("after_pool_100k", "+ пул, N+1, вхід", "#1c5cab"),
    ("after_redis_100k", "+ кеш Redis", "#0d366b"),
]

SURFACE = "#fcfcfb"
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRID = "#e6e5e1"

ENDPOINTS = [
    "GET /analytics/summary",
    "GET /analytics/by-cafe",
    "GET /analytics/top-products",
    "GET /analytics/revenue-by-day",
    "GET /sales/",
    "POST /sales/",
]


def read_stats(results, label, users):
    path = results / ("%s_u%d_stats.csv" % (label, users))
    if not path.exists():
        return None
    with open(path, newline="", encoding="utf-8") as f:
        return {row["Name"]: row for row in csv.DictReader(f)}


def read_resources(results, label, users):
    path = results / ("%s_u%d_resources.csv" % (label, users))
    if not path.exists():
        return None
    out = {}
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for container in ("web1", "db"):
        cpu = [float(r["cpu_pct"]) for r in rows if r["container"] == container]
        ram = [parse_mib(r["mem_usage"]) for r in rows if r["container"] == container]
        out[container] = {
            "cpu_avg": statistics.mean(cpu),
            "cpu_max": max(cpu),
            "ram_avg": statistics.mean(ram),
            "ram_max": max(ram),
        }
    return out


def parse_mib(text):
    match = re.match(r"([\d.]+)\s*([KMG]i?B)", text)
    value, unit = float(match.group(1)), match.group(2)
    return value * {"KiB": 1 / 1024, "MiB": 1, "GiB": 1024}.get(unit, 1)


def metrics(row):
    requests = int(row["Request Count"])
    failures = int(row["Failure Count"])
    return {
        "requests": requests,
        "failures": failures,
        "error_pct": 100.0 * failures / requests if requests else 0.0,
        "rps": requests / DURATION_S,
        "avg": float(row["Average Response Time"]),
        "p50": float(row["50%"]),
        "p95": float(row["95%"]),
        "p99": float(row["99%"]),
    }


def collect(results):
    data = {}
    for label, _, _ in STAGES:
        for users in USERS:
            stats = read_stats(results, label, users)
            if stats is None:
                continue
            data[(label, users)] = {
                "agg": metrics(stats["Aggregated"]),
                "endpoints": {n: metrics(stats[n]) for n in ENDPOINTS if n in stats},
                "res": read_resources(results, label, users),
            }
    return data


def style_axes(ax):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=TEXT_SECONDARY, labelsize=9, length=0)
    ax.grid(axis="y", color=GRID, linewidth=1, linestyle="-")
    ax.set_axisbelow(True)


def thousands(value, _pos=None):
    return "{:,.0f}".format(value).replace(",", " ")


def line_chart(data, key, title, ylabel, out_path, log=False, ymin=None):
    fig, ax = plt.subplots(figsize=(8, 4.6), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    style_axes(ax)
    xs = list(range(len(USERS)))
    for label, name, color in STAGES:
        ys = [data[(label, u)]["agg"][key] for u in USERS if (label, u) in data]
        if len(ys) != len(USERS):
            continue
        ax.plot(
            xs,
            ys,
            color=color,
            linewidth=2,
            solid_capstyle="round",
            marker="o",
            markersize=8,
            markeredgecolor=SURFACE,
            markeredgewidth=2,
            label=name,
        )
    ax.set_xticks(xs)
    ax.set_xticklabels([str(u) for u in USERS])
    ax.set_xlabel("Віртуальних користувачів", color=TEXT_SECONDARY, fontsize=10)
    ax.set_ylabel(ylabel, color=TEXT_SECONDARY, fontsize=10)
    if log:
        ax.set_yscale("log")
    if ymin is not None:
        ax.set_ylim(bottom=ymin)
    ax.yaxis.set_major_formatter(FuncFormatter(thousands))
    ax.set_title(title, loc="left", color=TEXT_PRIMARY, fontsize=12, pad=14)
    legend = ax.legend(frameon=False, fontsize=9, loc="best")
    for text in legend.get_texts():
        text.set_color(TEXT_SECONDARY)
    fig.tight_layout()
    fig.savefig(out_path, facecolor=SURFACE)
    plt.close(fig)


def endpoints_chart(data, out_path, users=300):
    first, last = STAGES[0], STAGES[-1]
    if (first[0], users) not in data or (last[0], users) not in data:
        return
    fig, ax = plt.subplots(figsize=(8, 4.6), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    style_axes(ax)
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color=GRID, linewidth=1)
    height = 0.3
    gap = 0.04
    for i, name in enumerate(ENDPOINTS):
        before = data[(first[0], users)]["endpoints"].get(name)
        after = data[(last[0], users)]["endpoints"].get(name)
        if not before or not after:
            continue
        for offset, stage, row in (
            (-(height + gap) / 2, first, before),
            ((height + gap) / 2, last, after),
        ):
            ax.barh(i + offset, row["avg"], height=height, color=stage[2])
            ax.text(
                row["avg"] + 0.6,
                i + offset,
                "%.0f мс" % row["avg"],
                va="center",
                fontsize=8.5,
                color=TEXT_SECONDARY,
            )
    ax.set_yticks(range(len(ENDPOINTS)))
    ax.set_yticklabels(ENDPOINTS, fontsize=9, color=TEXT_SECONDARY)
    ax.invert_yaxis()
    ax.set_xlabel("Середня затримка, мс", color=TEXT_SECONDARY, fontsize=10)
    ax.set_title(
        "Затримка ендпоінтів, %d користувачів: до і після" % users,
        loc="left",
        color=TEXT_PRIMARY,
        fontsize=12,
        pad=14,
    )
    handles = [
        plt.Line2D([0], [0], color=first[2], linewidth=8, label=first[1]),
        plt.Line2D([0], [0], color=last[2], linewidth=8, label="Після всіх змін"),
    ]
    legend = ax.legend(handles=handles, frameon=False, fontsize=9, loc="lower right")
    for text in legend.get_texts():
        text.set_color(TEXT_SECONDARY)
    fig.tight_layout()
    fig.savefig(out_path, facecolor=SURFACE)
    plt.close(fig)


def fmt(value, digits=0):
    text = ("{:,.%df}" % digits).format(value)
    # Пробіл між тисячами, кома в десяткових (українська типографіка).
    return text.replace(",", " ").replace(".", ",")


def tables_md(data):
    lines = []
    lines.append("### Загальна таблиця (100 тис. чеків, 1 екземпляр, прогін 2 хв)\n")
    lines.append(
        "| Користувачів | Етап | Запитів | RPS | Середня, мс | p95, мс | p99, мс | "
        "Помилок | CPU web1 серед./макс., % | CPU db серед., % | RAM web1 макс., МБ |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for users in USERS:
        for label, name, _ in STAGES:
            item = data.get((label, users))
            if not item:
                continue
            a, r = item["agg"], item["res"]
            cpu = "%s / %s" % (fmt(r["web1"]["cpu_avg"]), fmt(r["web1"]["cpu_max"]))
            lines.append(
                "| %d | %s | %s | %s | %s | %s | %s | %d (%s%%) | %s | %s | %s |"
                % (
                    users,
                    name,
                    fmt(a["requests"]),
                    fmt(a["rps"], 1),
                    fmt(a["avg"]),
                    fmt(a["p95"]),
                    fmt(a["p99"]),
                    a["failures"],
                    fmt(a["error_pct"], 2),
                    cpu,
                    fmt(r["db"]["cpu_avg"]),
                    fmt(r["web1"]["ram_max"]),
                )
            )
    lines.append("")
    lines.append("### Ендпоінти при 300 користувачах: середня затримка / p95, мс\n")
    header = "| Ендпоінт | " + " | ".join(n for _, n, _ in STAGES) + " |"
    lines.append(header)
    lines.append("|---|" + "---|" * len(STAGES))
    for endpoint in ENDPOINTS:
        cells = []
        for label, _, _ in STAGES:
            item = data.get((label, 300))
            row = item["endpoints"].get(endpoint) if item else None
            cells.append("%s / %s" % (fmt(row["avg"]), fmt(row["p95"])) if row else "-")
        lines.append("| %s | %s |" % (endpoint, " | ".join(cells)))
    lines.append("")
    return "\n".join(lines)


def explain_md(results):
    parts = {}
    for which in ("before", "after"):
        path = results / ("explain_%s.txt" % which)
        if not path.exists():
            return ""
        text = path.read_text(encoding="utf-8")
        found = re.findall(
            r"=== (Q\d\..*?) ===.*?Execution Time: ([\d.]+) ms", text, re.S
        )
        parts[which] = {title: float(ms) for title, ms in found}
    lines = ["### EXPLAIN ANALYZE: час виконання запиту, мс\n"]
    lines.append("| Запит | Без індексів | З індексами |")
    lines.append("|---|---|---|")
    for title, before in parts["before"].items():
        after = parts["after"].get(title)
        lines.append(
            "| %s | %s | %s |"
            % (title, fmt(before, 2), fmt(after, 2) if after is not None else "-")
        )
    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", default="results/lab7")
    parser.add_argument("--out", default="docs/lab7")
    args = parser.parse_args()
    results, out = Path(args.results), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    plt.rcParams["font.family"] = "DejaVu Sans"
    data = collect(results)
    line_chart(
        data,
        "rps",
        "Пропускна здатність (RPS)",
        "Запитів за секунду",
        out / "rps.png",
        ymin=0,
    )
    line_chart(
        data,
        "avg",
        "Середня затримка (логарифмічна шкала)",
        "мс",
        out / "latency_avg.png",
        log=True,
    )
    line_chart(
        data,
        "p99",
        "99-й перцентиль затримки (логарифмічна шкала)",
        "мс",
        out / "latency_p99.png",
        log=True,
    )
    endpoints_chart(data, out / "endpoints_300.png")
    (out / "tables.md").write_text(tables_md(data), encoding="utf-8")
    (out / "explain.md").write_text(explain_md(results), encoding="utf-8")
    print("Готово: %s" % out)


if __name__ == "__main__":
    main()
