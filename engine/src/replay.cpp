#include "mf/replay.hpp"

#include <cstdio>
#include <filesystem>
#include <memory>

#include "mf/checksum.hpp"
#include "mf/journal.hpp"
#include "mf/order_book.hpp"
#include "mf/snapshot.hpp"
#include "mf/text_format.hpp"

namespace mf {

namespace {

std::string snapshot_name(SeqNo seq_no) {
  char name[32];
  std::snprintf(name, sizeof name, "snap-%012llu.mfsnap",
                static_cast<unsigned long long>(seq_no));
  return name;
}

}  // namespace

ReplayResult replay(const ReplayOptions& options) {
  JournalReader reader(options.journal_path);
  std::unique_ptr<OrderBook> book;
  if (options.snapshot_path) {
    const Snapshot snap = read_snapshot_file(*options.snapshot_path);
    try {
      book = OrderBook::from_state(snap.book);
    } catch (const InvariantViolation& e) {
      throw SnapshotError(*options.snapshot_path + ": inconsistent snapshot: " + e.what());
    }
    // The next record must be last_seq_no + 1: a snapshot from another journal fails here.
    reader.seek(snap.journal_offset, snap.book.last_seq_no, snap.last_timestamp);
  } else {
    book = std::make_unique<OrderBook>();
  }

  Sha256 hash;
  const auto emit = [&](std::string_view text) {
    hash.update(text);
    if (options.events_out) {
      options.events_out->write(text.data(), static_cast<std::streamsize>(text.size()));
    }
  };

  ReplayResult result;
  std::vector<Event> events;
  std::string text;
  while (const auto record = reader.next()) {
    events.clear();
    book->process(record->command, events);
    text.clear();
    append_command_output(events, record->seq_no(), *book, text);
    emit(text);
    ++result.commands;

    if (options.snapshot_every != 0 && record->seq_no() % options.snapshot_every == 0) {
      const std::string path =
          (std::filesystem::path(options.snapshot_dir) / snapshot_name(record->seq_no())).string();
      write_snapshot_file(path, Snapshot{.book = book->state(),
                                         .last_timestamp = record->timestamp,
                                         .journal_offset = reader.offset()});
      result.snapshots_written.push_back(path);
    }
  }
  emit(format_depth(*book));

  result.last_seq_no = reader.last_seq_no();
  result.sha256 = Sha256::hex(hash.finish());
  return result;
}

}  // namespace mf
