# proxyables — cross-process remote objects

A family of libraries for **exported** and **imported** proxyable objects over multiplexed streams (Yamux and a shared instruction protocol). Implementations share the same conceptual model — transparent proxies, bi-directional references, and coordinated cleanup — with idiomatic APIs in each language.

## Language implementations

| Language | Module | Repository |
|----------|--------|------------|
| Go | [proxyables.go](proxyables.go/) | [stateforward/proxyables.go](https://github.com/stateforward/proxyables.go) |
| Python | [proxyables.py](proxyables.py/) | [stateforward/proxyables.py](https://github.com/stateforward/proxyables.py) |
| Rust | [proxyables.rs](proxyables.rs/) | [stateforward/proxyables.rs](https://github.com/stateforward/proxyables.rs) |
| TypeScript | [proxyables.ts](proxyables.ts/) | [stateforward/proxyables.ts](https://github.com/stateforward/proxyables.ts) |
| Zig | [proxyables.zig](proxyables.zig/) | [stateforward/proxyables.zig](https://github.com/stateforward/proxyables.zig) |

Each subdirectory is its own repository (git submodule). Use the language-specific README in that folder for install commands, examples, and API details.

Cross-language protocol contracts are documented in [DSL.md](DSL.md).

## Repository structure

This monorepo uses git submodules. Clone with submodules so every implementation is present:

```
proxyables/
├── README.md        # this file
├── proxyables.go/   # Go implementation
├── proxyables.py/   # Python implementation
├── proxyables.rs/   # Rust implementation
├── proxyables.ts/   # TypeScript / Node implementation
└── proxyables.zig/  # Zig implementation
```

## Getting started

Clone with submodules:

```sh
git clone --recurse-submodules https://github.com/stateforward/proxyables.git
```

Or initialize submodules after cloning:

```sh
git clone https://github.com/stateforward/proxyables.git
cd proxyables
git submodule update --init --recursive
```

Then open the implementation you need; build and test instructions live in each submodule.

For repository-level parity checks:

```sh
python3 parity/run.py
```

- `--langs` controls the active language set (defaults to `ts,py,go,rs,zig`).
- `--pairs` restricts client/server combinations (e.g. `ts:go,go:ts`).
- `--scenarios` limits matrix scenarios (from `parity/scenarios.json`).
- `--allow-unsupported` marks unsupported pairs instead of failing the run.

## Small examples (all 5 languages)

### TypeScript

```ts
import { createExportedProxyable, createImportedProxyable } from "proxyables";

const exported = createExportedProxyable({ object: { echo: (msg: string) => `echo ${msg}` }, stream });
const proxy = createImportedProxyable({ stream });

await Promise.all([proxy.echo("hello"), proxy.compute(10, 20)]);
```

### Python

```python
from proxyables import Proxyable

class API:
    async def echo(self, msg: str) -> str:
        return f"echo {msg}"

    async def compute(self, a: int, b: int) -> int:
        return a + b

exported = await Proxyable.export(API(), stream)
proxy = await Proxyable.import_from(stream)
await proxy.echo("hello")
await proxy.compute(10, 20)
```

### Go

```go
exported, _ := proxyables.Export(conn, &API{}, nil)
imported, _ := proxyables.ImportFrom(conn, nil)

result, _ := imported.Root().Get("Echo").Apply("hello").Exec(ctx)
_ = result // "echo hello"
```

### Rust

```rust
#[proxyable]
struct API;

impl API {
    async fn echo(&self, msg: String) -> String { format!("echo {msg}") }
    async fn compute(&self, a: i64, b: i64) -> i64 { a + b }
}

let (imported, driver) = Proxyable::import_from(stream);
tokio::spawn(driver);
let api = ApiProxy::new(imported);
let _ = api.echo("hello".into()).await;
```

### Zig

```zig
const API = struct {
    pub fn echo(self: *@This(), msg: []const u8) []const u8 { return msg; }
    pub fn compute(self: *@This(), a: i64, b: i64) i64 { return a + b; }
};

const exported = try proxyables.Proxyable.export(.{ .allocator = allocator, .session = session, .root = api.proxyTarget() });
const cursor = try proxyables.Proxyable.import_from(.{ .allocator = allocator, .session = session });
```

## Parity matrix results

Latest captured run: `parity/results/20260326-083238` (`parity-json-v1`)

- Total: `225` checks
- Passed: `225`
- Failed: `0`
- Required scenarios: `225`

| Client → Server | ts | py | go | rs | zig |
|-----------------|----|----|----|----|-----|
| ts              | ✅ | ✅ | ✅ | ✅ | ✅ |
| py              | ✅ | ✅ | ✅ | ✅ | ✅ |
| go              | ✅ | ✅ | ✅ | ✅ | ✅ |
| rs              | ✅ | ✅ | ✅ | ✅ | ✅ |
| zig             | ✅ | ✅ | ✅ | ✅ | ✅ |

All supported scenarios are currently green.

## License

Each language implementation maintains its own license. See the individual submodule directories for details.
