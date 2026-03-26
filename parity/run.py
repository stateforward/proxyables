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
DEFAULT_MULTIHOP_CHAINS: list[tuple[str, ...]] = [
    ("go", "py", "ts", "rs"),
    ("py", "ts", "rs", "zig"),
    ("ts", "go", "py", "zig"),
    ("rs", "zig", "go", "ts"),
    ("zig", "rs", "py", "go"),
]


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
    bridge: list[str]
    drive: list[str]
    env: dict[str, str] | None = None


LANGUAGES: dict[str, LanguageConfig] = {
    "ts": LanguageConfig(
        name="ts",
        workdir=ROOT / "proxyables.ts",
        prepare=[["npm", "run", "build"]],
        serve=["node", "parity/agent.js", "serve"],
        bridge=["node", "parity/agent.js", "bridge"],
        drive=["node", "--expose-gc", "parity/agent.js", "drive"],
    ),
    "py": LanguageConfig(
        name="py",
        workdir=ROOT / "proxyables.py",
        prepare=[],
        serve=["/bin/zsh", "parity/run_agent.sh", "serve"],
        bridge=["/bin/zsh", "parity/run_agent.sh", "bridge"],
        drive=["/bin/zsh", "parity/run_agent.sh", "drive"],
    ),
    "go": LanguageConfig(
        name="go",
        workdir=ROOT / "proxyables.go",
        prepare=[],
        serve=[GO_BIN, "run", "./cmd/parity-agent", "serve"],
        bridge=[GO_BIN, "run", "./cmd/parity-agent", "bridge"],
        drive=[GO_BIN, "run", "./cmd/parity-agent", "drive"],
        env={"GOTOOLCHAIN": "local"},
    ),
    "rs": LanguageConfig(
        name="rs",
        workdir=ROOT / "proxyables.rs",
        prepare=[],
        serve=["cargo", "run", "--quiet", "--bin", "parity_agent", "--", "serve"],
        bridge=["cargo", "run", "--quiet", "--bin", "parity_agent", "--", "bridge"],
        drive=["cargo", "run", "--quiet", "--bin", "parity_agent", "--", "drive"],
    ),
    "zig": LanguageConfig(
        name="zig",
        workdir=ROOT / "proxyables.zig",
        prepare=[],
        serve=["zig", "run", "parity_agent.zig", "--", "serve"],
        bridge=["zig", "run", "parity_agent.zig", "--", "bridge"],
        drive=["zig", "run", "parity_agent.zig", "--", "drive"],
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the cross-language proxyables parity matrix.")
    parser.add_argument("--langs", default="ts,py,go,rs,zig")
    parser.add_argument("--pairs", default="")
    parser.add_argument("--scenarios", default="")
    parser.add_argument("--profile", choices=("functional", "release", "stress", "multihop"), default="functional")
    parser.add_argument("--gc-tier-a-langs", default="ts,py,go")
    parser.add_argument("--chains", default="")
    parser.add_argument("--soak-iterations", type=int, default=32)
    parser.add_argument("--stress-iterations", type=int, default=128)
    parser.add_argument("--payload-bytes", type=int, default=32768)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--cleanup-timeout", type=float, default=5.0)
    parser.add_argument("--disconnect-timeout", type=float, default=5.0)
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


def selected_chains(langs: list[str], raw: str, profile: str) -> list[tuple[str, ...]]:
    if profile != "multihop":
        return []
    if not raw:
        chains = [chain for chain in DEFAULT_MULTIHOP_CHAINS if all(lang in langs for lang in chain)]
        if not chains:
            raise SystemExit("no default multihop chains available for selected languages")
        return chains
    chains: list[tuple[str, ...]] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        chain = tuple(part.strip() for part in item.split(":") if part.strip())
        if len(chain) < 3:
            raise SystemExit(f"invalid multihop chain (need at least 3 langs): {item}")
        for lang in chain:
            if lang not in LANGUAGES:
                raise SystemExit(f"unknown language in chain {item}: {lang}")
        chains.append(chain)
    return chains


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


def launch_agent(
    command: list[str],
    config: LanguageConfig,
    pair_dir: Path,
    prefix: str,
) -> tuple[subprocess.Popen[str], Any, Any]:
    stdout_handle = (pair_dir / f"{prefix}.stdout.log").open("w+", encoding="utf-8")
    stderr_handle = (pair_dir / f"{prefix}.stderr.log").open("w", encoding="utf-8")
    proc = subprocess.Popen(
        command,
        cwd=config.workdir,
        env=merged_env(config.env),
        stdout=subprocess.PIPE,
        stderr=stderr_handle,
        text=True,
    )
    return proc, stdout_handle, stderr_handle


def write_ready(stdout_handle: Any, ready: dict[str, Any]) -> None:
    stdout_handle.write(json.dumps(ready) + "\n")
    stdout_handle.flush()


def expected_actual(scenario: str, soak_iterations: int, payload_bytes: int, concurrency: int) -> Any:
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
    if scenario == "AbruptDisconnectCleanup":
        return {
            "baseline": 0,
            "final": 0,
            "cleaned": True,
        }
    if scenario == "ServerAbortInFlight":
        return {
            "code": "TransportClosed",
        }
    if scenario == "ConcurrentSharedReference":
        return {
            "baseline": 0,
            "final": 0,
            "consistent": True,
            "concurrency": concurrency,
        }
    if scenario == "ConcurrentCallbackFanout":
        return {
            "consistent": True,
            "concurrency": concurrency,
        }
    if scenario == "ReleaseUseRace":
        return {
            "concurrency": 2,
        }
    if scenario == "LargePayloadRoundtrip":
        return {
            "bytes": payload_bytes,
            "ok": True,
        }
    if scenario == "DeepObjectGraph":
        return {
            "label": "deep",
            "answer": 42,
            "echo": "echo deep",
        }
    if scenario == "SlowConsumerBackpressure":
        return {
            "bytes": payload_bytes,
            "ok": True,
            "delayed": True,
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
    if scenario == "AbruptDisconnectCleanup":
        peak = actual.get("peak")
        if not isinstance(peak, int) or peak < 1:
            return "peak did not reflect an acquired remote reference"
        return None
    if scenario == "ServerAbortInFlight":
        message = actual.get("message")
        if not isinstance(message, str) or not message:
            return "transport-closed payload did not include a message"
        return None
    if scenario == "ConcurrentSharedReference":
        peak = actual.get("peak")
        if not isinstance(peak, int) or peak < 1:
            return "peak did not reflect a shared remote reference"
        values = actual.get("values")
        if isinstance(values, list):
            if not isinstance(values, list) or len(values) != expected["concurrency"]:
                return "values did not include the expected concurrency fanout"
            if any(value != "shared" for value in values):
                return "concurrent shared reference values drifted"
        return None
    if scenario == "ConcurrentCallbackFanout":
        values = actual.get("values")
        if isinstance(values, list):
            if not isinstance(values, list) or len(values) != expected["concurrency"]:
                return "callback fanout did not produce the expected number of results"
            if any(value != "callback:value" for value in values):
                return "callback fanout results drifted"
        return None
    if scenario == "ReleaseUseRace":
        outcome = actual.get("outcome")
        if outcome not in {"completed", "transportClosed", "released"}:
            return "race outcome was not canonicalized"
        if outcome != "completed":
            code = actual.get("code")
            if code not in {"TransportClosed", "ReleasedReference"}:
                return "race error did not use a canonical parity code"
        return None
    if scenario in {"LargePayloadRoundtrip", "SlowConsumerBackpressure"}:
        digest = actual.get("digest")
        if not isinstance(digest, str) or len(digest) != 64:
            return "payload result did not include a sha256 digest"
        return None
    return None


def collect_results(
    *,
    args: argparse.Namespace,
    scenarios: list[str],
    scenarios_by_name: dict[str, dict[str, Any]],
    canonical: set[str],
    gc_tier_a_langs: set[str],
    client_lang: str,
    server_lang: str,
    pair_name: str,
    pair_dir: Path,
    server_capabilities: set[str],
    server_protocol: str | None,
    drive_stdout: str,
    trace_expected: list[str] | None = None,
    topology: str = "direct",
    chain: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    client_results = parse_drive_output(drive_stdout)
    result_by_name = {
        canonicalize_scenario(item["scenario"], canonical): item
        for item in client_results
        if item.get("type") == "scenario" and canonicalize_scenario(item.get("scenario", ""), canonical)
    }
    trace_payload = next(
        (item for item in client_results if item.get("type") == "scenario" and item.get("scenario") == "ParityTracePath"),
        None,
    )
    trace_error: str | None = None
    if trace_expected is not None:
        if trace_payload is None:
            trace_error = "missing ParityTracePath result"
        elif trace_payload.get("status") != "passed":
            trace_error = trace_payload.get("message", "ParityTracePath failed")
        else:
            actual_trace = trace_payload.get("actual")
            expected_variants = [trace_expected]
            if len(trace_expected) > 1:
                expected_variants.append(trace_expected[1:])
            if actual_trace not in expected_variants:
                trace_error = f"trace mismatch: expected one of {expected_variants}, got {actual_trace}"

    collected: list[dict[str, Any]] = []
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
            iterations = args.stress_iterations if args.profile == "stress" else args.soak_iterations
            expected = expected_actual(scenario, iterations, args.payload_bytes, args.concurrency)
            actual = payload.get("actual")
            mismatch = validate_actual(scenario, actual, expected) if status == "passed" else None
            if mismatch is not None:
                status = "failed"
                details = {
                    **payload,
                    "message": mismatch,
                    "expected": expected,
                }

        if trace_error and status == "passed":
            status = "failed"
            details = {
                **details,
                "message": trace_error,
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

        entry = {
            "pair": pair_name,
            "client": client_lang,
            "server": server_lang,
            "scenario": scenario,
            "required": required,
            "supported": supported,
            "status": final_status,
            "details": details,
            "artifacts": {
                "client_stdout": str(pair_dir / "client.stdout.log"),
                "client_stderr": str(pair_dir / "client.stderr.log"),
            },
        }
        if topology == "multihop":
            entry["topology"] = "multihop"
            entry["chain"] = list(chain or ())
            entry["source"] = client_lang
            entry["sink"] = server_lang
            entry["artifacts"].update(
                {
                    "sink_stdout": str(pair_dir / "sink.stdout.log"),
                    "sink_stderr": str(pair_dir / "sink.stderr.log"),
                }
            )
            if chain:
                for index, lang in enumerate(chain[1:-1], start=1):
                    entry["artifacts"][f"bridge_{index}_{lang}_stdout"] = str(pair_dir / f"bridge{index}-{lang}.stdout.log")
                    entry["artifacts"][f"bridge_{index}_{lang}_stderr"] = str(pair_dir / f"bridge{index}-{lang}.stderr.log")
        else:
            entry["artifacts"].update(
                {
                    "server_stdout": str(pair_dir / "server.stdout.log"),
                    "server_stderr": str(pair_dir / "server.stderr.log"),
                }
            )
        collected.append(entry)
    return collected


def main() -> int:
    args = parse_args()
    manifest = load_manifest()
    scenarios_by_name = scenario_map(manifest)
    canonical = set(scenarios_by_name.keys())
    langs = selected_languages(args.langs)
    gc_tier_a_langs = set(selected_languages(args.gc_tier_a_langs))
    scenarios = selected_scenarios(manifest, args.scenarios, args.profile)
    pairs = selected_pairs(langs, args.pairs) if args.profile != "multihop" else []
    chains = selected_chains(langs, args.chains, args.profile)

    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
    run_dir = RESULTS_ROOT / timestamp()
    run_dir.mkdir(parents=True, exist_ok=True)

    prepared: set[str] = set()
    results: list[dict[str, Any]] = []

    def ensure_prepared(lang: str) -> None:
        if lang not in prepared:
            run_prepare(LANGUAGES[lang])
            prepared.add(lang)

    def run_drive_command(client_lang: str, host: str, port: int, server_lang: str, drive_scenarios: list[str], pair_dir: Path) -> tuple[str, str] | None:
        client_cfg = LANGUAGES[client_lang]
        drive_cmd = client_cfg.drive + [
            "--host",
            host,
            "--port",
            str(port),
            "--server-lang",
            server_lang,
            "--scenarios",
            ",".join(drive_scenarios),
            "--profile",
            args.profile,
            "--soak-iterations",
            str(args.soak_iterations),
            "--stress-iterations",
            str(args.stress_iterations),
            "--payload-bytes",
            str(args.payload_bytes),
            "--concurrency",
            str(args.concurrency),
            "--cleanup-timeout",
            str(args.cleanup_timeout),
            "--disconnect-timeout",
            str(args.disconnect_timeout),
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
            return None
        (pair_dir / "client.stdout.log").write_text(drive.stdout, encoding="utf-8")
        (pair_dir / "client.stderr.log").write_text(drive.stderr, encoding="utf-8")
        return drive.stdout, drive.stderr

    if args.profile == "multihop":
        for chain in chains:
            for lang in chain:
                ensure_prepared(lang)
            client_lang = chain[0]
            server_lang = chain[-1]
            pair_name = "-to-".join(chain)
            pair_dir = run_dir / pair_name
            pair_dir.mkdir(parents=True, exist_ok=True)

            launched: list[tuple[subprocess.Popen[str], Any, Any]] = []
            server_capabilities: set[str] = set()
            server_protocol: str | None = None
            downstream_port: int | None = None
            try:
                sink_cfg = LANGUAGES[server_lang]
                sink_proc, sink_stdout, sink_stderr = launch_agent(sink_cfg.serve, sink_cfg, pair_dir, "sink")
                launched.append((sink_proc, sink_stdout, sink_stderr))
                sink_ready = wait_for_ready(sink_proc, args.timeout)
                write_ready(sink_stdout, sink_ready)
                downstream_port = int(sink_ready["port"])
                server_capabilities = set(sink_ready.get("capabilities", []))
                server_protocol = sink_ready.get("protocol")

                for index, bridge_lang in enumerate(reversed(chain[1:-1]), start=1):
                    bridge_cfg = LANGUAGES[bridge_lang]
                    bridge_cmd = bridge_cfg.bridge + [
                        "--upstream-host",
                        "127.0.0.1",
                        "--upstream-port",
                        str(downstream_port),
                        "--upstream-lang",
                        server_lang,
                    ]
                    prefix = f"bridge{len(chain) - index - 1}-{bridge_lang}"
                    bridge_proc, bridge_stdout, bridge_stderr = launch_agent(bridge_cmd, bridge_cfg, pair_dir, prefix)
                    launched.append((bridge_proc, bridge_stdout, bridge_stderr))
                    bridge_ready = wait_for_ready(bridge_proc, args.timeout)
                    write_ready(bridge_stdout, bridge_ready)
                    downstream_port = int(bridge_ready["port"])

                drive_output = run_drive_command(
                    client_lang,
                    "127.0.0.1",
                    downstream_port or 0,
                    server_lang,
                    ["ParityTracePath", *scenarios],
                    pair_dir,
                )
                if drive_output is None:
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
                                "topology": "multihop",
                                "chain": list(chain),
                                "source": client_lang,
                                "sink": server_lang,
                                "artifacts": {
                                    "client_stdout": str(pair_dir / "client.stdout.log"),
                                    "client_stderr": str(pair_dir / "client.stderr.log"),
                                    "sink_stdout": str(pair_dir / "sink.stdout.log"),
                                    "sink_stderr": str(pair_dir / "sink.stderr.log"),
                                },
                            }
                        )
                    continue
                drive_stdout, _ = drive_output
                results.extend(
                    collect_results(
                        args=args,
                        scenarios=scenarios,
                        scenarios_by_name=scenarios_by_name,
                        canonical=canonical,
                        gc_tier_a_langs=gc_tier_a_langs,
                        client_lang=client_lang,
                        server_lang=server_lang,
                        pair_name=pair_name,
                        pair_dir=pair_dir,
                        server_capabilities=server_capabilities,
                        server_protocol=server_protocol,
                        drive_stdout=drive_stdout,
                        trace_expected=list(chain),
                        topology="multihop",
                        chain=chain,
                    )
                )
            finally:
                for proc, stdout_handle, stderr_handle in reversed(launched):
                    terminate_process(proc)
                    stdout_handle.close()
                    stderr_handle.close()
    else:
        for client_lang, server_lang in pairs:
            server_cfg = LANGUAGES[server_lang]
            ensure_prepared(server_lang)
            ensure_prepared(client_lang)

            pair_name = f"{client_lang}-to-{server_lang}"
            pair_dir = run_dir / pair_name
            pair_dir.mkdir(parents=True, exist_ok=True)
            server_proc, server_stdout, server_stderr = launch_agent(server_cfg.serve, server_cfg, pair_dir, "server")
            try:
                ready = wait_for_ready(server_proc, args.timeout)
                write_ready(server_stdout, ready)
                server_capabilities = set(ready.get("capabilities", []))
                server_protocol = ready.get("protocol")
                drive_output = run_drive_command(client_lang, "127.0.0.1", int(ready["port"]), server_lang, scenarios, pair_dir)
                if drive_output is None:
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
                drive_stdout, _ = drive_output
                results.extend(
                    collect_results(
                        args=args,
                        scenarios=scenarios,
                        scenarios_by_name=scenarios_by_name,
                        canonical=canonical,
                        gc_tier_a_langs=gc_tier_a_langs,
                        client_lang=client_lang,
                        server_lang=server_lang,
                        pair_name=pair_name,
                        pair_dir=pair_dir,
                        server_capabilities=server_capabilities,
                        server_protocol=server_protocol,
                        drive_stdout=drive_stdout,
                    )
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
        "stress_iterations": args.stress_iterations,
        "payload_bytes": args.payload_bytes,
        "concurrency": args.concurrency,
        "cleanup_timeout": args.cleanup_timeout,
        "disconnect_timeout": args.disconnect_timeout,
        "chains": [list(chain) for chain in chains],
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
