# Cross-language parity harness

This directory contains the repo-level parity runner for `proxyables`.

## Usage

Run the full matrix:

```bash
python3 parity/run.py
```

Run the full release gate:

```bash
python3 parity/run.py --profile release
```

Run the curated multihop chains:

```bash
python3 parity/run.py --profile multihop
python3 parity/run.py --profile multihop --chains go:py:ts:rs
```

Run a subset (canonical `PascalCase` IDs, legacy aliases also accepted):

```bash
python3 parity/run.py --langs ts,py,go --pairs ts:py,py:go --scenarios GetScalars,CallAdd
```

Keep unsupported pairs as informational instead of failing:

```bash
python3 parity/run.py --allow-unsupported
```

## Contract

- Each language provides a parity agent with `serve` and `drive` modes.
- Multihop chains add a `bridge` mode that imports upstream and re-exports downstream over the real runtime path.
- Agents communicate readiness and scenario results over stdout as JSONL.
- The runner uses real TCP loopback sockets for the data plane.
- Protocol mismatches and missing capabilities are reported explicitly per pair and scenario.
- The agent protocol aligns with the shared constants and behaviors in [DSL.md](../DSL.md).
