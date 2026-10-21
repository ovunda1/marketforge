# Journal format (M4)

The journal is the source of truth for one symbol's session. It holds the inbound
commands in sequencer order. Outputs are never stored: replaying the journal through the
engine regenerates them byte for byte, and `mf-replay` proves it with a SHA-256 of the
canonical event stream (`docs/event-format.md`).

Implemented in `engine/src/journal.cpp` (`JournalWriter`, `JournalReader`) and written by
the `Sequencer` (`engine/include/mf/sequencer.hpp`).

## What is journaled

Every command that receives a `seq_no`, **including commands the book rejects**:

- A reject depends on book state (an unknown cancel, an unfillable FOK, a reused id), so
  it is only known after matching. Journaling only accepted commands would force the book
  to run before the journal write, which gives up write-ahead.
- The client saw the reject. Replay has to reproduce every published event, or the hash
  would describe a different stream from the one that went out.
- `seq_no`s stay gapless, so the reader can treat any gap as damage.

The sequencer never rejects. Validation, including session-wide order-id uniqueness,
runs in the book, which also runs on replay.

## Encoding

All integers are little-endian and fixed-width. Signed values are two's complement.
Fields are written byte by byte, never by copying a struct, so host endianness and
struct padding cannot reach a file. CRCs are CRC-32/IEEE (the zlib polynomial, reflected
`0xEDB88320`, check value `crc32("123456789") = 0xCBF43926`).

### File header (16 bytes)

| Offset | Size | Field | Value |
| --- | --- | --- | --- |
| 0 | 8 | magic | ASCII `MFJOURNL` |
| 8 | 2 | format_version | u16, `1` |
| 10 | 2 | reserved | u16, must be `0` |
| 12 | 4 | header_crc | u32, CRC32 of bytes 0–11 |

### Record

| Offset | Size | Field |
| --- | --- | --- |
| 0 | 4 | `length` u32: bytes from `seq_no` to the end of the payload (`17 + n`) |
| 4 | 8 | `seq_no` u64 |
| 12 | 8 | `timestamp` i64, nanoseconds since the Unix epoch, from the injected `Clock` |
| 20 | 1 | `cmd_type` u8: `1` New, `2` Cancel, `3` Replace |
| 21 | n | payload |
| 21 + n | 4 | `crc32` u32 over bytes `0 .. 20 + n` (the length field is covered) |

### Payloads

| Type | Fields, in order | n |
| --- | --- | --- |
| New | `order_id` u64, `account_id` u64, `side` u8, `type` u8, `tif` u8, `post_only` u8, `stp` u8, `price` i64, `qty` i64 | 37 |
| Cancel | `order_id` u64 | 8 |
| Replace | `order_id` u64, `price` i64, `qty` i64 | 24 |

Record sizes: New 62 bytes, Cancel 33, Replace 49.

Enum values: `side` Buy=0, Sell=1; `type` Limit=0, Market=1; `tif` GTC=0, IOC=1, FOK=2;
`post_only` 0 or 1; `stp` cancel-newest=0, cancel-oldest=1, cancel-both=2. Invalid
orders (a zero quantity, say) are encoded as they arrived; the book rejects them again
on replay.

The `seq_no` sits outside the payload so continuity can be checked without decoding. The
length prefix frames records and leaves room for new command types in later versions.
In version 1 each type's length is exact.

## Validation

The reader returns a record only after all of these checks pass. Any failure throws
`JournalError`, whose message names the file, the record's byte offset and the last good
`seq_no`. `mf-replay` then exits with code 4 and prints no hash, so a damaged journal is
never partially accepted.

| Kind | Cause |
| --- | --- |
| `BadHeader` | wrong magic, header CRC mismatch, unsupported version, non-zero reserved field |
| `Truncated` | the file ends inside the header or inside a record |
| `Corrupt` | `length` outside 25–54 (checked before anything is read or allocated), CRC mismatch, `length` not matching `cmd_type`, unknown `cmd_type`, an enum or flag out of range |
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
mf-journal commands.txt run.mfj [--start-ns T] [--step-ns S]   # text -> journal
mf-replay run.mfj [--events out.txt]                           # prints sha256=<hex>
mf-replay run.mfj --snapshot-every 10000 --snapshot-dir snaps/
mf-replay run.mfj --snapshot snaps/snap-000000010000.mfsnap    # tail only
make replay JOURNAL=run.mfj [SNAPSHOT=...]
```

`mf-journal` sends text commands through the real `Sequencer` with a `SimulatedClock`.
The input's `seq_no`s must already be 1, 2, 3…, because a journal that renumbered them
would replay to a different stream from the text file. For the same commands,
`mf-replay`'s hash equals `sha256` of `mf_driver`'s output.

`mf-replay` exit codes: 0 ok, 1 usage or I/O error, 3 invariant violation, 4 damaged
journal, 5 bad snapshot.

Replay runs as fast as possible. Real-time and scaled-speed replay (SPEC) need a host
clock in the tool and are not built yet.

`engine/tests/journal/v1.mfj` is a committed version-1 journal with its expected hash.
A change that makes old journals unreadable, or replay differently, fails that test.
