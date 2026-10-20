"""Лаба 8, додаткове завдання 1: звіт стрес-тесту (step) і раптового піку (spike).

Читає results/lab8/<мітка>_{stats_history,stats}.csv (Locust) і, якщо є,
<мітка>_resources.csv (loadtests/soak_monitor.py). Пише:
  docs/lab8/<мітка>.png, <мітка>_resources.png (якщо є ресурси)
  docs/lab8/<мітка>_report.md

Режим step: таблиця кроків, точка зламу (Break Point), відновлення після перевантаження.
Режим spike: поведінка під час піку і час повернення до норми.

Запуск:
  python loadtests/stress_report.py --mode step  --label stress_step
  python loadtests/stress_report.py --mode spike --label stress_spike
  (додати --update-readme README.md, щоб вставити звіт між маркерами
   <!-- LAB8-STEP:START --> або <!-- LAB8-SPIKE:START -->)
"""

import argparse
from pathlib import Path

from report_common import (
    BLUE,
    BLUE_DARK,
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
    read_history,
    update_readme_block,
)

# Що вважаємо «зламом» і «відновленням» (евристики, описані у README).
BREAK_ERROR_PCT = 1.0
BREAK_P95_MS = 2000.0
RECOVERY_ERROR_PCT = 0.1
RECOVERY_P95_FACTOR = 1.5
RECOVERY_P95_MIN_ADD_MS = 100.0
RECOVERY_HOLD_S = 30
STEADY_FROM_S = (
    20  # в кожному кроці рахуємо лише після перших 20 с (розгін користувачів)
)


def table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines)


def window_stats(rows):
    """RPS, p95, p99 і відсоток помилок у наборі посекундних рядків."""
    rps = mean([r["rps"] for r in rows])
    fps = mean([r["fps"] for r in rows])
    return {
        "rps": rps or 0.0,
        "error_pct": 100.0 * (fps or 0.0) / rps if rps else 0.0,
        "p95": mean([r["p95"] for r in rows if r["p95"]]),
        "p99": mean([r["p99"] for r in rows if r["p99"]]),
        "users": max((r["users"] for r in rows), default=0),
    }


def find_recovery(rows, down_ts, baseline_p95):
    """Через скільки секунд після спаду навантаження система знову в нормі.

    Норма: p95 не більший за 1,5 від базового (і не більший за базовий + 100 мс)
    та помилок не більше 0,1% протягом RECOVERY_HOLD_S секунд підряд.
    """
    limit = max(
        baseline_p95 * RECOVERY_P95_FACTOR, baseline_p95 + RECOVERY_P95_MIN_ADD_MS
    )
    after = [r for r in rows if r["ts"] >= down_ts]

    def good(r):
        if r["p95"] is None or not r["rps"]:
            return False
        error = 100.0 * (r["fps"] or 0.0) / r["rps"]
        return r["p95"] <= limit and error <= RECOVERY_ERROR_PCT

    run_start = None
    for r in after:
        if good(r):
            run_start = r["ts"] if run_start is None else run_start
            if r["ts"] - run_start >= RECOVERY_HOLD_S:
                return run_start - down_ts, limit
        else:
            run_start = None
    return None, limit


def analyze_step(rows, t0, step_s, base_users):
    peak_users = max(r["users"] for r in rows)
    peak_ts = next(r["ts"] for r in rows if r["users"] == peak_users)
    down_ts = next(
        (r["ts"] for r in rows if r["ts"] > peak_ts and r["users"] < peak_users),
        rows[-1]["ts"],
    )
    steps = {}
    for r in rows:
        if r["ts"] >= down_ts or r["users"] == 0:
            continue
        index = (r["ts"] - t0) // step_s
        if (r["ts"] - t0) % step_s >= STEADY_FROM_S:
            steps.setdefault(index, []).append(r)
    if not steps:
        raise SystemExit(
            "У історії немає жодного повного кроку. Перевір --step-seconds."
        )
    result = []
    for index in sorted(steps):
        w = window_stats(steps[index])
        w["index"] = index + 1
        w["start_s"] = index * step_s
        result.append(w)
    for w in result:
        w["broken"] = w["error_pct"] > BREAK_ERROR_PCT or (w["p95"] or 0) > BREAK_P95_MS
    break_step = next((w for w in result if w["broken"]), None)
    stable = [
        w
        for w in result
        if not w["broken"] and (break_step is None or w["index"] < break_step["index"])
    ]
    peak = max(result, key=lambda w: w["rps"])
    plateau = next(w for w in result if w["rps"] >= 0.97 * peak["rps"])
    baseline = result[0]
    recovery_s, limit = find_recovery(rows, down_ts, baseline["p95"] or 0.0)
    return {
        "steps": result,
        "break_step": break_step,
        "last_stable": stable[-1] if stable else None,
        "peak": peak,
        "plateau": plateau,
        "baseline": baseline,
        "down_ts": down_ts,
        "recovery_s": recovery_s,
        "recovery_limit": limit,
        "has_cooldown": rows[-1]["ts"] - down_ts >= 20,
    }


