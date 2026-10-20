"""Лаба 8: збір системних метрик увесь час тривалого тесту.

Кожні N секунд пише один рядок у CSV:
  - CPU і RAM кожного контейнера (docker stats): web1, web2, web3, db, redis, nginx;
  - CPU і RAM усієї Docker-системи у відсотках від доступних ядер і пам'яті
    (саме з ними порівнюємо критерії «CPU ≤ 80%» і «RAM ≤ 80%»);
  - кількість підключень до PostgreSQL (ріст без повернення = витік підключень);
  - розмір бази і кількість чеків (щоб відрізнити витік від природного росту даних);
  - пам'ять Redis.

Також пише <out>.meta.json: час старту і завершення, кількість ядер, обсяг пам'яті,
кількість перезапусків контейнерів до і після тесту та чи не вбивав їх OOM-killer.

Запуск (з кореня проєкту, стек уже піднятий через docker compose):
  python loadtests/soak_monitor.py --out results/lab8/soak_resources.csv --duration 3600

Зупинка: Ctrl+C, SIGTERM або --duration. Лише стандартна бібліотека Python.
"""

import argparse
import csv
import json
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SERVICES = ["web1", "web2", "web3", "db", "redis", "nginx"]

UNITS = {
    "b": 1,
    "kb": 1000,
    "kib": 1024,
    "mb": 1000**2,
    "mib": 1024**2,
    "gb": 1000**3,
    "gib": 1024**3,
}

DB_QUERY = (
    "SELECT "
    "(SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()), "
    "pg_database_size(current_database()) / 1048576, "
    "COALESCE((SELECT n_live_tup FROM pg_stat_user_tables WHERE relname = 'sales'), 0)"
)

stop_requested = False


def request_stop(signum, frame):
    global stop_requested
    stop_requested = True


def run(cmd, timeout=30):
    """Запускає команду, повертає stdout або None, якщо вона не вдалась."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def parse_size_mb(text):
    """'84.5MiB' -> 84.5, '1.5GiB' -> 1536.0 (результат у MiB)."""
    match = re.match(r"\s*([\d.]+)\s*([A-Za-z]+)", text or "")
    if not match:
        return None
    unit = UNITS.get(match.group(2).lower())
    if unit is None:
        return None
    return float(match.group(1)) * unit / (1024**2)


def parse_percent(text):
    try:
        return float((text or "").strip().rstrip("%"))
    except ValueError:
        return None


def docker_capacity():
    """Скільки ядер і пам'яті бачить Docker (у Docker Desktop це ВМ, а не весь Mac)."""
    out = run(["docker", "info", "--format", "{{.NCPU}} {{.MemTotal}}"])
    if not out:
        return None, None
    ncpu, mem_total = out.split()
    return int(ncpu), int(mem_total) / (1024**2)


def container_ids():
    """Службу -> повний ID контейнера."""
    ids = {}
    for service in SERVICES:
        out = run(["docker", "compose", "ps", "-q", service])
        if out and out.strip():
            ids[service] = out.strip().splitlines()[0]
    return ids


def container_state(ids):
    """Кількість перезапусків і чи був OOM-kill для кожної служби."""
    state = {}
    for service, cid in ids.items():
        out = run(
            [
                "docker",
                "inspect",
                "-f",
                "{{.RestartCount}} {{.State.OOMKilled}} {{.State.Status}}",
                cid,
            ]
        )
        if out:
            restarts, oom, status = out.split()
            state[service] = {
                "restarts": int(restarts),
                "oom_killed": oom == "true",
                "status": status,
            }
    return state


def sample_stats(ids):
    """CPU (%) і RAM (MiB) кожного контейнера одним викликом docker stats."""
    out = run(
        [
            "docker",
            "stats",
            "--no-stream",
            "--format",
            "{{.ID}}\t{{.CPUPerc}}\t{{.MemUsage}}",
        ],
        timeout=60,
    )
    result = {}
    if not out:
        return result
    for line in out.strip().splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        short_id, cpu, mem = parts
        for service, cid in ids.items():
            if cid.startswith(short_id):
                result[service] = (
                    parse_percent(cpu),
                    parse_size_mb(mem.split("/")[0]),
                )
    return result


