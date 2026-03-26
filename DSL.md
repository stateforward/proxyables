# DSL Contract

This repository defines a shared wire contract for cross-language `proxyables` behavior.
All implementations in the submodules should read and write the same instruction and value
encodings so a proxy exported in one language can be consumed by another.

Use this as the canonical reference when adding new language support, changing on-wire
structures, or debugging parity failures.

## Message format

Most traffic is MessagePack-encoded `ProxyInstruction` objects with these fields:

- `id` (optional): string, implementation-generated correlation id.
- `kind` (required): instruction kind.
- `data` (required): instruction payload.
- `metadata` (optional): reserved/extension data.

### Value kinds

- `function` `0x9ed64249`
- `array` `0x8a58ad26`
- `string` `0x17c16538`
- `number` `0x1bd670a0`
- `boolean` `0x65f46ebf`
- `symbol` `0xf3fb51d1`
- `object` `0xb8c60cba`
- `bigint` `0x8a67a5ca`
- `unknown` `0x9b759fb9`
- `null` `0x77074ba4`
- `undefined` `0x9b61ad43`
- `reference` `0x5a1b3c4d`

`reference` values carry a string reference id in `data` and represent remote/cached objects.

## Instruction kinds

- `local` `0x9c436708`
- `get` `0x540ca757`
- `set` `0xc6270703` (supported in implementation code; not required in parity matrix)
- `apply` `0x24bc4a3b`
- `construct` `0x40c09172`
- `execute` `0xa01e3d98`
- `throw` `0x7a78762f`
- `return` `0x85ee37bf`
- `next` `0x5cb68de8`
- `release` `0x1a2b3c4d`

Core semantics:

- `get`: read a property from the current target; payload is `["propertyName"]`.
- `apply`: invoke current target; payload is positional args.
- `construct`: construct current target with args.
- `execute`: batch execute a sequence of instructions.
- `return` / `throw`: terminal response instructions from the remote side.
- `release`: signal explicit reference release for `data = [refId]`.

## Reference lifetime

Implementations maintain registries of local exported objects and reference IDs. A remote
proxy may send a `reference` value or `release` instruction to coordinate lifecycle.

- Exported object/function values should map to stable reference IDs.
- Consumers receive references as callable or property-bearing proxies.
- `release` is expected to decrement remote reference counts when supported.

## Parity expectations

- Parity harness protocol label: `parity-json-v1`.
- Scenario coverage includes scalar access, method calls, construction, callbacks, error round-trips,
  reference consistency, and explicit release.
- The canonical tests check instruction/constant shape and these scenarios from each language pair.
- See `parity/run.py` and `parity/scenarios.json` for the full matrix.

## Maintenance guidance

- Any change that touches instruction/value constants must be updated atomically in all
  language submodules plus parity tooling.
- New scenarios should be added to `parity/scenarios.json`, then reflected in agent implementations
  and documentation.