def analyze_spike(rows, t0, base_users):
    peak_users = max(r["users"] for r in rows)
    first_peak_ts = next(r["ts"] for r in rows if r["users"] == peak_users)
    before_peak = sorted(r["users"] for r in rows if t0 + 10 <= r["ts"] < first_peak_ts)
    base = before_peak[len(before_peak) // 2] if before_peak else base_users
    middle = base + 0.5 * (peak_users - base)
    start_ts = next(r["ts"] for r in rows if r["users"] > middle)
    end_ts = next(
        (r["ts"] for r in rows if r["ts"] > start_ts and r["users"] <= middle),
        rows[-1]["ts"],
    )
    before = [r for r in rows if start_ts - 60 <= r["ts"] < start_ts]
    during = [r for r in rows if start_ts + 10 <= r["ts"] < end_ts]
    baseline = window_stats(before)
    spike = window_stats(during)
    worst_p95 = max((r["p95"] or 0 for r in during), default=0)
    worst_error = max(
        (100.0 * (r["fps"] or 0) / r["rps"] for r in during if r["rps"]), default=0
    )
    recovery_s, limit = find_recovery(rows, end_ts, baseline["p95"] or 0.0)
    after = [r for r in rows if r["ts"] >= end_ts + (recovery_s or 0) + RECOVERY_HOLD_S]
    return {
        "base_users": base,
        "peak_users": peak_users,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "baseline": baseline,
        "spike": spike,
        "worst_p95": worst_p95,
        "worst_error": worst_error,
        "recovery_s": recovery_s,
        "recovery_limit": limit,
        "after": window_stats(after) if after else None,
    }


def plot_timeline(out_path, rows, t0, title, mark_s=None, shade=None, break_s=None):
    xs = [(r["ts"] - t0) / 60 for r in rows]
    fig, axes = new_figure(rows=3, height=8.2, sharex=True)
    ax1, ax2, ax3 = axes[0][0], axes[1][0], axes[2][0]
    ax1.plot(xs, [r["rps"] for r in rows], color=BLUE, linewidth=1.8, label="RPS")
    ax1.set_ylim(bottom=0)
    label_axis(ax1, None, "Запитів за секунду")
    twin = ax1.twinx()
    twin.plot(
        xs,
        [r["users"] for r in rows],
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
    handles = ax1.get_legend_handles_labels()[0] + twin.get_legend_handles_labels()[0]
    labels = ax1.get_legend_handles_labels()[1] + twin.get_legend_handles_labels()[1]
    ax1.legend(handles, labels, frameon=False, fontsize=9, loc="upper left")

    ax2.plot(xs, [r["p95"] for r in rows], color=BLUE, linewidth=1.8, label="p95")
    ax2.plot(
        xs,
        [r["p99"] for r in rows],
        color=BLUE_DARK,
        linewidth=1.4,
        linestyle="--",
        label="p99",
    )
    ax2.set_yscale("log")
    label_axis(ax2, None, "Затримка, мс (лог. шкала)")
    ax2.legend(frameon=False, fontsize=9, loc="upper left")

    errors = [100.0 * (r["fps"] or 0) / r["rps"] if r["rps"] else 0.0 for r in rows]
    ax3.plot(xs, errors, color=RED, linewidth=1.6, label="помилки, %")
    ax3.set_ylim(bottom=0)
    ax3.legend(frameon=False, fontsize=9, loc="upper left")
    label_axis(ax3, "Час тесту, хв", "Помилки, %")

    for ax in (ax1, ax2, ax3):
        if shade:
            ax.axvspan(
                shade[0] / 60, shade[1] / 60, color=ORANGE, alpha=0.12, linewidth=0
            )
        if break_s is not None:
            ax.axvline(break_s / 60, color=RED, linewidth=1.2, linestyle=":")
        if mark_s is not None:
            ax.axvline(mark_s / 60, color="#2a9d8f", linewidth=1.2, linestyle=":")
    finish(fig, None, out_path, title, legend=False)


def plot_resources(out_path, times, res, title, vlines=()):
    fig, axes = new_figure(rows=2, height=6.2, sharex=True)
    ax, ax2 = axes[0][0], axes[1][0]
    ax.plot(
        times,
        res["docker_cpu_util_pct"],
        color=BLUE,
        linewidth=1.8,
        label="CPU, % від усіх ядер",
    )
    ax.plot(
        times,
        res["docker_mem_util_pct"],
        color=ORANGE,
        linewidth=1.8,
        label="RAM, % від пам'яті Docker",
    )
    ax.axhline(80, color=RED, linewidth=1, linestyle="--", label="80%")
    ax.set_ylim(0, 105)
    label_axis(ax, None, "Уся система Docker, %")
    ax.legend(frameon=False, fontsize=9, loc="upper left", ncol=3)
    for name in ("web1", "web2", "web3", "db", "redis", "nginx"):
        color, style = SERVICE_STYLE[name]
        ys = [None if v is None else v / 100 for v in res.get(name + "_cpu_pct", [])]
        if any(v for v in ys if v is not None):
            ax2.plot(times, ys, color=color, linestyle=style, linewidth=1.5, label=name)
    label_axis(ax2, "Час тесту, хв", "CPU контейнера, ядер")
    ax2.legend(
        frameon=False, fontsize=9, loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=6
    )
    for a in (ax, ax2):
        for x, color in vlines:
            a.axvline(x, color=color, linewidth=1.2, linestyle=":")
    finish(fig, None, out_path, title, legend=False)


def step_markdown(label, result, args, resources_by_step):
    steps = result["steps"]
    brk, stable, peak = result["break_step"], result["last_stable"], result["peak"]
    md = ["##### Покрокове навантаження до відмови\n"]
    md.append(
        f"Кожні {args.step_seconds} с додається по {args.step_users} користувачів. У таблиці дані за останні {args.step_seconds - STEADY_FROM_S} с кожного кроку "
        f"(перші {STEADY_FROM_S} с йде розгін і не рахуються). Крок вважаємо зламаним, коли помилок понад {fmt(BREAK_ERROR_PCT, 1)}% або p95 понад {fmt(BREAK_P95_MS)} мс.\n"
    )
    rows = []
    for w in steps:
        res = resources_by_step.get(w["index"])
        if w["broken"]:
            status = "❌ злам"
        elif w["index"] > result["plateau"]["index"]:
            status = "⚠️ насичення"
        else:
            status = "✅ стабільно"
        rows.append(
            [
                w["index"],
                w["users"],
                fmt(w["rps"], 1),
                fmt(w["p95"]),
                fmt(w["p99"]),
                f"{fmt(w['error_pct'], 2)}%",
                fmt(res[0], 0) + " / " + fmt(res[1], 0) if res else "-",
                status,
            ]
        )
    md.append(
        table(
            [
                "Крок",
                "Користувачів",
                "RPS",
                "p95, мс",
                "p99, мс",
                "Помилок",
                "CPU / RAM системи, %",
                "Стан",
            ],
            rows,
        )
    )
    plateau = result["plateau"]
    md.append("\n##### Точка насичення і точка зламу (Break Point)\n")
    if len(steps) > 1 and plateau["index"] == steps[-1]["index"]:
        md.append(
            f"**Насичення.** Не досягнуто: RPS ріс до останнього кроку ({steps[-1]['users']} користувачів, RPS {fmt(peak['rps'], 1)}). Для пошуку межі збільшіть MAX_USERS.\n\n"
        )
    else:
        md.append(
            f"**Насичення.** RPS виходить на плато на кроці {plateau['index']} (**{plateau['users']} користувачів**, RPS {fmt(plateau['rps'], 1)}): далі приріст менший за 3%. "
            f"Найбільший RPS {fmt(peak['rps'], 1)} (крок {peak['index']}, {peak['users']} користувачів). Додаткові користувачі після плато лише збільшують чергу і затримку. "
            f"Робоче навантаження з soak-тесту ({args.work_users} користувачів) це {fmt(100.0 * args.work_users / plateau['users'], 0)}% від точки насичення.\n\n"
        )
    if brk:
        md.append(
            f"**Злам.** На кроці {brk['index']} (**{brk['users']} користувачів**) RPS {fmt(brk['rps'], 1)}, помилок {fmt(brk['error_pct'], 2)}%, p95 {fmt(brk['p95'])} мс. "
            f"Злам це понад {fmt(BREAK_ERROR_PCT, 1)}% помилок або p95 понад {fmt(BREAK_P95_MS)} мс."
        )
        if stable:
            md.append(
                f" Останній крок до зламу: {stable['users']} користувачів, p95 {fmt(stable['p95'])} мс, помилок {fmt(stable['error_pct'], 2)}%."
            )
    else:
        md.append(
            f"**Злам.** До {steps[-1]['users']} користувачів зламу немає: помилок не більше {fmt(BREAK_ERROR_PCT, 1)}% і p95 не більше {fmt(BREAK_P95_MS)} мс на жодному кроці. "
            "Точка зламу лежить вище, для її пошуку збільшіть MAX_USERS."
        )
    md.append("\n")
    md.append("\n##### Відновлення після перевантаження\n")
    base = result["baseline"]
    if not result["has_cooldown"]:
        md.append(
            "У цьому прогоні немає фази спаду навантаження, тож час відновлення не виміряно. Запускайте `stress.sh step` без змін: після піку він утримує базове навантаження 3 хвилини."
        )
    elif result["recovery_s"] is None:
        md.append(
            f"Після зниження навантаження до {args.base_users} користувачів система **не повернулась** до норми "
            f"(p95 не більше {fmt(result['recovery_limit'])} мс і помилок не більше {fmt(RECOVERY_ERROR_PCT, 1)}% протягом {RECOVERY_HOLD_S} с) до кінця тесту. Потрібен ручний перезапуск."
        )
    elif result["recovery_s"] <= 1:
        md.append("Після зниження навантаження система одразу працює в нормі.")
    else:
        md.append(
            f"Після зниження навантаження до {args.base_users} користувачів затримка і помилки повернулись до норми "
            f"(p95 не більше {fmt(result['recovery_limit'])} мс, помилок не більше {fmt(RECOVERY_ERROR_PCT, 1)}% протягом {RECOVERY_HOLD_S} с) через **{fmt(result['recovery_s'])} с** без втручання. "
            f"Базовий p95 на першому кроці {fmt(base['p95'])} мс."
        )
    return "\n".join(md) + "\n"


def spike_markdown(result, args):
    b, s, a = result["baseline"], result["spike"], result["after"]
    md = ["##### Раптовий пік трафіку\n"]
    md.append(
        f"Навантаження {fmt(result['base_users'])} користувачів різко зростає до {fmt(result['peak_users'])} і через {fmt(result['end_ts'] - result['start_ts'])} с знову падає до початкового.\n"
    )
    rows = [
        [
            "До піку",
            fmt(b["users"]),
            fmt(b["rps"], 1),
            fmt(b["p95"]),
            fmt(b["p99"]),
            f"{fmt(b['error_pct'], 2)}%",
        ],
        [
            "Під час піку",
            fmt(s["users"]),
            fmt(s["rps"], 1),
            fmt(s["p95"]),
            fmt(s["p99"]),
            f"{fmt(s['error_pct'], 2)}%",
        ],
    ]
    if a:
        rows.append(
            [
                "Після відновлення",
                fmt(a["users"]),
                fmt(a["rps"], 1),
                fmt(a["p95"]),
                fmt(a["p99"]),
                f"{fmt(a['error_pct'], 2)}%",
            ]
        )
    md.append(
        table(["Фаза", "Користувачів", "RPS", "p95, мс", "p99, мс", "Помилок"], rows)
    )
    md.append(
        f"\n\nНайгірші значення під час піку: p95 до {fmt(result['worst_p95'])} мс (за 10-секундними вікнами), частка помилок до {fmt(result['worst_error'], 1)}%.\n"
    )
    md.append("\n##### Час відновлення\n")
    if result["recovery_s"] is None:
        md.append(
            f"Після піку система **не повернулась** до норми до кінця тесту (норма: p95 не більше {fmt(result['recovery_limit'])} мс, помилок не більше {fmt(RECOVERY_ERROR_PCT, 1)}% протягом {RECOVERY_HOLD_S} с)."
        )
    elif result["recovery_s"] <= 1:
        if (
            result["worst_p95"] > result["recovery_limit"]
            or result["worst_error"] > RECOVERY_ERROR_PCT
        ):
            md.append(
                "Під час піку затримка зростала, але щойно навантаження спало, затримка і помилки одразу повернулись до норми."
            )
        else:
            md.append(
                "Після піку затримка і помилки одразу в нормі: система пік витримала без помітної деградації."
            )
    else:
        md.append(
            f"Після спаду навантаження затримка і помилки повернулись до норми (p95 не більше {fmt(result['recovery_limit'])} мс, помилок не більше {fmt(RECOVERY_ERROR_PCT, 1)}% протягом {RECOVERY_HOLD_S} с) "
            f"через **{fmt(result['recovery_s'])} с** без втручання."
        )
    return "\n".join(md) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["step", "spike"], required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--results", default="results/lab8")
    parser.add_argument("--out", default="docs/lab8")
    parser.add_argument("--img-prefix", default="docs/lab8")
    parser.add_argument("--step-seconds", type=int, default=60)
    parser.add_argument("--step-users", type=int, default=100)
    parser.add_argument(
        "--base-users",
        type=int,
        default=150,
        help="початкове навантаження для spike і cooldown",
    )
    parser.add_argument(
        "--work-users", type=int, default=400, help="робоче навантаження з soak-тесту"
    )
    parser.add_argument("--update-readme", metavar="README.md")
    args = parser.parse_args()

    results, out = Path(args.results), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = read_history(results / f"{args.label}_stats_history.csv")
    started = [r for r in rows if r["users"] > 0]
    if not started:
        raise SystemExit("У історії Locust немає жодного користувача.")
    t0 = started[0]["ts"]
    end_min = (rows[-1]["ts"] - t0) / 60

    res_path = results / f"{args.label}_resources.csv"
    times, res = ([], {})
    if res_path.exists():
        times, res = load_resources(res_path, t0)
        times, res = cut(times, res, end_min)
    has_res = len(times) > 3

    resources_by_step = {}
    if args.mode == "step":
        result = analyze_step(rows, t0, args.step_seconds, args.base_users)
        if has_res:
            for w in result["steps"]:
                lo = (w["start_s"] + STEADY_FROM_S) / 60
                hi = (w["start_s"] + args.step_seconds) / 60
                cpu = mean(
                    [
                        v
                        for t, v in zip(times, res["docker_cpu_util_pct"])
                        if lo <= t < hi
                    ]
                )
                ram = mean(
                    [
                        v
                        for t, v in zip(times, res["docker_mem_util_pct"])
                        if lo <= t < hi
                    ]
                )
                if cpu is not None:
                    resources_by_step[w["index"]] = (cpu, ram)
        brk = result["break_step"]
        break_s = brk["start_s"] if brk else None
        down_s = result["down_ts"] - t0
        plot_timeline(
            out / f"{args.label}.png",
            rows,
            t0,
            "Стрес-тест: покрокове навантаження",
            mark_s=down_s,
            break_s=break_s,
        )
        if has_res:
            vlines = [(down_s / 60, "#2a9d8f")] + (
                [(break_s / 60, RED)] if break_s is not None else []
            )
            plot_resources(
                out / f"{args.label}_resources.png",
                times,
                res,
                "Ресурси під час стрес-тесту (червона лінія це злам, зелена початок спаду)",
                vlines,
            )
        body = step_markdown(args.label, result, args, resources_by_step)
        marker = "LAB8-STEP"
        summary = {
            "break_users": brk["users"] if brk else None,
            "peak_rps": result["peak"]["rps"],
            "recovery_s": result["recovery_s"],
        }
    else:
        result = analyze_spike(rows, t0, args.base_users)
        start_s, end_s = result["start_ts"] - t0, result["end_ts"] - t0
        mark = (
            end_s + result["recovery_s"] if result["recovery_s"] is not None else None
        )
        plot_timeline(
            out / f"{args.label}.png",
            rows,
            t0,
            "Раптовий пік: затримка, RPS, помилки",
            mark_s=mark,
            shade=(start_s, end_s),
        )
        if has_res:
            vlines = [(start_s / 60, ORANGE), (end_s / 60, ORANGE)]
            plot_resources(
                out / f"{args.label}_resources.png",
                times,
                res,
                "Ресурси під час піку (помаранчеві лінії це початок і кінець піку)",
                vlines,
            )
        body = spike_markdown(result, args)
        marker = "LAB8-SPIKE"
        summary = {
            "worst_p95": result["worst_p95"],
            "worst_error": result["worst_error"],
            "recovery_s": result["recovery_s"],
        }

    p = args.img_prefix
    body += f"\n![Графік]({p}/{args.label}.png)\n"
    if has_res:
        body += f"\n![Ресурси]({p}/{args.label}_resources.png)\n"
    (out / f"{args.label}_report.md").write_text(body, encoding="utf-8")
    if args.update_readme:
        update_readme_block(args.update_readme, marker, body)
        print(f"README оновлено: {args.update_readme}")
    print(f"Готово: {out}/{args.label}_report.md", summary)


if __name__ == "__main__":
    main()
