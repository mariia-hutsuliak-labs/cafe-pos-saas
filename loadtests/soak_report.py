"""Лаба 8: підсумковий протокол soak-тесту (таблиці, графіки, висновок).

Читає results/lab8/<мітка>_{stats,stats_history,failures}.csv (Locust),
<мітка>_resources.csv і <мітка>_resources.meta.json (loadtests/soak_monitor.py) і пише:
  docs/lab8/soak_latency.png, soak_throughput.png, soak_cpu.png, soak_ram.png, soak_db.png
  docs/lab8/report.md      (протокол: таблиці, перевірка критеріїв, висновок)
  docs/lab8/summary.json   (ті самі числа для скриптів і Grafana-порівняння)

Запуск (потрібен matplotlib):
  python loadtests/soak_report.py
  python loadtests/soak_report.py --label soak --warmup-min 5 --update-readme README.md

--update-readme вставляє протокол у README між маркерами
<!-- LAB8-SOAK:START --> і <!-- LAB8-SOAK:END -->.
"""

import argparse
import json
from pathlib import Path

from matplotlib.ticker import FuncFormatter

from report_common import (
    BLUE,
    BLUE_DARK,
    BLUE_LIGHT,
    GRID,
    ORANGE,
    RED,
    SERVICE_STYLE,
    TEXT_SECONDARY,
    cut,
    finish,
    fmt,
    label_axis,
    load_resources,
    mean,
    new_figure,
    per_minute,
    percentile,
    read_failures,
    read_history,
    read_stats,
    trend,
    update_readme_block,
    window_means,
)

CONTAINERS = ["web1", "web2", "web3", "db", "redis", "nginx"]

# Критерії успішності з методички.
CPU_LIMIT = 80.0
RAM_LIMIT = 80.0
ERROR_LIMIT_PCT = 0.1

# Пороги для висновків про витоки і деградацію (евристики, описані у README).
LEAK_MIN_MB = 10.0  # менший ріст пам'яті ігноруємо
LEAK_MIN_PCT = 10.0  # і менший за 10% теж
LEAK_MIN_R2 = 0.5  # ріст має бути схожий на пряму, а не на випадкові коливання
PLATEAU_RATIO = 0.3  # нахил у другій половині менший за 30% загального = плато
LATENCY_RATIO = 1.2  # затримка в кінці більша за початок більш ніж на 20%
LATENCY_MIN_DIFF_MS = 20.0  # і різниця більша за 20 мс
CONN_GROWTH_RATIO = 1.1

# Максимум із лаби 7 (1 екземпляр, 100 тис. чеків, 600 користувачів, без збоїв).
LAB7_MAX_USERS = 600
LAB7_MAX_RPS = 204.0


def analyze_series(times, values, warmup, unit_limit_mb=LEAK_MIN_MB):
    """Тренд величини після розігріву. Для пам'яті: чи є підозра на витік."""
    steady = [(t, v) for t, v in zip(times, values) if t >= warmup and v is not None]
    if len(steady) < 6 or steady[-1][0] - steady[0][0] < 15:
        return {"verdict": "замало даних", "ok": None}
    ts = [p[0] for p in steady]
    vs = [p[1] for p in steady]
    span = ts[-1] - ts[0]
    window = min(10.0, span / 3)
    first = window_means(ts, vs, ts[0], ts[0] + window)
    last = window_means(ts, vs, ts[-1] - window, ts[-1] + 1)
    slope_min, r2 = trend(ts, vs)
    middle = ts[0] + span / 2
    half = [(t, v) for t, v in steady if t >= middle]
    slope_half_min, _ = trend([p[0] for p in half], [p[1] for p in half])
    growth = last - first
    pct = 100.0 * growth / first if first else 0.0
    if growth < unit_limit_mb or pct < LEAK_MIN_PCT:
        verdict, ok = "стабільно", True
    elif r2 < LEAK_MIN_R2:
        verdict, ok = "коливається, стійкого росту немає", True
    elif slope_min > 0 and slope_half_min < PLATEAU_RATIO * slope_min:
        verdict, ok = "виріс і вийшов на плато", True
    else:
        verdict, ok = "росте весь час (підозра на витік)", False
    return {
        "first": first,
        "last": last,
        "growth": growth,
        "growth_pct": pct,
        "slope_per_hour": slope_min * 60,
        "r2": r2,
        "window_min": window,
        "verdict": verdict,
        "ok": ok,
    }