def sample_db():
    out = run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "db",
            "sh",
            "-c",
            'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAF, -c "%s"' % DB_QUERY,
        ]
    )
    if not out:
        return None, None, None
    try:
        conns, size_mb, sales = out.strip().split(",")
        return int(conns), float(size_mb), int(sales)
    except ValueError:
        return None, None, None


def sample_redis():
    out = run(
        ["docker", "compose", "exec", "-T", "redis", "redis-cli", "info", "memory"]
    )
    if not out:
        return None
    match = re.search(r"used_memory:(\d+)", out)
    return int(match.group(1)) / (1024**2) if match else None


def fmt(value, digits=1):
    return "" if value is None else round(value, digits)


def write_meta(path, meta):
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="results/lab8/soak_resources.csv")
    parser.add_argument("--interval", type=float, default=10.0, help="секунди")
    parser.add_argument("--duration", type=float, default=0, help="0 = до Ctrl+C")
    parser.add_argument(
        "--meta",
        action="append",
        default=[],
        metavar="КЛЮЧ=ЗНАЧЕННЯ",
        help="додаткові поля для meta.json (наприклад users=400)",
    )
    args = parser.parse_args()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path = out_path.with_suffix(".meta.json")

    ncpu, mem_total_mb = docker_capacity()
    ids = container_ids()
    if not ids:
        sys.exit(
            "Контейнери не знайдено. Запусти з кореня проєкту після docker compose up -d."
        )
    if not ncpu:
        sys.exit("docker info не відповів. Docker запущено?")

    started = datetime.now(timezone.utc)
    meta = {
        "started_at": started.isoformat(),
        "interval_s": args.interval,
        "docker_ncpu": ncpu,
        "docker_mem_total_mb": round(mem_total_mb, 1),
        "services": sorted(ids),
        "state_start": container_state(ids),
    }
    for item in args.meta:
        key, _, value = item.partition("=")
        meta[key] = value
    write_meta(meta_path, meta)

    header = ["elapsed_s", "timestamp"]
    for service in SERVICES:
        header += [f"{service}_cpu_pct", f"{service}_mem_mb"]
    header += [
        "docker_cpu_total_pct",
        "docker_cpu_util_pct",
        "docker_mem_total_mb",
        "docker_mem_util_pct",
        "db_connections",
        "db_size_mb",
        "sales_rows",
        "redis_used_mb",
    ]

    print(f"Пишу в {out_path} кожні {args.interval:g} с. Зупинка: Ctrl+C")
    print(f"Docker бачить {ncpu} ядер і {mem_total_mb / 1024:.1f} ГБ пам'яті.")

    start = time.monotonic()
    rows = 0
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        next_tick = start
        while not stop_requested:
            now = time.monotonic()
            elapsed = now - start
            stats = sample_stats(ids)
            conns, db_size, sales = sample_db()
            redis_mb = sample_redis()

            row = [
                fmt(elapsed),
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ]
            cpu_sum = 0.0
            mem_sum = 0.0
            for service in SERVICES:
                cpu, mem = stats.get(service, (None, None))
                row += [fmt(cpu), fmt(mem)]
                cpu_sum += cpu or 0.0
                mem_sum += mem or 0.0
            row += [
                fmt(cpu_sum),
                fmt(cpu_sum / ncpu),
                fmt(mem_sum),
                fmt(mem_sum / mem_total_mb * 100),
                "" if conns is None else conns,
                fmt(db_size),
                "" if sales is None else sales,
                fmt(redis_mb),
            ]
            writer.writerow(row)
            f.flush()
            rows += 1

            if args.duration and elapsed >= args.duration:
                break
            next_tick += args.interval
            while not stop_requested and time.monotonic() < next_tick:
                time.sleep(min(0.5, max(0.0, next_tick - time.monotonic())))

    meta["finished_at"] = datetime.now(timezone.utc).isoformat()
    meta["samples"] = rows
    meta["state_end"] = container_state(ids)
    write_meta(meta_path, meta)
    print(f"Готово: {rows} вимірів. Метадані: {meta_path}")


if __name__ == "__main__":
    main()
