#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SCENARIO_FILE = ROOT / "parity" / "scenarios.json"
RESULTS_ROOT = ROOT / "parity" / "results"


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


def canonicalize_scenario(raw: str, canonical_names: set[str]) -> str:
    if not raw:
        return ""
    candidates = {raw, raw.strip(), to_pascal_case(raw), to_pascal_case(raw.strip())}
    for candidate in candidates:
        if candidate in canonical_names:
            return candidate
    return ""
@dataclass
class LanguageConfig:
    name: str
    workdir: Path
    prepare: list[list[str]]
    serve: list[str]
    drive: list[str]
    env: dict[str, str] | None = None


LANGUAGES: dict[str, LanguageConfig] = {
    "ts": LanguageConfig(
        name="ts",
        workdir=ROOT / "proxyables.ts",
        prepare=[["npm", "run", "build"]],
        serve=["node", "parity/agent.js", "serve"],
        drive=["node", "--expose-gc", "parity/agent.js", "drive"],
    ),
    "py": LanguageConfig(
        name="py",
        workdir=ROOT / "proxyables.py",
        prepare=[],
        serve=["/bin/zsh", "parity/run_agent.sh", "serve"],
        drive=["/bin/zsh", "parity/run_agent.sh", "drive"],
    ),
    "go": LanguageConfig(
        name="go",
        workdir=ROOT / "proxyables.go",
        prepare=[],
        serve=[GO_BIN, "run", "./cmd/parity-agent", "serve"],
        drive=[GO_BIN, "run", "./cmd/parity-agent", "drive"],
        env={"GOTOOLCHAIN": "local"},
    ),
    "rs": LanguageConfig(
        name="rs",
        workdir=ROOT / "proxyables.rs",
        prepare=[],
        serve=["cargo", "run", "--quiet", "--bin", "parity_agent", "--", "serve"],
        drive=["cargo", "run", "--quiet", "--bin", "parity_agent", "--", "drive"],
    ),
    "zig": LanguageConfig(
        name="zig",
        workdir=ROOT / "proxyables.zig",
        prepare=[],
        serve=["zig", "run", "parity_agent.zig", "--", "serve"],
        drive=["zig", "run", "parity_agent.zig", "--", "drive"],
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the cross-language proxyables parity matrix.")
    parser.add_argument("--langs", default="ts,py,go,rs,zig")
    parser.add_argument("--pairs", default="")
    parser.add_argument("--scenarios", default="")
    parser.add_argument("--profile", choices=("functional", "release"), default="functional")
    parser.add_argument("--gc-tier-a-langs", default="ts,py,go")
    parser.add_argument("--soak-iterations", type=int, default=32)
    parser.add_argument("--cleanup-timeout", type=float, default=5.0)
    parser.add_argument("--allow-unsupported", action="store_true")
    parser.add_argument("--keep-artifacts", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser.parse_args()


def load_manifest() -> dict[str, Any]:
    with SCENARIO_FILE.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def scenario_map(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["name"]: item for item in manifest["scenarios"]}


def canonical_scenarios(manifest: dict[str, Any]) -> list[str]:
    return [item["name"] for item in manifest["scenarios"]]


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


def selected_scenarios(manifest: dict[str, Any], raw: str, profile: str) -> list[str]:
    canonical_names = [
        item["name"]
        for item in manifest["scenarios"]
        if profile in item.get("profiles", ["functional", "release"])
    ]
    canonical_set = set(canonical_names)
    if not raw:
        return canonical_names
    selected = [item.strip() for item in raw.split(",") if item.strip()]
    canonicalized = [canonicalize_scenario(name, canonical_set) for name in selected]
    unknown = {
        raw
        for raw, canonical in zip(selected, canonicalized)
        if not canonical
    }
    if unknown:
        raise SystemExit(f"unknown scenarios: {', '.join(sorted(unknown))}")
    # Preserve manifest ordering from canonical list so matrix output is stable.
    return canonicalized


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


def parse_drive_output(raw: str) -> list[dict[str, Any]]:
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


def expected_actual(scenario: str, soak_iterations: int) -> Any:
    if scenario == "GetScalars":
        return {
            "intValue": 42,
            "boolValue": True,
            "stringValue": "hello",
            "nullValue": None,
        }
    if scenario == "CallAdd":
        return 42
    if scenario == "NestedObjectAccess":
        return {"label": "nested", "pong": "pong"}
    if scenario == "ConstructGreeter":
        return "Hello World"
    if scenario == "CallbackRoundtrip":
        return "callback:value"
    if scenario == "ObjectArgumentRoundtrip":
        return "helper:Ada"
    if scenario == "ErrorPropagation":
        return "Boom"
    if scenario == "SharedReferenceConsistency":
        return {
            "firstKind": "shared",
            "secondKind": "shared",
            "firstValue": "shared",
            "secondValue": "shared",
        }
    if scenario == "ExplicitRelease":
        return {
            "before": 0,
            "after": 0,
            "acquired": 2,
        }
    if scenario == "AliasRetainRelease":
        return {
            "baseline": 0,
            "final": 0,
            "released": True,
        }
    if scenario == "UseAfterRelease":
        return {
            "baseline": 0,
            "final": 0,
            "released": True,
        }
    if scenario == "SessionCloseCleanup":
        return {
            "baseline": 0,
            "final": 0,
            "cleaned": True,
        }
    if scenario == "ErrorPathNoLeak":
        return {
            "baseline": 0,
            "final": 0,
            "error": "Boom",
            "cleaned": True,
        }
    if scenario == "ReferenceChurnSoak":
        return {
            "baseline": 0,
            "final": 0,
            "iterations": soak_iterations,
            "stable": True,
        }
    if scenario == "AutomaticReleaseAfterDrop":
        return {
            "baseline": 0,
            "final": 0,
            "released": True,
            "eventual": True,
        }
    if scenario == "CallbackReferenceCleanup":
        return {
            "baseline": 0,
            "final": 0,
            "released": True,
        }
    if scenario == "FinalizerEventualCleanup":
        return {
            "baseline": 0,
            "final": 0,
            "released": True,
            "eventual": True,
        }
    return None


def validate_actual(scenario: str, actual: Any, expected: Any) -> str | None:
    if expected is None:
        return None
    if scenario in {
        "GetScalars",
        "CallAdd",
        "NestedObjectAccess",
        "ConstructGreeter",
        "CallbackRoundtrip",
        "ObjectArgumentRoundtrip",
        "ErrorPropagation",
        "SharedReferenceConsistency",
        "ExplicitRelease",
    }:
        if actual != expected:
            return "actual did not match expected"
        return None
    if not isinstance(actual, dict):
        return "actual did not return an object payload"
    for key, value in expected.items():
        if actual.get(key) != value:
            return f"field {key} did not match expected"
    if scenario == "ReferenceChurnSoak":
        peak = actual.get("peak")
        iterations = actual.get("iterations")
        if not isinstance(peak, int) or not isinstance(iterations, int):
            return "peak/iterations were not numeric"
        if peak < iterations:
            return "peak was lower than iterations"
        return None
    if scenario in {"AutomaticReleaseAfterDrop", "FinalizerEventualCleanup"}:
        peak = actual.get("peak")
        if not isinstance(peak, int) or peak < 1:
            return "peak did not reflect an acquired remote reference"
        return None
    if scenario == "CallbackReferenceCleanup":
        peak = actual.get("peak")
        if not isinstance(peak, int) or peak < 1:
            return "peak did not reflect callback/object temp references"
        return None
    if scenario in {"AliasRetainRelease", "UseAfterRelease", "SessionCloseCleanup", "ErrorPathNoLeak"}:
        peak = actual.get("peak")
        if not isinstance(peak, int) or peak < 1:
            return "peak did not reflect any retained references"
        if scenario == "AliasRetainRelease":
            after_first = actual.get("afterFirstRelease")
            if not isinstance(after_first, int) or after_first < 0 or after_first > peak:
                return "afterFirstRelease was not a valid retained count"
        return None
    return None


def main() -> int:
    args = parse_args()
    manifest = load_manifest()
    scenarios_by_name = scenario_map(manifest)
    canonical = set(scenarios_by_name.keys())
    langs = selected_languages(args.langs)
    gc_tier_a_langs = set(selected_languages(args.gc_tier_a_langs))
    pairs = selected_pairs(langs, args.pairs)
    scenarios = selected_scenarios(manifest, args.scenarios, args.profile)

    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
    run_dir = RESULTS_ROOT / timestamp()
    run_dir.mkdir(parents=True, exist_ok=True)

    prepared: set[str] = set()
    results: list[dict[str, Any]] = []

    for client_lang, server_lang in pairs:
        server_cfg = LANGUAGES[server_lang]
        client_cfg = LANGUAGES[client_lang]

        if server_lang not in prepared:
            run_prepare(server_cfg)
            prepared.add(server_lang)
        if client_lang not in prepared:
            run_prepare(client_cfg)
            prepared.add(client_lang)

        pair_name = f"{client_lang}-to-{server_lang}"
        pair_dir = run_dir / pair_name
        pair_dir.mkdir(parents=True, exist_ok=True)
        server_stdout = (pair_dir / "server.stdout.log").open("w+", encoding="utf-8")
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
            server_stdout.write(json.dumps(ready) + "\n")
            server_stdout.flush()
            server_capabilities = set(ready.get("capabilities", []))
            server_protocol = ready.get("protocol")

            drive_cmd = client_cfg.drive + [
                "--host",
                "127.0.0.1",
                "--port",
                str(ready["port"]),
                "--server-lang",
                server_lang,
                "--scenarios",
                ",".join(scenarios),
                "--profile",
                args.profile,
                "--soak-iterations",
                str(args.soak_iterations),
                "--cleanup-timeout",
                str(args.cleanup_timeout),
            ]
            try:
                drive = subprocess.run(
                    drive_cmd,
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
                            "required": bool(scenarios_by_name[scenario].get("required", False)),
                            "supported": False,
                            "status": "failed",
                            "details": {"message": "client drive timed out"},
                            "artifacts": {
                                "server_stdout": str(pair_dir / "server.stdout.log"),
                                "server_stderr": str(pair_dir / "server.stderr.log"),
                                "client_stdout": str(pair_dir / "client.stdout.log"),
                                "client_stderr": str(pair_dir / "client.stderr.log"),
                            },
                        }
                    )
                continue
            (pair_dir / "client.stdout.log").write_text(drive.stdout, encoding="utf-8")
            (pair_dir / "client.stderr.log").write_text(drive.stderr, encoding="utf-8")

            client_results = parse_drive_output(drive.stdout)
            result_by_name = {
                canonicalize_scenario(item["scenario"], canonical): item
                for item in client_results
                if item.get("type") == "scenario" and canonicalize_scenario(item.get("scenario", ""), canonical)
            }

            for scenario in scenarios:
                manifest_item = scenarios_by_name[scenario]
                gc_tier_a_only = bool(manifest_item.get("gc_tier_a_only", False))
                required = bool(manifest_item.get("required", False))
                if gc_tier_a_only and client_lang not in gc_tier_a_langs:
                    required = False
                payload = result_by_name.get(scenario)
                if payload is None:
                    status = "failed"
                    details = {"message": "client did not emit a result"}
                else:
                    status = payload.get("status", "failed")
                    details = payload
                    expected = expected_actual(scenario, args.soak_iterations)
                    actual = payload.get("actual")
                    mismatch = validate_actual(scenario, actual, expected) if status == "passed" else None
                    if mismatch is not None:
                        status = "failed"
                        details = {
                            **payload,
                            "message": mismatch,
                            "expected": expected,
                        }

                protocol = details.get("protocol", server_protocol)
                supported = scenario in server_capabilities and status != "unsupported"
                if server_protocol and protocol and server_protocol != protocol:
                    status = "unsupported"
                    details = {
                        "message": f"protocol mismatch: client={protocol} server={server_protocol}",
                    }
                    supported = False

                if status == "unsupported" and args.allow_unsupported:
                    final_status = "skipped"
                elif status == "unsupported" and gc_tier_a_only and client_lang not in gc_tier_a_langs:
                    final_status = "skipped"
                elif status == "unsupported" and not required:
                    final_status = "skipped"
                else:
                    final_status = status

                results.append(
                    {
                        "pair": pair_name,
                        "client": client_lang,
                        "server": server_lang,
                        "scenario": scenario,
                        "required": required,
                        "supported": supported,
                        "status": final_status,
                        "details": details,
                        "artifacts": {
                            "server_stdout": str(pair_dir / "server.stdout.log"),
                            "server_stderr": str(pair_dir / "server.stderr.log"),
                            "client_stdout": str(pair_dir / "client.stdout.log"),
                            "client_stderr": str(pair_dir / "client.stderr.log"),
                        },
                    }
                )
        finally:
            terminate_process(server_proc)
            server_stdout.close()
            server_stderr.close()

    summary = {
        "run_dir": str(run_dir),
        "profile": args.profile,
        "gc_tier_a_langs": sorted(gc_tier_a_langs),
        "soak_iterations": args.soak_iterations,
        "cleanup_timeout": args.cleanup_timeout,
        "results": results,
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    failed = [item for item in results if item["status"] not in {"passed", "skipped"}]
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        for item in results:
            print(f"{item['pair']:>14}  {item['scenario']:<28}  {item['status']}")
        print(f"\nsummary: {len(results) - len(failed)} ok, {len(failed)} failed, run_dir={run_dir}")

    if failed:
        return 1
    if not args.keep_artifacts:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
