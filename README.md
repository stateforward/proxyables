# proxyables

**Remote objects that feel local. No schemas. No codegen. No stubs.**

Proxyables is not another RPC framework. RPC makes you think in endpoints and messages — define a schema, generate stubs, serialize requests, deserialize responses, repeat for every language. Proxyables makes the network disappear. You export an object in one process, and it shows up as a native object in another — with property access, method calls, construction, and callbacks working transparently across language boundaries.

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

No `.proto` files. No OpenAPI specs. No generated clients. The proxy *is* the API.

## What makes this different

### Objects, not endpoints

Traditional RPC maps every operation to a named endpoint. Proxyables maps operations to *object interactions* — get a property, call a method, construct an instance, access a nested child. The remote object graph is yours to navigate, just like local code.

### Bidirectional by default

There is no client. There is no server. Both sides export and import objects over the same connection. A TypeScript process can call into Go while Go simultaneously calls back into TypeScript — over a single multiplexed stream.

### Callbacks that cross language boundaries

Pass a function from Python to Rust. Rust calls it — the function executes in Python and the result flows back. This works because functions and objects passed as arguments are automatically registered as proxies on the other side.

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

Remote references are reference-counted and cleaned up automatically. In GC-capable languages (TypeScript, Python, Go), dropping a proxy triggers a finalizer that sends a `release` instruction to the exporter. No manual lifecycle management. No leaked resources.

### One wire protocol, five languages

Every implementation encodes the same instructions, the same value types, the same reference lifecycle — down to the byte. This isn't "theoretically compatible." It's proven by a **625-check parity matrix** that runs on every commit: 25 client/server pairs across 25 scenarios including callbacks, error propagation, concurrent fan-out, GC cleanup, abrupt disconnects, and large payload fidelity.

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
python3 parity/run.py --profile release         # full release gate (25 scenarios)
python3 parity/run.py --langs ts,py --pairs ts:py,py:ts  # specific pairs
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
- Transport edge cases: abrupt disconnect cleanup, server abort in-flight, large payloads, deep object graphs, slow consumer backpressure

Latest run:

| Client \ Server | ts | py | go | rs | zig |
|-----------------|----|----|----|----|-----|
| **ts**          | pass | pass | pass | pass | pass |
| **py**          | pass | pass | pass | pass | pass |
| **go**          | pass | pass | pass | pass | pass |
| **rs**          | pass | pass | pass | pass | pass |
| **zig**         | pass | pass | pass | pass | pass |

Full results and scenario definitions are in [`parity/`](parity/).

## Repository structure

```
proxyables/
├── DSL.md             # Wire protocol contract (instruction kinds, value types, reference lifecycle)
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
