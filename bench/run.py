#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SCENARIO_FILE = ROOT / "bench" / "scenarios.json"
RESULTS_ROOT = ROOT / "bench" / "results"


def resolve_go_bin() -> str:
    configured = os.environ.get("GO_BIN")
    if configured:
        return configured
    local = Path("/opt/homebrew/Cellar/go/1.24.5/bin/go")
    if local.exists():
        return str(local)
    return "go"


GO_BIN = resolve_go_bin()
WORD_BOUNDARY = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])")


def to_pascal_case(value: str) -> str:
    parts = WORD_BOUNDARY.findall(value)
    if not parts:
        return "".join(
            segment.capitalize()
            for segment in re.split(r"[^a-zA-Z0-9]+", value)
            if segment
        )
    return "".join(part[:1].upper() + part[1:].lower() for part in parts)


@dataclass
class LanguageConfig:
    name: str
    workdir: Path
    prepare: list[list[str]]
    serve: list[str]
    bench: list[str]
    env: dict[str, str] | None = None


LANGUAGES: dict[str, LanguageConfig] = {
    "ts": LanguageConfig(
        name="ts",
        workdir=ROOT / "proxyables.ts",
        prepare=[["npm", "run", "build"]],
        serve=["node", "parity/agent.js", "serve"],
        bench=["node", "parity/agent.js", "bench"],
    ),
    "py": LanguageConfig(
        name="py",
        workdir=ROOT / "proxyables.py",
        prepare=[],
        serve=["/bin/zsh", "parity/run_agent.sh", "serve"],
        bench=["/bin/zsh", "parity/run_agent.sh", "bench"],
    ),
    "go": LanguageConfig(
        name="go",
        workdir=ROOT / "proxyables.go",
        prepare=[],
        serve=[GO_BIN, "run", "./cmd/parity-agent", "serve"],
        bench=[GO_BIN, "run", "./cmd/parity-agent", "bench"],
        env={"GOTOOLCHAIN": "local"},
    ),
    "rs": LanguageConfig(
        name="rs",
        workdir=ROOT / "proxyables.rs",
        prepare=[],
        serve=["cargo", "run", "--quiet", "--bin", "parity_agent", "--", "serve"],
        bench=["cargo", "run", "--quiet", "--bin", "parity_agent", "--", "bench"],
    ),
    "zig": LanguageConfig(
        name="zig",
        workdir=ROOT / "proxyables.zig",
        prepare=[],
        serve=["zig", "run", "parity_agent.zig", "--", "serve"],
        bench=["zig", "run", "parity_agent.zig", "--", "bench"],
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the cross-language proxyables benchmark matrix.")
    parser.add_argument("--langs", default="ts,py,go,rs,zig")
    parser.add_argument("--pairs", default="")
    parser.add_argument("--scenarios", default="")
    parser.add_argument("--iterations", type=int, default=1000)
    parser.add_argument("--warmup", type=int, default=100)
    parser.add_argument("--payload-bytes", type=int, default=32768)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def load_manifest() -> dict[str, Any]:
    with SCENARIO_FILE.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def scenario_map(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["name"]: item for item in manifest["scenarios"]}


def selected_languages(raw: str) -> list[str]:
    langs = [item.strip() for item in raw.split(",") if item.strip()]
    for lang in langs:
        if lang not in LANGUAGES:
            raise SystemExit(f"unknown language: {lang}")
    return langs


def selected_pairs(langs: list[str], raw: str) -> list[tuple[str, str]]:
    if not raw:
        return [(client, server) for client, server in product(langs, langs)]
    pairs: list[tuple[str, str]] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        client, server = item.split(":")
        pairs.append((client, server))
    return pairs


def selected_scenarios(manifest: dict[str, Any], raw: str) -> list[str]:
    canonical = [item["name"] for item in manifest["scenarios"]]
    canonical_set = set(canonical)
    if not raw:
        return canonical
    out: list[str] = []
    unknown: list[str] = []
    for item in [part.strip() for part in raw.split(",") if part.strip()]:
        candidate = item if item in canonical_set else to_pascal_case(item)
        if candidate not in canonical_set:
            unknown.append(item)
            continue
        out.append(candidate)
    if unknown:
        raise SystemExit(f"unknown scenarios: {', '.join(sorted(unknown))}")
    return out


def merged_env(extra: dict[str, str] | None) -> dict[str, str]:
    env = os.environ.copy()
    if extra:
        env.update(extra)
    return env


def run_prepare(config: LanguageConfig) -> None:
    if config.name == "py":
        venv_python = config.workdir / ".venv" / "bin" / "python"
        if not venv_python.exists():
            subprocess.run(["python3", "-m", "venv", ".venv"], cwd=config.workdir, check=True)
        subprocess.run([str(venv_python), "-m", "pip", "install", "msgpack>=1.1.0"], cwd=config.workdir, check=True)
    for command in config.prepare:
        subprocess.run(command, cwd=config.workdir, env=merged_env(config.env), check=True)


def timestamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def wait_for_ready(proc: subprocess.Popen[str], timeout: float) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                raise RuntimeError("server exited before announcing readiness")
            time.sleep(0.05)
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("type") == "ready":
            return payload
    raise TimeoutError("timed out waiting for ready line")


def terminate_process(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def parse_client_output(raw: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return items


def percentile(samples: list[float], fraction: float) -> float:
    if not samples:
        return 0.0
    index = max(0, min(len(samples) - 1, int(round((len(samples) - 1) * fraction))))
    ordered = sorted(samples)
    return ordered[index]


def validate_metrics(item: dict[str, Any]) -> str | None:
    metrics = item.get("metrics")
    if not isinstance(metrics, dict):
        return "missing metrics payload"
    for key in ("totalMs", "avgMs", "ops", "p50Ms", "p95Ms", "minMs", "maxMs"):
        value = metrics.get(key)
        if not isinstance(value, (int, float)) or value < 0:
            return f"invalid metric: {key}"
    if metrics["ops"] <= 0:
        return "ops/sec was not positive"
    return None


def render_html(summary: dict[str, Any]) -> str:
    results = summary["results"]
    scenarios = summary["scenarios"]
    langs = summary["langs"]

    scenario_tables: list[str] = []
    for scenario in scenarios:
        rows = [item for item in results if item["scenario"] == scenario and item["status"] == "passed"]
        avg_values = [item["metrics"]["avgMs"] for item in rows]
        if avg_values:
            low = min(avg_values)
            high = max(avg_values)
        else:
            low = high = 0.0

        header = "".join(f"<th>{html.escape(lang)}</th>" for lang in langs)
        body_rows: list[str] = []
        for client in langs:
            cells = []
            for server in langs:
                match = next((item for item in rows if item["client"] == client and item["server"] == server), None)
                if not match:
                    cells.append("<td class='missing'>missing</td>")
                    continue
                avg_ms = match["metrics"]["avgMs"]
                ops = match["metrics"]["ops"]
                ratio = 0.0 if high <= low else (avg_ms - low) / (high - low)
                red = int(226 - (ratio * 70))
                green = int(248 - (ratio * 120))
                blue = int(232 - (ratio * 150))
                color = f"rgb({red}, {green}, {blue})"
                cells.append(
                    "<td style='background:{color}'><div>{avg:.4f} ms</div><div class='sub'>{ops:.1f} ops/s</div></td>".format(
                        color=color,
                        avg=avg_ms,
                        ops=ops,
                    )
                )
            body_rows.append(f"<tr><th>{html.escape(client)}</th>{''.join(cells)}</tr>")
        scenario_tables.append(
            "<section><h2>{name}</h2><p>{desc}</p><table><thead><tr><th>Client \\ Server</th>{header}</tr></thead>"
            "<tbody>{rows}</tbody></table></section>".format(
                name=html.escape(scenario),
                desc=html.escape(summary["scenario_descriptions"][scenario]),
                header=header,
                rows="".join(body_rows),
            )
        )

    flat_rows = []
    for item in sorted(results, key=lambda entry: (entry["scenario"], entry["client"], entry["server"])):
        if item["status"] != "passed":
            flat_rows.append(
                "<tr><td>{pair}</td><td>{scenario}</td><td colspan='6' class='missing'>{message}</td></tr>".format(
                    pair=html.escape(item["pair"]),
                    scenario=html.escape(item["scenario"]),
                    message=html.escape(item["details"].get("message", item["status"])),
                )
            )
            continue
        metrics = item["metrics"]
        flat_rows.append(
            "<tr><td>{pair}</td><td>{scenario}</td><td>{avg:.4f}</td><td>{p50:.4f}</td><td>{p95:.4f}</td><td>{ops:.1f}</td><td>{iters}</td><td>{warmup}</td></tr>".format(
                pair=html.escape(item["pair"]),
                scenario=html.escape(item["scenario"]),
                avg=metrics["avgMs"],
                p50=metrics["p50Ms"],
                p95=metrics["p95Ms"],
                ops=metrics["ops"],
                iters=item["iterations"],
                warmup=item["warmup"],
            )
        )

    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Proxyables Benchmark Report</title>
  <style>
    body {{ font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: #182026; background: #f8faf7; }}
    h1, h2 {{ margin: 0 0 12px; }}
    p {{ margin: 0 0 16px; line-height: 1.5; }}
    section {{ margin: 24px 0 32px; }}
    table {{ border-collapse: collapse; width: 100%; background: white; }}
    th, td {{ border: 1px solid #d7dfd4; padding: 10px 12px; text-align: left; vertical-align: top; }}
    th {{ background: #edf3ea; }}
    .sub {{ color: #4d6355; font-size: 12px; margin-top: 4px; }}
    .missing {{ color: #7b3c3c; background: #f9e6e6; }}
    .meta {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin: 20px 0 28px; }}
    .card {{ background: white; border: 1px solid #d7dfd4; padding: 14px; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
  </style>
</head>
<body>
  <h1>Proxyables Benchmark Report</h1>
  <p>Generated benchmark matrix across {pair_count} direct client/server pairs and {scenario_count} benchmark scenarios per pair.</p>
  <div class="meta">
    <div class="card"><strong>Run</strong><div class="sub">{run_dir}</div></div>
    <div class="card"><strong>Iterations</strong><div class="sub">{iterations}</div></div>
    <div class="card"><strong>Warmup</strong><div class="sub">{warmup}</div></div>
    <div class="card"><strong>Payload</strong><div class="sub">{payload_bytes} bytes</div></div>
  </div>
  <section>
    <h2>Scenario Heatmaps</h2>
    <p>Cells show average latency and throughput for each direct language pair. Lower latency is greener.</p>
    {scenario_tables}
  </section>
  <section>
    <h2>All Results</h2>
    <table>
      <thead>
        <tr>
          <th>Pair</th>
          <th>Scenario</th>
          <th>Avg ms</th>
          <th>P50 ms</th>
          <th>P95 ms</th>
          <th>Ops/s</th>
          <th>Iterations</th>
          <th>Warmup</th>
        </tr>
      </thead>
      <tbody>
        {flat_rows}
      </tbody>
    </table>
  </section>
</body>
</html>
""".format(
        pair_count=len(summary["pairs"]),
        scenario_count=len(scenarios),
        run_dir=html.escape(summary["run_dir"]),
        iterations=summary["iterations"],
        warmup=summary["warmup"],
        payload_bytes=summary["payload_bytes"],
        scenario_tables="".join(scenario_tables),
        flat_rows="".join(flat_rows),
    )


def main() -> int:
    args = parse_args()
    manifest = load_manifest()
    scenarios_by_name = scenario_map(manifest)
    langs = selected_languages(args.langs)
    pairs = selected_pairs(langs, args.pairs)
    scenarios = selected_scenarios(manifest, args.scenarios)

    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
    run_dir = RESULTS_ROOT / timestamp()
    run_dir.mkdir(parents=True, exist_ok=True)

    prepared: set[str] = set()

    def ensure_prepared(lang: str) -> None:
        if lang not in prepared:
            run_prepare(LANGUAGES[lang])
            prepared.add(lang)

    results: list[dict[str, Any]] = []

    for client_lang, server_lang in pairs:
        ensure_prepared(client_lang)
        ensure_prepared(server_lang)
        pair_name = f"{client_lang}-to-{server_lang}"
        pair_dir = run_dir / pair_name
        pair_dir.mkdir(parents=True, exist_ok=True)

        server_cfg = LANGUAGES[server_lang]
        server_stderr = (pair_dir / "server.stderr.log").open("w", encoding="utf-8")
        server_proc = subprocess.Popen(
            server_cfg.serve,
            cwd=server_cfg.workdir,
            env=merged_env(server_cfg.env),
            stdout=subprocess.PIPE,
            stderr=server_stderr,
            text=True,
        )
        try:
            ready = wait_for_ready(server_proc, args.timeout)
            (pair_dir / "server.stdout.log").write_text(json.dumps(ready) + "\n", encoding="utf-8")

            client_cfg = LANGUAGES[client_lang]
            bench_cmd = client_cfg.bench + [
                "--host",
                "127.0.0.1",
                "--port",
                str(int(ready["port"])),
                "--server-lang",
                server_lang,
                "--scenarios",
                ",".join(scenarios),
                "--iterations",
                str(args.iterations),
                "--warmup",
                str(args.warmup),
                "--payload-bytes",
                str(args.payload_bytes),
            ]
            try:
                client = subprocess.run(
                    bench_cmd,
                    cwd=client_cfg.workdir,
                    env=merged_env(client_cfg.env),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=args.timeout,
                )
            except subprocess.TimeoutExpired as error:
                stdout = error.stdout.decode("utf-8", errors="replace") if isinstance(error.stdout, bytes) else (error.stdout or "")
                stderr = error.stderr.decode("utf-8", errors="replace") if isinstance(error.stderr, bytes) else (error.stderr or "")
                (pair_dir / "client.stdout.log").write_text(stdout, encoding="utf-8")
                (pair_dir / "client.stderr.log").write_text(stderr + "\nTIMEOUT\n", encoding="utf-8")
                for scenario in scenarios:
                    results.append(
                        {
                            "pair": pair_name,
                            "client": client_lang,
                            "server": server_lang,
                            "scenario": scenario,
                            "status": "failed",
                            "iterations": args.iterations,
                            "warmup": args.warmup,
                            "details": {"message": "benchmark client timed out"},
                        }
                    )
                continue

            (pair_dir / "client.stdout.log").write_text(client.stdout, encoding="utf-8")
            (pair_dir / "client.stderr.log").write_text(client.stderr, encoding="utf-8")

            items = parse_client_output(client.stdout)
            by_name = {
                item["scenario"]: item
                for item in items
                if item.get("type") == "benchmark" and item.get("scenario")
            }
            for scenario in scenarios:
                item = by_name.get(scenario)
                if item is None:
                    results.append(
                        {
                            "pair": pair_name,
                            "client": client_lang,
                            "server": server_lang,
                            "scenario": scenario,
                            "status": "failed",
                            "iterations": args.iterations,
                            "warmup": args.warmup,
                            "details": {"message": "client did not emit a benchmark result"},
                        }
                    )
                    continue
                status = item.get("status", "failed")
                details = dict(item)
                if status == "passed":
                    mismatch = validate_metrics(item)
                    if mismatch is not None:
                        status = "failed"
                        details["message"] = mismatch
                results.append(
                    {
                        "pair": pair_name,
                        "client": client_lang,
                        "server": server_lang,
                        "scenario": scenario,
                        "status": status,
                        "iterations": int(item.get("iterations", args.iterations)),
                        "warmup": int(item.get("warmup", args.warmup)),
                        "metrics": item.get("metrics"),
                        "details": details,
                    }
                )
        finally:
            terminate_process(server_proc)
            server_stderr.close()

    summary = {
        "run_dir": str(run_dir),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "langs": langs,
        "pairs": [f"{client}:{server}" for client, server in pairs],
        "scenarios": scenarios,
        "scenario_descriptions": {name: scenarios_by_name[name]["description"] for name in scenarios},
        "iterations": args.iterations,
        "warmup": args.warmup,
        "payload_bytes": args.payload_bytes,
        "results": results,
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (run_dir / "report.html").write_text(render_html(summary), encoding="utf-8")

    failed = [item for item in results if item["status"] != "passed"]
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        for item in results:
            if item["status"] == "passed":
                avg_ms = item["metrics"]["avgMs"]
                ops = item["metrics"]["ops"]
                print(f"{item['pair']:>12}  {item['scenario']:<28}  {avg_ms:>9.4f} ms  {ops:>10.1f} ops/s")
            else:
                print(f"{item['pair']:>12}  {item['scenario']:<28}  failed")
        print(f"\nsummary: {len(results) - len(failed)} ok, {len(failed)} failed, run_dir={run_dir}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
