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

## License

Each language implementation maintains its own license. See the individual submodule directories for details.
