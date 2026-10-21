# Snapshot format (M4)

A snapshot is a point-in-time copy of one book plus the place to resume in its journal,
so replay can start mid-session. It is a cache, not a source of truth: if it is lost or
damaged, a full replay of the journal rebuilds the same state.

Implemented in `engine/src/snapshot.cpp` (`encode_snapshot`, `decode_snapshot`) and
`OrderBook::state()` / `OrderBook::from_state()`.

## What it contains

Everything the book's **future output** depends on, not just the visible book:

| Field | Why it is needed |
| --- | --- |
| `last_seq_no` | the book's `seq_no` check continues from here; replay resumes at `last_seq_no + 1` |
| `last_timestamp` | the journal's non-decreasing timestamp check continues across the snapshot |
| `journal_offset` | byte offset of record `last_seq_no + 1`, so replay seeks instead of scanning |
| `next_trade_id` | without it, trade ids after a restore would restart at 1 |
| `used_ids` | without it, a restored book would accept a reused id that a full replay rejects |
| levels and orders | the book itself, including FIFO position, partial fills and STP modes |

Not stored: level totals and order counts (recomputed on load and then checked by the
invariant walk), and each order's side and price (they come from its level).

`used_ids` grows with the session. It will be reset at session open once session states
arrive (M5), which bounds it, and therefore the snapshot, to one session.

## Encoding

Little-endian, fixed-width, written byte by byte (the same rules as the journal).

```text
magic             8   "MFSNAPSH"
version           u16 1
reserved          u16 0
last_seq_no       u64 0 = nothing processed yet
last_timestamp    i64
journal_offset    u64
next_trade_id     u64
used_id_count     u64, then that many u64 ids, strictly ascending
bid_level_count   u64, then per level, best (highest) price first:
    price         i64
    order_count   u64, then per order, front of the queue first:
        order_id u64, account_id u64, qty i64 (current total), leaves i64,
        filled i64, priority_seq_no u64, post_only u8, stp u8
ask_level_count   u64, then the same per level, best (lowest) price first
crc32             u32 over every preceding byte
```

**Deterministic bytes.** The same state always encodes to the same bytes. `used_ids` is
held in a hash set, so it is sorted before writing; levels and queues come from ordered
structures. A test reaches the same state through two different histories and compares
the bytes.

## Validation

`decode_snapshot` checks the magic, the version (before the CRC, so the message names
the version), the CRC, the enum and flag ranges, and that nothing is left over. It also
checks that every count fits in the remaining bytes, so a corrupt count cannot trigger
a huge allocation.

`OrderBook::from_state` then rejects states that decode fine but are inconsistent:

- levels not strictly best price first, an empty level, or a non-positive price;
- an order with `leaves <= 0` or `filled + leaves != qty`;
- a priority `seq_no` outside `1..last_seq_no`;
- a resting id that appears twice or is missing from `used_ids`;
- `used_ids` not ascending, or `next_trade_id = 0`;
- a crossed book.

## Writing and resuming

- `write_snapshot_file` writes `<path>.tmp` and renames it into place, so a crash never
  leaves a half-written snapshot under the real name.
- `mf-replay --snapshot-every N --snapshot-dir D` writes
  `D/snap-<seq_no, 12 digits>.mfsnap` after every N-th command.
- `mf-replay --snapshot S` restores the book, seeks to `journal_offset` and requires the
  next record to be `last_seq_no + 1`. A snapshot from a different journal, or one
  pointing past the end of the file, fails with a journal error. The printed hash
  covers the tail only. A test checks that the tail's stream equals the full replay's
  stream after `last_seq_no`, byte for byte.

Snapshots are taken on the matching thread, which pauses matching for the O(N) copy.
Taking them from a replica or a copy-on-write fork is left for later.
