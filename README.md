# proxyables

**Remote objects that behave like local ones. No schemas, codegen, or stubs.**

Proxyables is a cross-language object RPC system. Instead of designing endpoints and generating clients, you export an object in one process and interact with it from another process using normal object operations: property reads, method calls, construction, and callbacks.

```python
# Python process — export a real object
exported = await Proxyable.Export(Calculator(), stream)
```

```typescript
// TypeScript process — use it like it's right here
const calc = Proxyable.ImportFrom({ stream });
await calc.add(2, 3);                        // calls Python, returns 5
await calc.history[0];                        // reads a nested property
const session = await new calc.Session();     // constructs a remote instance
```

If the shape of the API already lives in code, Proxyables lets you use that shape directly.

## What makes this different

### Objects, not endpoints

Traditional RPC maps work to named endpoints. Proxyables maps work to object interactions: get a property, call a method, construct an instance, or follow a nested child object. The remote object graph is navigated the same way you would navigate a local one.

### Bidirectional by default

Both sides can export and import objects over the same connection. A TypeScript process can call into Go while Go calls back into TypeScript over the same multiplexed transport.

### Callbacks that cross language boundaries

Pass a function from Python to Rust. Rust can call it, the function runs in Python, and the result comes back through the same connection. Functions and objects passed as arguments are automatically registered as references on the other side.

```go
// Go exports an object that accepts a callback
type API struct{}
func (a *API) Transform(input string, fn func(string) string) string {
    return fn(input) // calls the remote function — could be in any language
}
```

```python
# Python passes a local function to Go
result = await proxy.Transform("hello", lambda s: s.upper())
# result == "HELLO" — the lambda ran in Python, called from Go
```

### Distributed garbage collection

Remote references are reference-counted and cleaned up automatically. In GC-capable languages (TypeScript, Python, Go), dropping a proxy triggers a finalizer that sends a `release` instruction back to the exporter.

### One wire protocol, five languages

Every implementation uses the same instruction vocabulary, value types, and reference lifecycle on the wire. That compatibility is checked continuously by a **600-check release matrix** plus a **65-check curated multihop matrix**: direct 25 client/server pairs across 24 release scenarios, and real chained transports like `go -> py -> ts -> rs` over the same Yamux + MessagePack path. The nightly stress profile adds slow-consumer and backpressure coverage on top.

## Language support

| Language | Install | Proxy Style |
|----------|---------|-------------|
| [TypeScript](proxyables.ts/) | `npm install proxyables` | `await proxy.method()` — native `Proxy` interception |
| [Python](proxyables.py/) | `pip install proxyables` | `await proxy.method()` — `__getattr__` magic |
| [Go](proxyables.go/) | `go get github.com/stateforward/proxyables.go` | `proxy.Get("Method").Apply(args).Await(ctx)` — cursor chaining |
| [Rust](proxyables.rs/) | `proxyables = { git = "..." }` | `proxy.method().await` — macro-generated type-safe proxies |
| [Zig](proxyables.zig/) | git submodule | `cursor.Get("method").Apply(args)` — vtable dispatch |

Each implementation is idiomatic to its language. TypeScript uses `Proxy` and `FinalizationRegistry`. Rust uses `#[proxyable]` and `#[proxy]` proc macros. Go uses reflection and `runtime.SetFinalizer`. Python uses `__getattr__` and `weakref`. Zig uses comptime vtables and explicit allocators. Same protocol, native feel.

## When to use proxyables

**Good fit:**
- Polyglot systems where services need to interact as objects, not message buses
- Plugin architectures where extensions run in separate processes or languages
- Dev tools, REPLs, and exploratory APIs where the shape isn't fixed upfront
- Any system where bidirectional callbacks are a requirement, not an afterthought
- Rapid prototyping across language boundaries without schema ceremony

**Reach for something else when:**
- You need a strict, versioned schema contract between large independent teams (gRPC/protobuf)
- You're building a browser-facing public API (REST/GraphQL)
- You need fire-and-forget messaging at massive scale (Kafka/NATS)
- Everything lives in one language and one process

## Quick start

Clone with submodules:

```sh
git clone --recurse-submodules https://github.com/stateforward/proxyables.git
```

Or initialize after cloning:

```sh
git clone https://github.com/stateforward/proxyables.git
cd proxyables
git submodule update --init --recursive
```

Build and test instructions live in each submodule's README. To run the cross-language parity suite:

```sh
python3 parity/run.py                          # functional baseline (9 core scenarios)
python3 parity/run.py --profile release         # full release gate (24 scenarios, 25x24 matrix)
python3 parity/run.py --profile multihop        # curated 4-language transport chains
python3 parity/run.py --profile stress          # stress profile (payload/backpressure)
python3 parity/run.py --langs ts,py --pairs ts:py,py:ts  # specific pairs
```

To benchmark the full direct matrix and generate a standalone HTML report:

```sh
python3 bench/run.py
open bench/results/<timestamp>/report.html
```

