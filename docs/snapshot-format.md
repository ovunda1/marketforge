# Snapshot format (M4, version 2 in M5)

A snapshot is a point-in-time copy of one symbol's engine (its book and its risk state)
plus the place to resume in its journal, so replay can start mid-session. It is a cache, not a source of truth: if it is lost or
damaged, a full replay of the journal rebuilds the same state.

Implemented in `engine/src/snapshot.cpp` (`encode_snapshot`, `decode_snapshot`) and
`Engine::state()` / `Engine::from_state()` (which use `OrderBook` and `RiskGate`).

## What it contains

Everything the book's **future output** depends on, not just the visible book:

| Field | Why it is needed |
| --- | --- |
| `last_seq_no` | the book's `seq_no` check continues from here; replay resumes at `last_seq_no + 1` |
| `last_timestamp` | the journal's non-decreasing timestamp check continues across the snapshot |
| `journal_offset` | byte offset of record `last_seq_no + 1`, so replay seeks instead of scanning |
| `next_trade_id` | without it, trade ids after a restore would restart at 1 |
| `used_ids` | without it, a restored book would accept a reused id that a full replay rejects |
| levels and orders | the book itself, including FIFO position, partial fills, STP modes and time in force (GTC or DAY, so a restored DAY order still expires at the close) |
| session state | new orders are only accepted while `CONTINUOUS` |
| limits | instrument, default and per-account limits in force at `last_seq_no` |
| kill switches | global and per account |
| positions | position limits count filled quantity, which the book no longer shows |
| rate buckets | tokens and last refill time per account; without them a restored engine would refill every bucket |
| last trade price | the price band's first reference |

Not stored:
- **Level totals and order counts.** They are recomputed on load and then checked by
  the invariant walk.
- **Each order's side and price.** They come from its level.
- **Each account's open-order exposure.** It is rebuilt from the resting orders, so it
  cannot disagree with the book.

`used_ids` is reset at session open (keeping the ids of resting orders), which bounds
it, and therefore the snapshot, to one session.

## Encoding

Little-endian, fixed-width, written byte by byte (the same rules as the journal).

```text
magic             8   "MFSNAPSH"
version           u16 2 (the decoder reads 1 and 2)
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
        filled i64, priority_seq_no u64, post_only u8, stp u8,
        tif u8 (version 2; GTC=0, DAY=3)
ask_level_count   u64, then the same per level, best (lowest) price first
-- risk state, version 2 --
session           u8  PRE_OPEN=0, CONTINUOUS=1, HALTED=2, CLOSED=3
global_kill       u8  0 or 1
has_last_trade    u8, last_trade_price i64 (0 when unset)
instrument        mask u8 (bit 0 max_qty, 1 band_bps, 2 reference_price), 3 x i64
has_defaults      u8, default limits (account limits, all zero when unset)
account_count     u64, then per account (ascending): account_id u64, account limits
killed_count      u64, then that many account ids u64, ascending
position_count    u64, then per account (ascending): account_id u64, qty i64 (non-zero)
bucket_count      u64, then per account (ascending): account_id u64, tokens i64,
                  last_refill i64
crc32             u32 over every preceding byte

account limits:   mask u8 (bit 0 max_notional, 1 position_limit, 2 credit,
                  3 max_open_orders, 4 rate), then max_notional, position_limit,
                  credit, max_open_orders, rate_per_sec, rate_burst as i64
```

**Deterministic bytes.** The same state always encodes to the same bytes:
- `used_ids` is held in a hash set, so it is sorted before writing.
- Levels and queues come from ordered structures.
- Accounts are written in ascending id.
- Unset optional fields are written as 0.

Tests reach the same book state, and the same risk state, through two different
histories and compare the bytes.

**Version 1** snapshots (written by M4) still load. Every order is GTC, and the risk
state is the default: continuous, no limits. `engine/tests/journal/v1-250.mfsnap` is
one, written by the M4 build, with the tail hash M4 recorded for it.

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
- a crossed book;
- a resting order whose time in force is not GTC or DAY.

`RiskGate::from_state` rejects:
- accounts that are unsorted or repeated in any list;
- invalid limits;
- a zero stored position;
- a bucket for an account without a rate limit, or tokens outside `0..burst`;
- a non-positive last trade price.

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
