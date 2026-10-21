#pragma once

#include <cstdint>
#include <optional>
#include <ostream>
#include <string>
#include <vector>

#include "mf/types.hpp"

namespace mf {

struct ReplayOptions {
  std::string journal_path;
  // Start from this snapshot and replay only the records after it.
  std::optional<std::string> snapshot_path;
  // Also write the canonical stream here (the bytes that are hashed).
  std::ostream* events_out{};
  // Write a snapshot after every `snapshot_every`-th seq_no (0: never) into
  // snapshot_dir, named snap-<seq_no, 12 digits>.mfsnap.
  std::uint64_t snapshot_every{};
  std::string snapshot_dir;
};

struct ReplayResult {
  std::string sha256;  // lowercase hex of the canonical stream
  std::uint64_t commands{};
  SeqNo last_seq_no{};
  std::vector<std::string> snapshots_written;
};

// Replays a journal (optionally from a snapshot) through a fresh Engine, passing each
// record's timestamp (the rate limiter's only clock), and hashes the
// canonical event stream (docs/event-format.md): each command's events and BOOK line,
// then the final LVL depth. For the same commands this is byte for byte what mf_driver
// writes. Throws JournalError, SnapshotError or InvariantViolation; on any error no
// result is returned, so a damaged journal is never partially accepted.
ReplayResult replay(const ReplayOptions& options);

}  // namespace mf
