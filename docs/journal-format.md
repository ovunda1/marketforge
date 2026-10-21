# Journal format (M4, version 2 in M5)

The journal is the source of truth for one symbol's session. It holds the inbound
commands in sequencer order. Outputs are never stored: replaying the journal through the
engine regenerates them byte for byte, and `mf-replay` proves it with a SHA-256 of the
canonical event stream (`docs/event-format.md`).

Implemented in `engine/src/journal.cpp` (`JournalWriter`, `JournalReader`) and written by
the `Sequencer` (`engine/include/mf/sequencer.hpp`).

## What is journaled

Every command that receives a `seq_no`, **including commands the engine rejects and
admin commands** (limits, kill switches, session transitions; `docs/risk.md`):

- A reject depends on engine state (an unknown cancel, an unfillable FOK, a reused id, a
  risk limit), so it is only known after the engine runs. Journaling only accepted
  commands would force the engine to run before the journal write, which gives up
  write-ahead.
- Admin changes are in the journal at the `seq_no` where they took effect, so replay
  checks every order against the same limits, session and kill switches.
- The client saw the reject. Replay has to reproduce every published event, or the hash
  would describe a different stream from the one that went out.
- `seq_no`s stay gapless, so the reader can treat any gap as damage.

The sequencer never rejects. Validation, including the risk gate and session-wide
order-id uniqueness, runs in the engine, which also runs on replay. The record's
timestamp is the rate limiter's only clock.

## Encoding

All integers are little-endian and fixed-width. Signed values are two's complement.
Fields are written byte by byte, never by copying a struct, so host endianness and
struct padding cannot reach a file. CRCs are CRC-32/IEEE (the zlib polynomial, reflected
`0xEDB88320`, check value `crc32("123456789") = 0xCBF43926`).

### File header (16 bytes)

| Offset | Size | Field | Value |
| --- | --- | --- | --- |
| 0 | 8 | magic | ASCII `MFJOURNL` |
| 8 | 2 | format_version | u16, `2` (the reader accepts `1` and `2`) |
| 10 | 2 | reserved | u16, must be `0` |
| 12 | 4 | header_crc | u32, CRC32 of bytes 0–11 |

### Record

| Offset | Size | Field |
| --- | --- | --- |
| 0 | 4 | `length` u32: bytes from `seq_no` to the end of the payload (`17 + n`) |
| 4 | 8 | `seq_no` u64 |
| 12 | 8 | `timestamp` i64, nanoseconds since the Unix epoch, from the injected `Clock` |
| 20 | 1 | `cmd_type` u8: `1` New, `2` Cancel, `3` Replace; version 2 adds `4` SetSession, `5` SetInstrumentLimits, `6` SetAccountLimits, `7` KillSwitch |
| 21 | n | payload |
| 21 + n | 4 | `crc32` u32 over bytes `0 .. 20 + n` (the length field is covered) |

### Payloads

| Type | Fields, in order | n |
| --- | --- | --- |
| New | `order_id` u64, `account_id` u64, `side` u8, `type` u8, `tif` u8, `post_only` u8, `stp` u8, `price` i64, `qty` i64 | 37 |
| Cancel | `order_id` u64 | 8 |
| Replace | `order_id` u64, `price` i64, `qty` i64 | 24 |
| SetSession | `state` u8 | 1 |
| SetInstrumentLimits | `mask` u8, `max_qty` i64, `band_bps` i64, `reference_price` i64 | 25 |
| SetAccountLimits | `scope` u8, `account_id` u64, `mask` u8, `max_notional` i64, `position_limit` i64, `credit` i64, `max_open_orders` i64, `rate_per_sec` i64, `rate_burst` i64 | 58 |
| KillSwitch | `scope` u8, `account_id` u64, `engaged` u8, `cancel_open` u8 | 11 |

Record sizes: New 62 bytes, Cancel 33, Replace 49, SetSession 26, SetInstrumentLimits 50,
SetAccountLimits 83, KillSwitch 36.

Enum values: `side` Buy=0, Sell=1; `type` Limit=0, Market=1; `tif` GTC=0, IOC=1, FOK=2,
DAY=3 (version 2); `post_only` 0 or 1; `stp` cancel-newest=0, cancel-oldest=1,
cancel-both=2; `state` PRE_OPEN=0, CONTINUOUS=1, HALTED=2, CLOSED=3. Invalid orders (a
zero quantity, say) and out-of-range limits are encoded as they arrived; the engine
rejects them again on replay.

Admin payloads (version 2):

- **`mask`** has one bit per optional limit, in field order. For instrument limits
  that is bit 0 `max_qty`, 1 `band_bps`, 2 `reference_price`. For account limits it is
  bit 0 `max_notional`, 1 `position_limit`, 2 `credit`, 3 `max_open_orders`, 4 `rate`;
  bit 4 covers both `rate_per_sec` and `rate_burst`.