You can scope the run the same way as parity:

```sh
python3 bench/run.py --pairs ts:py,py:go --iterations 250 --warmup 25
python3 bench/run.py --scenarios CallAdd,LargePayloadRoundtrip
```

## How it works

Every language implementation shares three layers:

1. **Proxy layer** — Wraps remote references as native objects. Property reads become `get` instructions, method calls become `apply`, constructors become `construct`. Chains of operations are batched into a single `execute` instruction.

2. **Wire protocol** — Instructions are MessagePack-encoded with fixed instruction kinds and value type constants shared across all implementations. The canonical contract is in [DSL.md](DSL.md).

3. **Transport** — All traffic is multiplexed over [Yamux](https://github.com/hashicorp/yamux), so concurrent operations share a single connection (TCP, Unix socket, WebSocket, stdio — anything that gives you a bidirectional byte stream). Stream pooling eliminates per-call handshake overhead.

Reference IDs are stable. Objects passed as arguments are automatically registered and sent as references. The other side receives a proxy. When that proxy is garbage collected (or explicitly released), a `release` instruction flows back to decrement the remote refcount. This is what makes bidirectional callbacks and complex object graphs work without leaking memory.

## Parity matrix

Every commit is validated against a cross-language test matrix. The release profile covers:

- Scalar access, method calls, nested objects, remote construction
- Bidirectional callback round-trips and object argument round-trips
- Error propagation with structured error chains
- Reference lifecycle: explicit release, alias retain/release, use-after-release
- GC-driven cleanup: automatic release after drop, finalizer eventual cleanup
- Stress: concurrent shared references, concurrent callback fan-out, release/use races
- Transport edge cases: abrupt disconnect cleanup, server abort in-flight, large payloads, deep object graphs
- Nightly stress profile: slow consumer backpressure
- Curated multihop profile: real chained paths such as `go -> py -> ts -> rs`, `py -> ts -> rs -> zig`, `ts -> go -> py -> zig`, `rs -> zig -> go -> ts`, and `zig -> rs -> py -> go`

Latest run:

| Client \ Server | ts | py | go | rs | zig |
|-----------------|----|----|----|----|-----|
| **ts**          | pass | pass | pass | pass | pass |
| **py**          | pass | pass | pass | pass | pass |
| **go**          | pass | pass | pass | pass | pass |
| **rs**          | pass | pass | pass | pass | pass |
| **zig**         | pass | pass | pass | pass | pass |

Latest full release run: [`parity/results/20260326-175530/summary.json`](parity/results/20260326-175530/summary.json) (`600 ok, 0 failed`)

Latest multihop run: [`parity/results/20260326-175716/summary.json`](parity/results/20260326-175716/summary.json) (`65 ok, 0 failed`)

Latest stress run: [`parity/results/20260326-173159/summary.json`](parity/results/20260326-173159/summary.json) (`10 ok, 0 failed`)

Full results and scenario definitions are in [`parity/`](parity/).

## Benchmark matrix

The repo also includes a benchmark harness that mirrors the direct parity topology, but records latency and throughput instead of only correctness. It runs the real client and real server implementations against each other across all 25 direct language pairs and writes:

- `bench/results/<timestamp>/summary.json`
- `bench/results/<timestamp>/report.html`

The HTML report is self-contained and can be opened directly from disk. No HTTP server is needed.

Latest captured benchmark run: [`bench/results/20260326-185624/summary.json`](bench/results/20260326-185624/summary.json)

Latest captured benchmark report: [`bench/results/20260326-185624/report.html`](bench/results/20260326-185624/report.html)

That run covered all 25 direct pairs across 10 benchmark scenarios and finished `250 ok, 0 failed`.

Representative average latency across the 10 benchmark scenarios from that run:

| Pair | Avg latency |
|------|-------------|
| `rs -> rs` | `0.1777 ms` |
| `ts -> rs` | `0.1872 ms` |
| `go -> rs` | `0.2874 ms` |
| `py -> py` | `0.5873 ms` |
| `ts -> ts` | `0.6219 ms` |
| `zig -> zig` | `0.6435 ms` |
| `go -> go` | `1.2976 ms` |

Fastest direct pair in the captured run was `rs -> rs`. Slowest direct pair was `zig -> go` at `1.8198 ms` average across the same 10-scenario set.

## Repository structure

```
proxyables/
├── DSL.md             # Wire protocol contract (instruction kinds, value types, reference lifecycle)
├── bench/             # Cross-language benchmark harness and standalone HTML reports
├── parity/            # Cross-language test harness and scenario definitions
├── proxyables.ts/     # TypeScript — npm package
├── proxyables.py/     # Python — pip package
├── proxyables.go/     # Go module
├── proxyables.rs/     # Rust crate with proc macros
└── proxyables.zig/    # Zig library
```

Each subdirectory is a git submodule with its own repo, README, tests, and CI.

## License

Each language implementation maintains its own license. See the individual submodule directories for details.
