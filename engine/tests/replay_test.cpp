#include <gtest/gtest.h>

#include <filesystem>
#include <fstream>
#include <random>
#include <sstream>
#include <string>
#include <vector>

#include "mf/checksum.hpp"
#include "mf/clock.hpp"
#include "mf/journal.hpp"
#include "mf/replay.hpp"
#include "mf/sequencer.hpp"
#include "mf/snapshot.hpp"
#include "mf/text_format.hpp"
#include "temp_dir.hpp"

// Deterministic replay (M4 done criterion): hashes, snapshot + tail, damaged journals.

namespace mf {
namespace {

using test::read_bytes;
using test::TempDir;
using test::write_bytes;

// Random command lines in the canonical text format, seq_no 1..n.
std::vector<std::string> random_commands(std::uint64_t seed, int n) {
  std::mt19937_64 rng(seed);
  std::vector<std::string> lines;
  const char* stp[] = {"CN", "CO", "CB"};
  const char* tif[] = {"GTC", "GTC", "IOC", "FOK"};
  for (int seq = 1; seq <= n; ++seq) {
    const auto r = rng() % 20;
    const std::string s = std::to_string(seq);
    const std::string id = std::to_string(1 + rng() % (seq + 5));  // reuses ids often
    const std::string px = std::to_string(95 + rng() % 11);
    const std::string qty = std::to_string(1 + rng() % 25);
    if (r < 3) {
      lines.push_back("C " + s + " " + id);
    } else if (r < 5) {
      lines.push_back("R " + s + " " + id + " " + px + " " + qty);
    } else if (r < 6) {
      lines.push_back("N " + s + " " + id + " " + std::to_string(1 + rng() % 4) +
                      (rng() % 2 ? " B" : " S") + " MKT IOC 0 " + stp[rng() % 3] + " 0 " + qty);
    } else {
      lines.push_back("N " + s + " " + id + " " + std::to_string(1 + rng() % 4) +
                      (rng() % 2 ? " B" : " S") + " LMT " + tif[rng() % 4] + " " +
                      (rng() % 8 == 0 ? "1" : "0") + " " + stp[rng() % 3] + " " + px + " " +
                      qty);
    }
  }
  return lines;
}

std::string join(const std::vector<std::string>& lines) {
  std::string out;
  for (const auto& l : lines) (out += l) += '\n';
  return out;
}

// What mf_driver writes for these commands.
std::string driver_output(const std::vector<std::string>& lines) {
  std::istringstream in(join(lines));
  std::ostringstream out;
  run_commands(in, out);
  return out.str();
}

// Journals text commands through the real sequencer, as mf-journal does.
std::string make_journal(const TempDir& dir, const std::vector<std::string>& lines,
                         const std::string& name = "j.mfj") {
  SimulatedClock clock(1'700'000'000'000'000'000, 1'000);
  JournalWriter writer(dir.file(name));
  Sequencer sequencer(clock, writer);
  for (const auto& line : lines) {
    if (auto c = parse_command(line)) sequencer.sequence(*c);
  }
  return dir.file(name);
}

ReplayResult replay_to(const std::string& journal, std::string* stream,
                       std::optional<std::string> snapshot = std::nullopt) {
  std::ostringstream out;
  ReplayOptions options{.journal_path = journal, .snapshot_path = std::move(snapshot),
                        .events_out = stream ? &out : nullptr};
  ReplayResult result = replay(options);
  if (stream) *stream = out.str();
  return result;
}

// The part of a full stream after command `seq`'s BOOK line (seq 0: all of it).
std::string suffix_after(const std::string& full, SeqNo seq) {
  if (seq == 0) return full;
  const std::string marker = "BOOK seq=" + std::to_string(seq) + " ";
  const auto at = full.find(marker);
  if (at == std::string::npos) return "<no BOOK line for seq " + std::to_string(seq) + ">";
  return full.substr(full.find('\n', at) + 1);
}

TEST(Replay, SameJournalTwiceGivesTheSameHash) {
  TempDir dir;
  const auto journal = make_journal(dir, random_commands(1, 5000));
  const auto a = replay_to(journal, nullptr);
  const auto b = replay_to(journal, nullptr);
  EXPECT_EQ(a.sha256, b.sha256);
  EXPECT_EQ(a.sha256.size(), 64u);
  EXPECT_EQ(a.commands, 5000u);
  EXPECT_EQ(a.last_seq_no, 5000u);
}

// The replayed stream is byte for byte what mf_driver writes for the same commands, so
// the M3 difftest (driver == Python reference) carries over to replay.
TEST(Replay, StreamAndHashMatchTheDriverOutput) {
  TempDir dir;
  const auto commands = random_commands(2, 5000);
  std::string stream;
  const auto result = replay_to(make_journal(dir, commands), &stream);
  const std::string expected = driver_output(commands);
  EXPECT_EQ(stream, expected);
  EXPECT_EQ(result.sha256, sha256_hex(expected));
}

TEST(Replay, DifferentCommandsGiveADifferentHash) {
  TempDir dir;
  EXPECT_NE(replay_to(make_journal(dir, random_commands(3, 500), "a.mfj"), nullptr).sha256,
            replay_to(make_journal(dir, random_commands(4, 500), "b.mfj"), nullptr).sha256);
}

TEST(Replay, EmptyJournalHashesTheEmptyStream) {
  TempDir dir;
  const auto result = replay_to(make_journal(dir, {}), nullptr);
  EXPECT_EQ(result.sha256, sha256_hex(""));
  EXPECT_EQ(result.commands, 0u);
}

// Snapshot + tail == full replay, at every periodic snapshot, at seq 0 and at the end.
TEST(Replay, SnapshotPlusTailEqualsFullReplay) {
  TempDir dir;
  constexpr int kCommands = 3000;
  const auto journal = make_journal(dir, random_commands(5, kCommands));
  std::string full;
  std::ostringstream full_out;
  ReplayOptions options{.journal_path = journal, .events_out = &full_out,
                        .snapshot_every = 1000, .snapshot_dir = dir.file("snaps")};
  std::filesystem::create_directories(options.snapshot_dir);
  const auto result = replay(options);
  full = full_out.str();

  std::vector<std::string> expected_names;
  for (const char* n : {"000000001000", "000000002000", "000000003000"}) {
    expected_names.push_back(dir.file("snaps/snap-" + std::string(n) + ".mfsnap"));
  }
  ASSERT_EQ(result.snapshots_written, expected_names);

  write_snapshot_file(dir.file("snap-0.mfsnap"),
                      Snapshot{.book = BookState{}, .last_timestamp = 0,
                               .journal_offset = kJournalHeaderSize});
  std::vector<std::string> snapshots = result.snapshots_written;
  snapshots.push_back(dir.file("snap-0.mfsnap"));

  for (const auto& path : snapshots) {
    const Snapshot snap = read_snapshot_file(path);
    std::string tail;
    const auto tail_result = replay_to(journal, &tail, path);
    const std::string expected = suffix_after(full, snap.book.last_seq_no);
    EXPECT_EQ(tail, expected) << path;
    EXPECT_EQ(tail_result.sha256, sha256_hex(expected)) << path;
    EXPECT_EQ(tail_result.commands, kCommands - snap.book.last_seq_no) << path;
    EXPECT_EQ(tail_result.last_seq_no, static_cast<SeqNo>(kCommands)) << path;
  }
}

TEST(Replay, SnapshotThatDoesNotMatchTheJournalIsAnError) {
  TempDir dir;
  const auto journal = make_journal(dir, random_commands(6, 100));
  Snapshot snap{.book = BookState{}, .last_timestamp = 0, .journal_offset = kJournalHeaderSize};
  snap.book.last_seq_no = 50;  // but the offset points at record 1
  write_snapshot_file(dir.file("wrong-seq.mfsnap"), snap);
  EXPECT_THROW(replay_to(journal, nullptr, dir.file("wrong-seq.mfsnap")), JournalError);

  snap.book.last_seq_no = 0;
  snap.journal_offset = read_bytes(journal).size() + 100;
  write_snapshot_file(dir.file("beyond.mfsnap"), snap);
  EXPECT_THROW(replay_to(journal, nullptr, dir.file("beyond.mfsnap")), JournalError);

  write_bytes(dir.file("junk.mfsnap"), "junk");
  EXPECT_THROW(replay_to(journal, nullptr, dir.file("junk.mfsnap")), SnapshotError);
}

// A damaged journal never yields a hash, even though the records before the damage
// are fine.
TEST(Replay, TruncatedOrCorruptJournalIsRejected) {
  TempDir dir;
  const std::string bytes = read_bytes(make_journal(dir, random_commands(7, 200)));

  write_bytes(dir.file("truncated.mfj"), bytes.substr(0, bytes.size() - 3));
  try {
    replay_to(dir.file("truncated.mfj"), nullptr);
    FAIL() << "expected JournalError";
  } catch (const JournalError& e) {
    EXPECT_EQ(e.kind(), JournalError::Kind::Truncated);
    EXPECT_NE(std::string(e.what()).find("truncated.mfj"), std::string::npos) << e.what();
    EXPECT_NE(std::string(e.what()).find("last good seq_no 199"), std::string::npos)
        << e.what();
  }

  std::string corrupt = bytes;
  corrupt[bytes.size() / 2] ^= 0x01;
  write_bytes(dir.file("corrupt.mfj"), corrupt);
  EXPECT_THROW(replay_to(dir.file("corrupt.mfj"), nullptr), JournalError);
}

// A journal committed with M4 must stay readable and replay to the same hash: catches
// any accidental change to the format or to engine behavior.
TEST(Replay, CommittedV1JournalReplaysToItsRecordedHash) {
  const std::filesystem::path dir = MF_JOURNAL_FIXTURE_DIR;
  std::ifstream hash_file(dir / "v1.sha256");
  ASSERT_TRUE(hash_file) << "missing " << (dir / "v1.sha256");
  std::string recorded;
  hash_file >> recorded;

  std::ifstream cmds(dir / "v1.cmds");
  std::vector<std::string> lines;
  for (std::string line; std::getline(cmds, line);) lines.push_back(line);

  const auto result = replay_to((dir / "v1.mfj").string(), nullptr);
  EXPECT_EQ(result.sha256, recorded);
  EXPECT_EQ(result.sha256, sha256_hex(driver_output(lines)));
}

}  // namespace
}  // namespace mf