- **Unset fields are written as 0**, so every command has exactly one encoding.
- **`scope`** is 0 for the defaults (account limits) or the global switch (kill
  switch), with `account_id` 0. Scope 1 means one account.

The `seq_no` sits outside the payload so continuity can be checked without decoding. The
length prefix frames records and leaves room for new command types in later versions.
Each type's length is exact.

## Validation

The reader returns a record only after all of these checks pass. Any failure throws
`JournalError`, whose message names the file, the record's byte offset and the last good
`seq_no`. `mf-replay` then exits with code 4 and prints no hash, so a damaged journal is
never partially accepted.

| Kind | Cause |
| --- | --- |
| `BadHeader` | wrong magic, header CRC mismatch, version other than 1 or 2, non-zero reserved field |
| `Truncated` | the file ends inside the header or inside a record |
| `Corrupt` | `length` outside 18–75 (checked before anything is read or allocated), CRC mismatch, `length` not matching `cmd_type`, unknown `cmd_type`, an enum or flag out of range, an unknown mask bit, an unset field that is not 0, a scope out of range or scope 0 with an account id, a version-2 command type or DAY in a version-1 journal |
| `SequenceGap` | `seq_no` is not the previous `seq_no + 1`; the first record must be 1 |
| `TimestampBackwards` | `timestamp` is earlier than the previous record's; **equal timestamps are allowed** (non-decreasing, not strictly increasing) |
| `Io` | the file cannot be opened or read (`mf-replay` exit code 1) |

A file that ends exactly on a record boundary is a clean end. A crash in the middle of
an append leaves a torn final record. It is reported as `Truncated`, never replayed and
never silently dropped. Automatically trimming a torn tail on restart is not implemented
yet.

`JournalWriter` refuses to create a journal that would fail these checks. It throws on a
`seq_no` that is not `last + 1` or a timestamp earlier than the last one. It also
refuses to open an existing file (`O_EXCL`), so a journal is never overwritten.

## Durability

`JournalWriter::append` hands each record to the OS with a single `write(2)` before the
sequencer returns, so before the book sees the command. There is **no `fsync`**:

- **Process crash** (the engine is killed or segfaults): every appended record survives.
  It is in the OS page cache and the kernel writes it out. At most the record being
  written at that moment is torn, and the reader reports that as `Truncated`.
- **OS crash, kernel panic or power loss**: records still in the page cache are lost,
  and a record may be partly on disk. The journal on disk can end several records
  before the last one the engine processed. Events already published for those records
  are then not reproducible.

Closing that gap needs `fsync` (or `fdatasync`) before acknowledging a command. Calling
it per record costs a disk flush per command. The usual fix is group commit: fsync a
batch, then release that batch's acknowledgements. Both are left for later milestones.
The cost of the current `write` per record will be measured in M6.

## Tools

```sh
mf-journal commands.txt run.mfj [--config risk.yaml] [--start-ns T] [--step-ns S]
mf-replay run.mfj [--events out.txt]                           # prints sha256=<hex>
mf-replay run.mfj --snapshot-every 10000 --snapshot-dir snaps/
mf-replay run.mfj --snapshot snaps/snap-000000010000.mfsnap    # tail only
make replay JOURNAL=run.mfj [SNAPSHOT=...]
```

`mf-journal` sends text commands through the real `Sequencer` with a `SimulatedClock`.
The input's `seq_no`s must already be 1, 2, 3…, because a journal that renumbered them
would replay to a different stream from the text file.

- **Clock lines:** a `T <ns>` line sets the clock; time going backwards is an error
  (exit 2).
- **`--step-ns`** defaults to 0. Timestamps then equal the ones `mf_driver` uses, which
  matters once a rate limit is configured.
- **`--config`** journals the YAML's admin commands first, as `seq_no` 1..k, so the file
  continues at `k + 1`.

For the same commands and config, `mf-replay`'s hash equals `sha256` of
`mf_driver [--config …]`'s output.

`mf-replay` exit codes: 0 ok, 1 usage or I/O error, 3 invariant violation, 4 damaged
journal, 5 bad snapshot.

Replay runs as fast as possible. Real-time and scaled-speed replay (SPEC) need a host
clock in the tool and are not built yet.

`engine/tests/journal/v1.mfj` is a committed version-1 journal with its expected hash.
`v2.mfj` (from `v2.cmds`, generated by `tests/risk_flow.py`) is a version-2 journal
with limits, admin commands and a moving clock; its stream contains every risk reject.
A change that makes either unreadable, or replay differently, fails a test.
