// mf-replay: replays a journal through a fresh order book, optionally starting from a
// snapshot, and prints the SHA-256 of the canonical event stream (docs/event-format.md).
// Replaying the same journal always prints the same hash; for the same commands it
// equals sha256 of mf_driver's output.
//
// Usage: mf-replay <journal> [--snapshot FILE] [--events FILE]
//                            [--snapshot-every N --snapshot-dir DIR]
// stdout: "sha256=<hex>" and nothing else; a summary goes to stderr.
// Exit codes: 0 ok, 1 usage or I/O error, 3 invariant violation, 4 damaged journal
// (truncated, corrupt, sequence gap, time going backwards, bad header),
// 5 bad snapshot. On any error nothing is printed to stdout.

#include <charconv>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <optional>
#include <string>
#include <string_view>

#include "mf/journal.hpp"
#include "mf/order_book.hpp"
#include "mf/replay.hpp"
#include "mf/snapshot.hpp"

namespace {

int usage() {
  std::cerr << "usage: mf-replay <journal> [--snapshot FILE] [--events FILE]\n"
               "                           [--snapshot-every N --snapshot-dir DIR]\n";
  return 1;
}

}  // namespace

int main(int argc, char** argv) {
  if (argc < 2) return usage();
  mf::ReplayOptions options{.journal_path = argv[1]};
  std::optional<std::string> events_path;
  for (int i = 2; i < argc; i += 2) {
    const std::string_view flag = argv[i];
    if (i + 1 >= argc) return usage();
    const std::string value = argv[i + 1];
    if (flag == "--snapshot") {
      options.snapshot_path = value;
    } else if (flag == "--events") {
      events_path = value;
    } else if (flag == "--snapshot-dir") {
      options.snapshot_dir = value;
    } else if (flag == "--snapshot-every") {
      const auto [end, ec] = std::from_chars(value.data(), value.data() + value.size(),
                                             options.snapshot_every);
      if (ec != std::errc{} || end != value.data() + value.size() || options.snapshot_every == 0) {
        return usage();
      }
    } else {
      return usage();
    }
  }
  if ((options.snapshot_every != 0) != !options.snapshot_dir.empty()) return usage();

  std::ofstream events_file;
  if (events_path) {
    events_file.open(*events_path, std::ios::binary | std::ios::trunc);
    if (!events_file) {
      std::cerr << "mf-replay: cannot open " << *events_path << '\n';
      return 1;
    }
    options.events_out = &events_file;
  }
  if (!options.snapshot_dir.empty()) {
    std::error_code ec;
    std::filesystem::create_directories(options.snapshot_dir, ec);
    if (ec) {
      std::cerr << "mf-replay: cannot create " << options.snapshot_dir << ": " << ec.message() << '\n';
      return 1;
    }
  }

  try {
    const mf::ReplayResult result = mf::replay(options);
    if (events_path) {
      events_file.flush();
      if (!events_file) {
        std::cerr << "mf-replay: cannot write " << *events_path << '\n';
        return 1;
      }
    }
    std::cerr << "mf-replay: " << result.commands << " commands, last seq_no "
              << result.last_seq_no << ", " << result.snapshots_written.size()
              << " snapshots written\n";
    std::cout << "sha256=" << result.sha256 << '\n';
    return 0;
  } catch (const mf::JournalError& e) {
    std::cerr << "mf-replay: " << e.what() << '\n';
    return e.kind() == mf::JournalError::Kind::Io ? 1 : 4;
  } catch (const mf::SnapshotError& e) {
    std::cerr << "mf-replay: " << e.what() << '\n';
    return 5;
  } catch (const mf::InvariantViolation& e) {
    std::cerr << "mf-replay: invariant violation: " << e.what() << '\n';
    return 3;
  }
}
