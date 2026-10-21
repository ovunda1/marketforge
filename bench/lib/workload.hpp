#pragma once

#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "mf/commands.hpp"
#include "mf/types.hpp"

namespace mf::bench {

// Seeded synthetic order flow for the benchmarks. Deterministic on every platform: the
// random numbers come from xoshiro256** with integer-only bounded draws (no
// std::*_distribution, whose output differs between standard libraries, and no floating
// point), and the generator decides each command from a shadow Engine that has run all
// earlier ones, so cancels and replaces target orders that are really resting.
//
// Bump kGeneratorVersion whenever the generated commands change for a given seed.
inline constexpr int kGeneratorVersion = 1;

// narrow: a fixed mid and prices within a few ticks of it, so few, deep levels.
// wide: a drifting mid, prices spread over thousands of ticks and a few far-away
//       orders, so many shallow levels (the case a flat tick array finds hardest).
enum class Profile : std::uint8_t { Narrow, Wide };

std::string_view profile_name(Profile profile);
std::optional<Profile> parse_profile(std::string_view name);

// xoshiro256** (Blackman and Vigna), seeded through splitmix64.
class Rng {
 public:
  explicit Rng(std::uint64_t seed);
  std::uint64_t next();
  // Uniform in [0, bound), bound > 0, without modulo bias (Lemire's method).
  std::uint64_t below(std::uint64_t bound);
  // Uniform in [lo, hi], lo <= hi.
  std::int64_t between(std::int64_t lo, std::int64_t hi);
  // True with probability num / den.
  bool chance(std::uint64_t num, std::uint64_t den) { return below(den) < num; }

 private:
  std::uint64_t s_[4];
};

struct TimedCommand {
  Command command;
  Timestamp timestamp{};
};

// What the generator produced, for the tests that keep the workload honest.
struct WorkloadStats {
  std::uint64_t new_passive{};
  std::uint64_t new_aggressive{};
  std::uint64_t new_ioc{};
  std::uint64_t new_market{};
  std::uint64_t cancels{};
  std::uint64_t cancels_of_resting{};  // cancels whose order was resting when generated
  std::uint64_t replaces{};
  std::uint64_t replaces_of_resting{};
  std::uint64_t max_resting{};  // most orders resting at once
  Price min_price{};            // over every limit price generated
  Price max_price{};
};

struct Workload {
  Profile profile{};
  std::uint64_t seed{};
  // Risk limits first (admin commands at seq_no 1..admin_commands), then the flow.
  std::size_t admin_commands{};
  std::vector<TimedCommand> commands;
  WorkloadStats stats;
};

// `n` flow commands after the admin prefix. Every limit is configured and generous, so
// the gate runs every check and almost every order passes.
Workload generate_workload(Profile profile, std::uint64_t seed, std::size_t n);

// Lowercase hex SHA-256 of the commands as journal records (docs/journal-format.md).
std::string command_digest(const Workload& workload);
// Lowercase hex SHA-256 of the canonical event stream of a fresh Engine running the
// workload: what mf-replay prints for the same commands journaled.
std::string event_digest(const Workload& workload);

}  // namespace mf::bench