def analyze_latency(mins, warmup):
    steady = [m for m in mins if m["minute"] >= warmup]
    if len(steady) < 6:
        return {"verdict": "замало даних", "ok": None}
    ts = [m["minute"] for m in steady]
    span = ts[-1] - ts[0]
    window = min(10.0, span / 3)
    result = {"window_min": window}
    ok = True
    for key in ("avg", "p95", "p99"):
        vs = [m[key] for m in steady]
        first = window_means(ts, vs, ts[0], ts[0] + window)
        last = window_means(ts, vs, ts[-1] - window, ts[-1] + 1)
        slope, _ = trend(ts, vs)
        result[key] = {"first": first, "last": last, "slope_per_hour": slope * 60}
        if (
            first
            and last
            and last > first * LATENCY_RATIO
            and last - first > LATENCY_MIN_DIFF_MS
        ):
            ok = False
    result["ok"] = ok
    result["verdict"] = "деградації немає" if ok else "затримка зросла"
    return result


def build_charts(out, mins, rtimes, res, warmup, end_min):
    # 1. Затримка в часі
    fig, axes = new_figure()
    ax = axes[0][0]
    xs = [m["minute"] for m in mins]
    ax.plot(xs, [m["p50"] for m in mins], color=BLUE_LIGHT, linewidth=2, label="p50")
    ax.plot(xs, [m["p95"] for m in mins], color=BLUE, linewidth=2, label="p95")
    ax.plot(
        xs,
        [m["p99"] for m in mins],
        color=BLUE_DARK,
        linewidth=2,
        linestyle="--",
        label="p99",
    )
    ax.plot(xs, [m["avg"] for m in mins], color=ORANGE, linewidth=1.5, label="середня")
    ax.axvline(warmup, color=GRID, linewidth=1.5)
    ax.text(
        warmup,
        ax.get_ylim()[1],
        " розігрів",
        color=TEXT_SECONDARY,
        fontsize=8,
        va="top",
    )
    ax.set_ylim(bottom=0)
    label_axis(ax, "Час тесту, хв", "Затримка, мс")
    finish(
        fig,
        ax,
        out / "soak_latency.png",
        "Затримка протягом тесту (по хвилинах)",
        loc="best",
    )

    # 2. Пропускна здатність і помилки
    fig, axes = new_figure(rows=2, height=6.2, sharex=True)
    ax, ax2 = axes[0][0], axes[1][0]
    ax.plot(xs, [m["rps"] for m in mins], color=BLUE, linewidth=2, label="RPS")
    ax.set_ylim(bottom=0)
    label_axis(ax, None, "Запитів за секунду")
    twin = ax.twinx()
    twin.plot(
        xs,
        [m["users"] for m in mins],
        color=TEXT_SECONDARY,
        linewidth=1.2,
        linestyle="--",
        label="користувачі",
    )
    twin.set_ylim(bottom=0)
    twin.tick_params(colors=TEXT_SECONDARY, labelsize=9, length=0)
    for side in ("top", "left", "bottom"):
        twin.spines[side].set_visible(False)
    twin.spines["right"].set_color(GRID)
    twin.set_ylabel("Користувачі", color=TEXT_SECONDARY, fontsize=10)
    handles = ax.get_legend_handles_labels()[0] + twin.get_legend_handles_labels()[0]
    labels = ax.get_legend_handles_labels()[1] + twin.get_legend_handles_labels()[1]
    ax.legend(handles, labels, frameon=False, fontsize=9, loc="lower right")
    ax2.bar(
        xs,
        [m["error_pct"] for m in mins],
        width=0.8,
        color=RED,
        label="помилок за хвилину, %",
    )
    ax2.axhline(
        ERROR_LIMIT_PCT,
        color=TEXT_SECONDARY,
        linewidth=1,
        linestyle="--",
        label="поріг 0,1%",
    )
    label_axis(ax2, "Час тесту, хв", "Помилки, %")
    finish(
        fig,
        ax2,
        out / "soak_throughput.png",
        "Пропускна здатність і відсоток помилок",
        loc="best",
    )

    # 3. CPU
    fig, axes = new_figure(rows=2, height=6.2, sharex=True)
    ax, ax2 = axes[0][0], axes[1][0]
    ax.plot(
        rtimes,
        res["docker_cpu_util_pct"],
        color=BLUE,
        linewidth=2,
        label="уся система Docker",
    )
    ax.axhline(CPU_LIMIT, color=RED, linewidth=1.2, linestyle="--", label="поріг 80%")
    ax.set_ylim(
        0, max(100, max(v for v in res["docker_cpu_util_pct"] if v is not None) * 1.1)
    )
    label_axis(ax, None, "CPU, % від усіх ядер")
    for name in CONTAINERS:
        color, style = SERVICE_STYLE[name]
        ys = [None if v is None else v / 100 for v in res.get(name + "_cpu_pct", [])]
        if any(v for v in ys if v is not None):
            ax2.plot(
                rtimes, ys, color=color, linestyle=style, linewidth=1.6, label=name
            )
    label_axis(ax2, "Час тесту, хв", "CPU контейнера, ядер")
    ax2.legend(
        frameon=False, fontsize=9, loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=6
    )
    finish(fig, ax, out / "soak_cpu.png", "Завантаження CPU", loc="upper right")

    # 4. RAM
    fig, axes = new_figure(rows=2, height=6.2, sharex=True)
    ax, ax2 = axes[0][0], axes[1][0]
    ax.plot(
        rtimes,
        res["docker_mem_util_pct"],
        color=BLUE,
        linewidth=2,
        label="уся система Docker",
    )
    ax.axhline(RAM_LIMIT, color=RED, linewidth=1.2, linestyle="--", label="поріг 80%")
    ax.set_ylim(0, 100)
    label_axis(ax, None, "RAM, % від пам'яті Docker")
    for name in CONTAINERS:
        color, style = SERVICE_STYLE[name]
        ys = res.get(name + "_mem_mb", [])
        if any(v for v in ys if v is not None):
            ax2.plot(
                rtimes, ys, color=color, linestyle=style, linewidth=1.6, label=name
            )
            slope, r2 = trend(
                [t for t in rtimes if t >= warmup],
                [y for t, y in zip(rtimes, ys) if t >= warmup],
            )
            if r2 >= LEAK_MIN_R2 and name.startswith("web"):
                pts = [
                    (t, y) for t, y in zip(rtimes, ys) if t >= warmup and y is not None
                ]
                mean_t = mean([p[0] for p in pts])
                mean_y = mean([p[1] for p in pts])
                ax2.plot(
                    [pts[0][0], pts[-1][0]],
                    [
                        mean_y + slope * (pts[0][0] - mean_t),
                        mean_y + slope * (pts[-1][0] - mean_t),
                    ],
                    color=color,
                    linewidth=1,
                    alpha=0.6,
                )
    label_axis(ax2, "Час тесту, хв", "RAM контейнера, МБ")
    ax2.legend(
        frameon=False, fontsize=9, loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=6
    )
    finish(
        fig,
        ax,
        out / "soak_ram.png",
        "Використання RAM (тонка лінія це тренд після розігріву)",
        loc="upper right",
    )

    # 5. База і Redis
    fig, axes = new_figure(rows=2, cols=2, height=6.2, width=9.2, sharex=True)
    panels = [
        ("db_connections", "Підключення до PostgreSQL", BLUE),
        ("sales_rows", "Чеків у таблиці sales", ORANGE),
        ("db_size_mb", "Розмір бази, МБ", ORANGE),
        ("redis_used_mb", "Пам'ять Redis, МБ", "#b4458b"),
    ]
    for ax, (key, title, color) in zip(axes.flat, panels):
        ax.plot(rtimes, res.get(key, []), color=color, linewidth=1.8)
        ax.set_title(title, loc="left", fontsize=10, color=TEXT_SECONDARY)
        ax.set_ylim(bottom=0)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _pos: fmt(v)))
    for ax in axes[1]:
        label_axis(ax, "Час тесту, хв")
    finish(fig, None, out / "soak_db.png", legend=False)


