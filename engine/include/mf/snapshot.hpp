#pragma once

#include <cstdint>
#include <stdexcept>
#include <string>
#include <string_view>

#include "mf/order_book.hpp"
#include "mf/risk.hpp"
#include "mf/types.hpp"

namespace mf {

// A point-in-time copy of one symbol's engine (book and risk state) plus where to resume
// in its journal, so replay can start mid-session. A snapshot is a cache: the journal stays the source of truth, and
// a full replay rebuilds the same state. Layout: docs/snapshot-format.md.

inline constexpr std::string_view kSnapshotMagic{"MFSNAPSH", 8};
inline constexpr std::uint16_t kSnapshotVersion = 2;  // reads 1 and 2

struct Snapshot {
  BookState book;
  RiskState risk;  // version 1 snapshots load with the defaults (continuous, no limits)
  Timestamp last_timestamp{};      // timestamp of record book.last_seq_no (0 if none)
  std::uint64_t journal_offset{};  // byte offset of record book.last_seq_no + 1

  bool operator==(const Snapshot&) const = default;
};

class SnapshotError : public std::runtime_error {
 public:
  using std::runtime_error::runtime_error;
};

// Deterministic: the same state always encodes to the same bytes.
std::string encode_snapshot(const Snapshot& snapshot);
// Throws SnapshotError on a bad magic, version, CRC, length or field value.
Snapshot decode_snapshot(std::string_view bytes);

// Writes to `path`.tmp and renames it, so a crash never leaves a partial snapshot at
// `path`. Throws SnapshotError on I/O failure.
void write_snapshot_file(const std::string& path, const Snapshot& snapshot);
Snapshot read_snapshot_file(const std::string& path);

}  // namespace mf
