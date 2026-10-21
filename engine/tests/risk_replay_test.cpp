#include <gtest/gtest.h>

#include <filesystem>
#include <fstream>
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

// Determinism with the risk gate (M5 main constraint): a journal with limits, admin
// commands and a moving clock replays to the same stream every time, equal to the
// driver's, from the start or from any snapshot, and reproduces every risk reject.
//
// engine/tests/journal/v2.cmds comes from tests/risk_flow.py (seed 2026, 3000 commands).

namespace mf {
namespace {

using test::TempDir;

const std::filesystem::path kFixtures = MF_JOURNAL_FIXTURE_DIR;

std::string read_text(const std::filesystem::path& path) {
  std::ifstream in(path);
  std::stringstream s;
  s << in.rdbuf();
  return s.str();
}

// Journals text lines through the real sequencer, setting the clock on "T" lines: what
// mf-journal does.
std::string make_journal(const TempDir& dir, const std::string& text) {
  SimulatedClock clock;
  JournalWriter writer(dir.file("risk.mfj"));
  Sequencer sequencer(clock, writer);
  std::istringstream in(text);
  for (std::string line; std::getline(in, line);) {
    const auto parsed = parse_line(line);
    if (!parsed) continue;
    if (const auto* set = std::get_if<ClockSet>(&*parsed)) {
      clock.set(set->timestamp);
    } else {
      sequencer.sequence(std::get<Command>(*parsed));
    }
  }
  return dir.file("risk.mfj");
}

std::string driver_output(const std::string& text) {
  std::istringstream in(text);
  std::ostringstream out;
  run_commands(in, out);
  return out.str();
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

std::string suffix_after(const std::string& full, SeqNo seq) {
  if (seq == 0) return full;
  const std::string marker = "BOOK seq=" + std::to_string(seq) + " ";
  const auto at = full.find(marker);
  if (at == std::string::npos) return "<no BOOK line for seq " + std::to_string(seq) + ">";
  return full.substr(full.find('\n', at) + 1);
}

class RiskReplay : public ::testing::Test {
 protected:
  void SetUp() override {
    text_ = read_text(kFixtures / "v2.cmds");
    ASSERT_FALSE(text_.empty()) << "missing v2.cmds";
    journal_ = make_journal(dir_, text_);
  }
  TempDir dir_;
  std::string text_;
  std::string journal_;
};

TEST_F(RiskReplay, ReplayIsDeterministicAndEqualsTheDriver) {
  std::string first;
  std::string second;
  const auto a = replay_to(journal_, &first);
  const auto b = replay_to(journal_, &second);
  EXPECT_EQ(a.sha256, b.sha256);
  EXPECT_EQ(first, second);
  EXPECT_EQ(first, driver_output(text_));
  EXPECT_EQ(a.sha256, sha256_hex(first));
  EXPECT_EQ(a.last_seq_no, 3000u);
}

// The fixture exercises every risk reject and every admin outcome, so the equality
// above covers reproducing each of them.
TEST_F(RiskReplay, StreamContainsEveryRiskRejectAndAdminOutcome) {
  std::string stream;
  replay_to(journal_, &stream);
  for (const char* needle :
       {"reason=MARKET_CLOSED", "reason=KILL_SWITCH", "reason=RATE_LIMIT", "reason=MAX_QTY",
        "reason=PRICE_BAND", "reason=MAX_NOTIONAL", "reason=MAX_OPEN_ORDERS",
        "reason=POSITION_LIMIT", "reason=INSUFFICIENT_CREDIT", "reason=DUPLICATE_ORDER_ID",
        "reason=INVALID_PRICE", "CXL seq=", "reason=EXPIRED", "SES seq=", "state=CLOSED",
        "state=PRE_OPEN", "state=HALTED", "ADM seq=", "kind=KILL_SWITCH",
        "kind=INSTRUMENT_LIMITS", "kind=ACCOUNT_LIMITS", "AREJ seq=",
        "reason=INVALID_TRANSITION", "reason=INVALID_LIMITS", "reason=INVALID_KILL_SWITCH",
        "TRD seq="}) {
    EXPECT_NE(stream.find(needle), std::string::npos) << needle;
  }
  // Rejects and cancels share some reason names; check the cancels by line.
  bool kill_cancel = false;
  std::istringstream lines(stream);
  for (std::string line; std::getline(lines, line);) {
    if (line.starts_with("CXL ") && line.ends_with("reason=KILL_SWITCH")) kill_cancel = true;
  }
  EXPECT_TRUE(kill_cancel) << "no kill-switch cancel in the stream";
}

TEST_F(RiskReplay, SnapshotPlusTailEqualsFullReplay) {
  std::ostringstream full_out;
  ReplayOptions options{.journal_path = journal_, .events_out = &full_out,
                        .snapshot_every = 250, .snapshot_dir = dir_.file("snaps")};
  std::filesystem::create_directories(options.snapshot_dir);
  const auto result = replay(options);
  const std::string full = full_out.str();
  ASSERT_EQ(result.snapshots_written.size(), 12u);
  for (const auto& path : result.snapshots_written) {
    const Snapshot snap = read_snapshot_file(path);
    std::string tail;
    const auto tail_result = replay_to(journal_, &tail, path);
    const std::string expected = suffix_after(full, snap.book.last_seq_no);
    ASSERT_EQ(tail, expected) << path;
    EXPECT_EQ(tail_result.sha256, sha256_hex(expected)) << path;
  }
}

// Pins the v2 format and engine behavior, like v1.mfj does for M4: a change that makes
// this journal unreadable, or replay differently, fails here.
TEST(RiskReplayFixture, CommittedV2JournalReplaysToItsRecordedHash) {
  std::string recorded;
  std::ifstream(kFixtures / "v2.sha256") >> recorded;
  ASSERT_EQ(recorded.size(), 64u) << "missing v2.sha256";
  const auto result = replay_to((kFixtures / "v2.mfj").string(), nullptr);
  EXPECT_EQ(result.sha256, recorded);
  EXPECT_EQ(result.sha256, sha256_hex(driver_output(read_text(kFixtures / "v2.cmds"))));
}

// A snapshot written by the M4 build resumes the v1 journal to the tail hash the M4
// build recorded.
TEST(RiskReplayFixture, CommittedV1SnapshotReplaysToItsRecordedTailHash) {
  std::string recorded;
  std::ifstream(kFixtures / "v1-250.sha256") >> recorded;
  ASSERT_EQ(recorded.size(), 64u);
  const auto result = replay_to((kFixtures / "v1.mfj").string(), nullptr,
                                (kFixtures / "v1-250.mfsnap").string());
  EXPECT_EQ(result.sha256, recorded);
}

}  // namespace
}  // namespace mf