STATUS = {True: "✅ виконано", False: "❌ не виконано", None: "⚠️ замало даних"}


def table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", default="results/lab8")
    parser.add_argument("--label", default="soak")
    parser.add_argument("--out", default="docs/lab8")
    parser.add_argument(
        "--img-prefix", default="docs/lab8", help="шлях до картинок у markdown"
    )
    parser.add_argument(
        "--warmup-min",
        type=float,
        default=5.0,
        help="хвилини розігріву, їх не враховуємо у висновках про тренд",
    )
    parser.add_argument("--update-readme", metavar="README.md")
    args = parser.parse_args()

    results, out = Path(args.results), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    label = args.label

    stats = read_stats(results / f"{label}_stats.csv")
    history = read_history(results / f"{label}_stats_history.csv")
    failures = read_failures(results / f"{label}_failures.csv")
    meta_path = results / f"{label}_resources.meta.json"
    meta = (
        json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    )

    started = [r for r in history if r["count"] > 0]
    if not started:
        raise SystemExit("У історії Locust немає жодного запиту.")
    t0 = started[0]["ts"]
    t_end = history[-1]["ts"]
    duration_s = t_end - t0
    end_min = duration_s / 60
    warmup = min(args.warmup_min, end_min / 4)

    mins = per_minute(history, t0)
    agg = stats["Aggregated"]
    total = int(agg["Request Count"])
    fails = int(agg["Failure Count"])
    error_pct = 100.0 * fails / total if total else 0.0
    rps = total / duration_s if duration_s else 0.0
    steady_rps = [m["rps"] for m in mins if m["minute"] >= warmup]
    max_users = max(r["users"] for r in history)

    # Ресурси
    rtimes, res = load_resources(results / f"{label}_resources.csv", t0)
    rtimes, res = cut(rtimes, res, end_min)
    has_res = len(rtimes) > 3
    cpu_series = res.get("docker_cpu_util_pct", [])
    ram_series = res.get("docker_mem_util_pct", [])
    cpu_avg, cpu_p95, cpu_max = (
        mean(cpu_series),
        percentile(cpu_series, 95),
        max([v for v in cpu_series if v is not None], default=None),
    )
    ram_avg, ram_p95, ram_max = (
        mean(ram_series),
        percentile(ram_series, 95),
        max([v for v in ram_series if v is not None], default=None),
    )

    memory = {}
    cpu_by = {}
    for name in CONTAINERS:
        mem_vals = res.get(name + "_mem_mb", [])
        if any(v for v in mem_vals if v is not None):
            memory[name] = analyze_series(rtimes, mem_vals, warmup)
            memory[name]["max"] = max(v for v in mem_vals if v is not None)
            cpu_by[name] = (
                mean(res.get(name + "_cpu_pct", [])),
                max(
                    [v for v in res.get(name + "_cpu_pct", []) if v is not None],
                    default=None,
                ),
            )

    conns = analyze_series(
        rtimes, res.get("db_connections", []), warmup, unit_limit_mb=3
    )
    latency = analyze_latency(mins, warmup)

    sales_rows = [v for v in res.get("sales_rows", []) if v is not None]
    db_size = [v for v in res.get("db_size_mb", []) if v is not None]

    state_start, state_end = meta.get("state_start", {}), meta.get("state_end", {})
    restarts = {
        s: state_end.get(s, {}).get("restarts", 0)
        - state_start.get(s, {}).get("restarts", 0)
        for s in state_start
    }
    oom = [s for s, v in state_end.items() if v.get("oom_killed")]
    not_running = [s for s, v in state_end.items() if v.get("status") != "running"]

    # Перевірка критеріїв
    checks = []
    checks.append(
        (
            "Error Rate",
            f"≤ {fmt(ERROR_LIMIT_PCT, 1)}%",
            f"{fmt(error_pct, 3)}% ({fails} з {fmt(total)})",
            error_pct <= ERROR_LIMIT_PCT,
        )
    )
    if has_res:
        checks.append(
            (
                "CPU (уся система Docker, середнє)",
                f"≤ {CPU_LIMIT:.0f}%",
                f"{fmt(cpu_avg, 1)}% (p95 {fmt(cpu_p95, 1)}%, макс. {fmt(cpu_max, 1)}%)",
                cpu_avg <= CPU_LIMIT,
            )
        )
        checks.append(
            (
                "RAM (уся система Docker, середнє)",
                f"≤ {RAM_LIMIT:.0f}%",
                f"{fmt(ram_avg, 1)}% (p95 {fmt(ram_p95, 1)}%, макс. {fmt(ram_max, 1)}%)",
                ram_avg <= RAM_LIMIT,
            )
        )
        mem_oks = [v["ok"] for v in memory.values()]
        mem_ok = False if False in mem_oks else (None if None in mem_oks else True)
        checks.append(
            (
                "Витоки пам'яті",
                "немає росту без повернення",
                "замало даних"
                if mem_ok is None
                else (
                    "; ".join(
                        f"{n}: {v['verdict']}"
                        for n, v in memory.items()
                        if n.startswith("web") or v["ok"] is not True
                    )
                    or "-"
                ),
                mem_ok,
            )
        )
        checks.append(
            (
                "Підключення до БД",
                "не накопичуються",
                f"{fmt(conns.get('first'), 0)} → {fmt(conns.get('last'), 0)}, {conns['verdict']}",
                conns["ok"],
            )
        )
    checks.append(
        (
            "Затримка в часі",
            f"кінець ≤ {fmt(LATENCY_RATIO, 1)}× початку",
            latency["verdict"],
            latency["ok"],
        )
    )
    if meta:
        checks.append(
            (
                "Перезапуски і OOM",
                "0 перезапусків, без OOM",
                f"перезапусків: {sum(restarts.values())}; OOM: {', '.join(oom) or 'ні'}; не працюють: {', '.join(not_running) or 'ні'}",
                not any(restarts.values()) and not oom and not not_running,
            )
        )
    all_ok = all(c[3] is True for c in checks)

    if has_res:
        build_charts(out, mins, rtimes, res, warmup, end_min)
    else:
        print("Файлу ресурсів немає або він порожній: графіки CPU/RAM пропущено.")

    # Markdown
    bulk = str(meta.get("bulk_sales", ""))
    bulk_text = (
        f"{fmt(float(bulk), 0)} чеків за 90 днів + демо-дані"
        if bulk.isdigit()
        else "не вказано"
    )
    md = []
    md.append("##### Профіль стабільності і критерії\n")
    md.append(
        table(
            ["Параметр", "Значення"],
            [
                [
                    "Тривалість тесту",
                    f"{fmt(end_min, 0)} хв ({fmt(end_min / 60, 2)} год)",
                ],
                [
                    "Віртуальних користувачів",
                    f"{max_users} ({fmt(100.0 * max_users / LAB7_MAX_USERS, 0)}% від {LAB7_MAX_USERS}, максимуму лаби 7 без збоїв)",
                ],
                [
                    "Середній RPS",
                    f"{fmt(rps, 1)} ({fmt(100.0 * rps / LAB7_MAX_RPS, 0)}% від {fmt(LAB7_MAX_RPS, 0)} RPS, максимуму лаби 7)",
                ],
                [
                    "Сценарій",
                    "`loadtests/locustfile.py`: каса 70%, власник мережі 20%, лише входи 10%; повторний вхід, коли токен старший за 50 хв",
                ],
                [
                    "Система",
                    "nginx (Round Robin) → web1, web2, web3 → PostgreSQL + Redis, усе в Docker",
                ],
                ["Дані на старті", bulk_text],
                [
                    "Критерії успіху",
                    f"CPU ≤ {CPU_LIMIT:.0f}%, RAM ≤ {RAM_LIMIT:.0f}%, Error Rate ≤ {fmt(ERROR_LIMIT_PCT, 1)}%",
                ],
            ],
        )
    )
    md.append("\n##### Підсумкові метрики\n")
    p50, p95, p99 = float(agg["50%"]), float(agg["95%"]), float(agg["99%"])
    md.append(
        table(
            [
                "RPS (серед.)",
                "RPS (мін. / макс. за хв)",
                "Запитів",
                "Avg, мс",
                "Min, мс",
                "Max, мс",
                "p50, мс",
                "p95, мс",
                "p99, мс",
                "Error Rate",
            ],
            [
                [
                    fmt(rps, 1),
                    f"{fmt(min(steady_rps), 1)} / {fmt(max(steady_rps), 1)}"
                    if steady_rps
                    else "-",
                    fmt(total),
                    fmt(float(agg["Average Response Time"]), 1),
                    fmt(float(agg["Min Response Time"]), 1),
                    fmt(float(agg["Max Response Time"])),
                    fmt(p50),
                    fmt(p95),
                    fmt(p99),
                    f"{fmt(error_pct, 3)}% ({fails})",
                ]
            ],
        )
    )
    md.append("\nПо ендпоінтах:\n")
    endpoint_rows = []
    for name, row in sorted(stats.items()):
        if name == "Aggregated":
            continue
        n, f_ = int(row["Request Count"]), int(row["Failure Count"])
        endpoint_rows.append(
            [
                name,
                fmt(n),
                f_,
                fmt(float(row["Average Response Time"]), 1),
                fmt(float(row["95%"])),
                fmt(float(row["99%"])),
                fmt(float(row["Max Response Time"])),
            ]
        )
    md.append(
        table(
            [
                "Ендпоінт",
                "Запитів",
                "Помилок",
                "Avg, мс",
                "p95, мс",
                "p99, мс",
                "Max, мс",
            ],
            endpoint_rows,
        )
    )
    if failures:
        md.append("\nПомилки:\n")
        md.append(
            table(
                ["Метод", "Ендпоінт", "Помилка", "Разів"],
                [
                    [r["Method"], r["Name"], r["Error"], r["Occurrences"]]
                    for r in failures
                ],
            )
        )

    if has_res:
        md.append("\n##### Ресурси\n")
        md.append(
            table(
                ["Показник", "Середнє", "p95", "Максимум"],
                [
                    [
                        "CPU всієї системи Docker, % від усіх ядер",
                        fmt(cpu_avg, 1),
                        fmt(cpu_p95, 1),
                        fmt(cpu_max, 1),
                    ],
                    [
                        "RAM всієї системи Docker, % від пам'яті",
                        fmt(ram_avg, 1),
                        fmt(ram_p95, 1),
                        fmt(ram_max, 1),
                    ],
                ],
            )
        )
        md.append(
            f"\nDocker бачить {meta.get('docker_ncpu', '?')} ядер і {fmt((meta.get('docker_mem_total_mb') or 0) / 1024, 1)} ГБ пам'яті. "
            "CPU і RAM рахуються як сума контейнерів, поділена на ці ресурси. Locust працює на хості поза Docker, тож у цих відсотках його немає.\n"
        )
        md.append(
            table(
                [
                    "Контейнер",
                    "CPU серед., % одного ядра",
                    "CPU макс., %",
                    "RAM на початку, МБ",
                    "RAM в кінці, МБ",
                    "RAM макс., МБ",
                    "Нахил, МБ/год",
                    "Висновок",
                ],
                [
                    [
                        n,
                        fmt(cpu_by[n][0], 0),
                        fmt(cpu_by[n][1], 0),
                        fmt(v.get("first"), 0),
                        fmt(v.get("last"), 0),
                        fmt(v["max"], 0),
                        fmt(v.get("slope_per_hour"), 1),
                        v["verdict"],
                    ]
                    for n, v in memory.items()
                ],
            )
        )
        md.append(
            f"\n«На початку» і «в кінці» це середнє за {fmt(latency.get('window_min', 10), 0)} хв після розігріву ({fmt(warmup, 0)} хв) і перед завершенням. "
            f"Підозра на витік, коли пам'ять виросла більш ніж на {LEAK_MIN_MB:.0f} МБ і {LEAK_MIN_PCT:.0f}%, ріст схожий на пряму (R² ≥ {fmt(LEAK_MIN_R2, 1)}) і не виходить на плато.\n"
        )
        md.append("\n##### Дані, підключення, диск\n")
        if sales_rows and db_size:
            md.append(
                table(
                    ["Показник", "На початку", "В кінці", "Максимум"],
                    [
                        [
                            "Підключень до PostgreSQL",
                            fmt(conns.get("first"), 0),
                            fmt(conns.get("last"), 0),
                            fmt(
                                max(v for v in res["db_connections"] if v is not None),
                                0,
                            ),
                        ],
                        [
                            "Чеків у sales (оцінка PostgreSQL)",
                            fmt(sales_rows[0]),
                            fmt(sales_rows[-1]),
                            fmt(max(sales_rows)),
                        ],
                        [
                            "Розмір бази, МБ",
                            fmt(db_size[0], 0),
                            fmt(db_size[-1], 0),
                            fmt(max(db_size), 0),
                        ],
                    ],
                )
            )
            md.append(
                f"\nЗа тест база виросла на {fmt(sales_rows[-1] - sales_rows[0])} чеків. Частину росту RAM у db і частину росту затримки аналітики дає саме більший обсяг даних, це природний ріст, а не витік. "
                "Логи контейнерів обмежені ротацією (3 файли по 10 МБ на контейнер у `docker-compose.yml`), тому диск ними не заповнюється.\n"
            )

    md.append("\n##### Стабільність затримки\n")
    if "avg" in latency:
        md.append(
            table(
                ["Показник", "На початку, мс", "В кінці, мс", "Зміна", "Нахил, мс/год"],
                [
                    [
                        {"avg": "Середня", "p95": "p95", "p99": "p99"}[k],
                        fmt(latency[k]["first"], 1),
                        fmt(latency[k]["last"], 1),
                        f"{fmt(100.0 * (latency[k]['last'] / latency[k]['first'] - 1), 0)}%"
                        if latency[k]["first"]
                        else "-",
                        fmt(latency[k]["slope_per_hour"], 1),
                    ]
                    for k in ("avg", "p95", "p99")
                ],
            )
        )
        md.append(
            f"\nПорівнюємо перші і останні {fmt(latency['window_min'], 0)} хв після розігріву. Деградація це зростання більш ніж у {fmt(LATENCY_RATIO, 1)} раза і більш ніж на {LATENCY_MIN_DIFF_MS:.0f} мс. "
            "p95 і p99 по хвилинах це середнє значень «за останні 10 секунд», які пише Locust.\n"
        )
    else:
        md.append("Замало даних для порівняння початку і кінця.\n")

    md.append("\n##### Перевірка критеріїв\n")
    md.append(
        table(
            ["Критерій", "Вимога", "Результат", "Статус"],
            [[a, b, c, STATUS[ok]] for a, b, c, ok in checks],
        )
    )

    if has_res:
        p = args.img_prefix
        md.append("\n##### Графіки\n")
        md.append(f"Затримка протягом тесту:\n\n![Затримка]({p}/soak_latency.png)\n")
        md.append(
            f"Пропускна здатність і помилки:\n\n![RPS і помилки]({p}/soak_throughput.png)\n"
        )
        md.append(f"CPU:\n\n![CPU]({p}/soak_cpu.png)\n")
        md.append(f"RAM:\n\n![RAM]({p}/soak_ram.png)\n")
        md.append(
            f"Підключення до бази, обсяг даних, Redis:\n\n![База]({p}/soak_db.png)\n"
        )

    md.append("\n##### Висновок\n")
    failed = [c for c in checks if c[3] is False]
    unknown = [c for c in checks if c[3] is None]
    hours = fmt(end_min / 60, 1)
    if not failed and unknown:
        md.append(
            f"Тест тривав {fmt(end_min, 0)} хв. Цього мало, щоб судити про витоки пам'яті і деградацію затримки: після розігріву потрібно хоча б 15-20 хв, а для висновку про стабільність рекомендовано від 1 години. "
            "Перевірені критерії (помилки, CPU, RAM, перезапуски) виконано. Для остаточного висновку про готовність до експлуатації треба повторити тест довше."
        )
    elif all_ok:
        md.append(
            f"Система витримала {hours} год стабільного навантаження {max_users} користувачів (середній RPS {fmt(rps, 1)}) без збоїв. "
            f"Error Rate {fmt(error_pct, 3)}% при порозі {fmt(ERROR_LIMIT_PCT, 1)}%, середній CPU {fmt(cpu_avg, 1)}% і RAM {fmt(ram_avg, 1)}% при порогах 80%. "
            f"Пам'ять репліки застосунку не росла без повернення, підключення до бази не накопичувались, затримка p95 на початку {fmt(latency['p95']['first'], 0)} мс і в кінці {fmt(latency['p95']['last'], 0)} мс. "
            "За критеріями стабільності продукт готовий до експлуатації на такому навантаженні."
            if has_res and "p95" in latency
            else f"Система витримала {hours} год навантаження без порушення критеріїв."
        )
    else:
        md.append(
            f"За {hours} год тесту виконано не всі критерії. Не пройдено: "
            + "; ".join(f"{c[0]} ({c[2]})" for c in failed)
            + ". До виправлення цих пунктів продукт не можна вважати готовим до експлуатації на такому навантаженні."
        )
    md.append(
        "\n\nОбмеження тесту: один прогін; Locust і Docker працюють на одному ноутбуці, тож Locust забирає частину процесора хоста; "
        "користувачі Locust роблять паузи, тому RPS визначається кількістю користувачів; база за тест значно виросла, "
        "тому частина змін затримки пояснюється обсягом даних."
    )
    report = "\n".join(md) + "\n"
    (out / "report.md").write_text(report, encoding="utf-8")

    summary = {
        "duration_min": end_min,
        "users": max_users,
        "requests": total,
        "failures": fails,
        "error_pct": error_pct,
        "rps_avg": rps,
        "latency_ms": {
            "avg": float(agg["Average Response Time"]),
            "min": float(agg["Min Response Time"]),
            "max": float(agg["Max Response Time"]),
            "p50": p50,
            "p95": p95,
            "p99": p99,
        },
        "cpu_util_pct": {"avg": cpu_avg, "p95": cpu_p95, "max": cpu_max},
        "ram_util_pct": {"avg": ram_avg, "p95": ram_p95, "max": ram_max},
        "memory": memory,
        "latency_trend": latency,
        "db_connections": conns,
        "checks": [
            {"name": a, "requirement": b, "result": c, "ok": ok}
            for a, b, c, ok in checks
        ],
        "all_ok": all_ok,
    }
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=float),
        encoding="utf-8",
    )

    if args.update_readme:
        update_readme_block(args.update_readme, "LAB8-SOAK", report)
        print(f"README оновлено: {args.update_readme}")
    print(
        f"Готово: {out}/report.md, summary.json"
        + (", графіки *.png" if has_res else "")
    )
    if all_ok:
        print("Загальний результат: усі критерії виконано")
    elif any(c[3] is False for c in checks):
        print("Загальний результат: є невиконані критерії")
    else:
        print(
            "Загальний результат: тест закороткий для висновку про витоки і деградацію"
        )


if __name__ == "__main__":
    main()
