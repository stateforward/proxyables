# Cross-language benchmark harness

This directory contains the repo-level benchmark runner for `proxyables`.

It mirrors the parity harness topology, but records timing metrics instead of only pass/fail results. The output is a timestamped JSON summary plus a self-contained `report.html` that can be opened directly from disk.

## Usage

Run the full direct benchmark matrix:

```bash
python3 bench/run.py
```

Open the generated standalone report:

```bash
open bench/results/<timestamp>/report.html
```

Run a smaller sweep:

```bash
python3 bench/run.py --langs ts,py,go --pairs ts:py,py:go --iterations 250 --warmup 25
```

Benchmark a single scenario:

```bash
python3 bench/run.py --scenarios CallAdd --iterations 2000 --warmup 200
```

## Outputs

Each run writes a new directory under `bench/results/<timestamp>/`:

- `summary.json`: machine-readable benchmark results
- `report.html`: standalone HTML report with no HTTP server required
- `<pair>/server*.log`, `<pair>/client*.log`: captured process output

## Contract

- Each language parity agent provides `serve` and `bench` modes.
- Benchmarks use the real language runtime as the client and the real language runtime as the server.
- The harness runs every selected client/server pair directly over TCP loopback.
- Results are organized by pair and scenario with latency and throughput metrics.
- The default run is exhaustive across `ts`, `py`, `go`, `rs`, and `zig`, which means 25 direct pairs times every benchmark scenario in [`scenarios.json`](scenarios.json).

## Latest captured run

- [`bench/results/20260326-185624/summary.json`](results/20260326-185624/summary.json)
- [`bench/results/20260326-185624/report.html`](results/20260326-185624/report.html)
