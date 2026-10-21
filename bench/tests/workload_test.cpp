#include "workload.hpp"

#include <gtest/gtest.h>

#include <algorithm>
#include <filesystem>
#include <map>
#include <string>
#include <variant>

#include "mf/engine.hpp"
#include "mf/journal.hpp"
#include "mf/replay.hpp"

namespace mf::bench {
namespace {

constexpr std::uint64_t kSeed = 20261021;

// Pinned digests. A change here means the workload changed (bump kGeneratorVersion) or
// the engine's output changed; an optimization must change neither. They are the same
// with libc++ and libstdc++ (CI builds both).
constexpr const char* kNarrowCommands20k =
    "428b49de29f4b602b7a0e7eac656da7c0f4f3365801b46011eb1ec274a97dacf";
constexpr const char* kWideCommands20k =
    "d03ec0e35e78781f481582626a32aa77c7dc45bdda4edab9322a420ee29752ac";
constexpr const char* kNarrowEvents200k =
    "cf757d76457286987295f741c147c92d884a54cd660aae72b0de2c31b3da80a3";
constexpr const char* kWideEvents200k =
    "386c46be49a832824b6d1f8b6a125b286c290c10b72f83959313e15bbb8c6bbb";

class WorkloadTest : public ::testing::TestWithParam<Profile> {};

INSTANTIATE_TEST_SUITE_P(Profiles, WorkloadTest, ::testing::Values(Profile::Narrow, Profile::Wide),
                         [](const auto& info) { return std::string(profile_name(info.param)); });

TEST(Rng, BelowStaysInRangeAndCoversIt) {
  Rng rng(1);
  std::map<std::uint64_t, int> seen;
  for (int i = 0; i < 70'000; ++i) {
    const auto v = rng.below(7);
    ASSERT_LT(v, 7u);
    ++seen[v];
  }
  ASSERT_EQ(seen.size(), 7u);
  for (const auto& [v, count] : seen) EXPECT_NEAR(count, 10'000, 500) << v;
}

TEST(Rng, BetweenIsInclusiveAndSameSeedRepeats) {
  Rng a(42), b(42);
  bool saw_lo = false, saw_hi = false;
  for (int i = 0; i < 10'000; ++i) {
    const auto v = a.between(-3, 3);
    ASSERT_EQ(v, b.between(-3, 3));
    ASSERT_GE(v, -3);
    ASSERT_LE(v, 3);
    saw_lo |= v == -3;
    saw_hi |= v == 3;
  }
  EXPECT_TRUE(saw_lo && saw_hi);
}

TEST(Profile, NamesRoundTrip) {
  for (const Profile p : {Profile::Narrow, Profile::Wide}) {
    EXPECT_EQ(parse_profile(profile_name(p)), p);
  }
  EXPECT_EQ(parse_profile("medium"), std::nullopt);
}

TEST_P(WorkloadTest, SameSeedSameCommandsOtherSeedDiffers) {
  const auto a = generate_workload(GetParam(), kSeed, 20'000);
  const auto b = generate_workload(GetParam(), kSeed, 20'000);
  const auto c = generate_workload(GetParam(), kSeed + 1, 20'000);
  EXPECT_EQ(command_digest(a), command_digest(b));
  EXPECT_NE(command_digest(a), command_digest(c));
}

TEST(Workload, ProfilesDiffer) {
  EXPECT_NE(command_digest(generate_workload(Profile::Narrow, kSeed, 5'000)),
            command_digest(generate_workload(Profile::Wide, kSeed, 5'000)));
}

TEST(Workload, PinnedCommandDigests) {
  EXPECT_EQ(command_digest(generate_workload(Profile::Narrow, kSeed, 20'000)), kNarrowCommands20k);
  EXPECT_EQ(command_digest(generate_workload(Profile::Wide, kSeed, 20'000)), kWideCommands20k);
}

// The correctness gate for every optimization: same workload, same event stream.
TEST(Workload, PinnedEventDigests) {
  EXPECT_EQ(event_digest(generate_workload(Profile::Narrow, kSeed, 200'000)), kNarrowEvents200k);
  EXPECT_EQ(event_digest(generate_workload(Profile::Wide, kSeed, 200'000)), kWideEvents200k);
}

TEST_P(WorkloadTest, CountsSeqNosAndTimestamps) {
  const auto w = generate_workload(GetParam(), kSeed, 50'000);
  ASSERT_EQ(w.commands.size(), w.admin_commands + 50'000);
  Timestamp last = 0;
  for (std::size_t i = 0; i < w.commands.size(); ++i) {
    ASSERT_EQ(seq_no_of(w.commands[i].command), i + 1);
    ASSERT_GE(w.commands[i].timestamp, last);
    last = w.commands[i].timestamp;
  }
  EXPECT_GT(last, w.commands.front().timestamp);
}

TEST_P(WorkloadTest, AdminPrefixSetsEveryLimit) {
  const auto w = generate_workload(GetParam(), kSeed, 10);
  ASSERT_EQ(w.admin_commands, 2u);
  const auto* inst = std::get_if<SetInstrumentLimits>(&w.commands[0].command);
  const auto* acct = std::get_if<SetAccountLimits>(&w.commands[1].command);
  ASSERT_TRUE(inst && acct);
  EXPECT_TRUE(inst->limits.max_qty && inst->limits.band_bps && inst->limits.reference_price);
  EXPECT_FALSE(acct->account_id);  // the defaults, for every account
  const auto& l = acct->limits;
  EXPECT_TRUE(l.max_notional && l.position_limit && l.credit && l.max_open_orders && l.rate);
}

TEST_P(WorkloadTest, MixIsWithinTolerance) {
  const auto w = generate_workload(GetParam(), kSeed, 200'000);
  const auto& s = w.stats;
  const double n = 200'000;
  EXPECT_NEAR(s.cancels / n, 0.27, 0.08);
  EXPECT_NEAR(s.replaces / n, 0.10, 0.03);
  EXPECT_NEAR(s.new_passive / n, 0.44, 0.08);
  EXPECT_NEAR(s.new_aggressive / n, 0.08, 0.03);
  EXPECT_NEAR(s.new_ioc / n, 0.08, 0.03);
  EXPECT_NEAR(s.new_market / n, 0.03, 0.02);
  EXPECT_EQ(s.cancels + s.replaces + s.new_passive + s.new_aggressive + s.new_ioc + s.new_market,
            200'000u);
}

// Cancels of unknown orders are cheap rejects: the benchmark must not be made of them.
TEST_P(WorkloadTest, CancelsAndReplacesHitRestingOrders) {
  const auto w = generate_workload(GetParam(), kSeed, 200'000);
  EXPECT_GE(w.stats.cancels_of_resting, w.stats.cancels * 9 / 10);
  EXPECT_EQ(w.stats.replaces_of_resting, w.stats.replaces);
}

TEST_P(WorkloadTest, BookStaysBoundedButNotEmpty) {
  const auto w = generate_workload(GetParam(), kSeed, 1'000'000);
  EXPECT_LE(w.stats.max_resting, GetParam() == Profile::Narrow ? 1'200u : 4'500u);
  Engine engine;
  std::vector<Event> events;
  for (const auto& c : w.commands) {
    engine.process(c.command, c.timestamp, events);
    events.clear();
  }
  EXPECT_GE(engine.book().order_count(), GetParam() == Profile::Narrow ? 500u : 2'000u);
}

TEST(Workload, WideSpreadsOverManyMoreLevelsAndPrices) {
  const auto narrow = generate_workload(Profile::Narrow, kSeed, 200'000);
  const auto wide = generate_workload(Profile::Wide, kSeed, 200'000);
  EXPECT_LE(narrow.stats.max_price - narrow.stats.min_price, 100);
  EXPECT_GE(wide.stats.max_price - wide.stats.min_price, 40'000);
  EXPECT_GT(narrow.stats.min_price, 0);
  EXPECT_GT(wide.stats.min_price, 0);

  const auto levels = [](const Workload& w) {
    Engine engine;
    std::vector<Event> events;
    for (const auto& c : w.commands) {
      engine.process(c.command, c.timestamp, events);
      events.clear();
    }
    return engine.book().level_count(Side::Buy) + engine.book().level_count(Side::Sell);
  };
  EXPECT_GT(levels(wide), 10 * levels(narrow));
}

// Generous limits: the gate runs every check, but almost nothing is a risk reject.
TEST_P(WorkloadTest, AlmostEveryOrderPassesTheGate) {
  const auto w = generate_workload(GetParam(), kSeed, 200'000);
  Engine engine;
  std::vector<Event> events;
  std::uint64_t risk_rejects = 0, acks = 0, trades = 0;
  for (const auto& c : w.commands) {
    engine.process(c.command, c.timestamp, events);
    for (const auto& e : events) {
      if (const auto* r = std::get_if<Reject>(&e)) {
        switch (r->reason) {
          case RejectReason::MaxQty: case RejectReason::MaxNotional: case RejectReason::PriceBand:
          case RejectReason::PositionLimit: case RejectReason::InsufficientCredit:
          case RejectReason::MaxOpenOrders: case RejectReason::RateLimit:
          case RejectReason::KillSwitch: case RejectReason::MarketClosed:
            ++risk_rejects;
            break;
          default:
            break;
        }
      }
      acks += std::holds_alternative<Ack>(e);
      trades += std::holds_alternative<Trade>(e);
    }
    events.clear();
  }
  EXPECT_LT(risk_rejects, acks / 100);
  EXPECT_GT(trades, 10'000u);  // the flow really matches
}

TEST_P(WorkloadTest, EventDigestIsWhatMfReplayPrints) {
  const auto w = generate_workload(GetParam(), kSeed, 30'000);
  const auto dir = std::filesystem::temp_directory_path() /
                   ("mf-bench-workload-" + std::string(profile_name(GetParam())));
  std::filesystem::remove_all(dir);
  std::filesystem::create_directories(dir);
  const std::string path = (dir / "w.mfj").string();
  {
    JournalWriter writer(path);
    for (const auto& c : w.commands) {
      writer.append(JournalRecord{.timestamp = c.timestamp, .command = c.command});
    }
  }
  EXPECT_EQ(replay(ReplayOptions{.journal_path = path}).sha256, event_digest(w));
  std::filesystem::remove_all(dir);
}

}  // namespace
}  // namespace mf::bench
