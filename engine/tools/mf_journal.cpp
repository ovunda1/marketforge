// mf-journal: converts canonical text commands (docs/event-format.md) into a binary
// journal (docs/journal-format.md) by feeding them through the real Sequencer with a
// SimulatedClock. The input's seq_nos must already be 1, 2, 3, ... (k + 1, k + 2, ...
// after k config commands) so the journal replays to exactly what mf_driver writes for
// the same file and config.
//
// Usage: mf-journal <commands-file> <journal-file> [--config risk.yaml]
//                   [--start-ns T] [--step-ns S]
//   --config journals the YAML's admin commands first, as seq_nos 1..k at time T.
//   Timestamps start at T (default 0) and advance S ns per command (default 0); a
//   "T <ns>" line sets the clock. With S = 0 and T = 0 the timestamps are exactly the
//   ones mf_driver uses, which matters once a rate limit is configured.
// Exit codes: 0 ok, 1 usage or I/O error, 2 malformed command, seq_no mismatch, time
// going backwards or bad config.
// Never overwrites an existing journal; on failure the partial output is removed.

#include <charconv>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <string>
#include <string_view>
#include <vector>

#include "mf/clock.hpp"
#include "mf/journal.hpp"
#include "mf/risk_config.hpp"
#include "mf/sequencer.hpp"
#include "mf/text_format.hpp"

namespace {

int usage() {
  std::cerr << "usage: mf-journal <commands-file> <journal-file> [--config risk.yaml]\n"
               "                  [--start-ns T] [--step-ns S]\n";
  return 1;
}

bool parse_i64(std::string_view s, mf::Timestamp& out) {
  const auto [end, ec] = std::from_chars(s.data(), s.data() + s.size(), out);
  return ec == std::errc{} && end == s.data() + s.size();
}

}  // namespace

int main(int argc, char** argv) {
  if (argc < 3) return usage();
  mf::Timestamp start = 0;
  mf::Timestamp step = 0;
  std::vector<mf::Command> config;
  for (int i = 3; i < argc; i += 2) {
    const std::string_view flag = argv[i];
    if (i + 1 >= argc) return usage();
    if (flag == "--config") {
      try {
        config = mf::load_risk_config(argv[i + 1]);
      } catch (const mf::ConfigError& e) {
        std::cerr << "mf-journal: " << e.what() << '\n';
        return 2;
      }
      continue;
    }
    mf::Timestamp& target = flag == "--start-ns" ? start : step;
    if ((flag != "--start-ns" && flag != "--step-ns") || !parse_i64(argv[i + 1], target)) {
      return usage();
    }
  }
  if (step < 0) return usage();

  std::ifstream in(argv[1]);
  if (!in) {
    std::cerr << "mf-journal: cannot open " << argv[1] << '\n';
    return 1;
  }
  const std::string out_path = argv[2];
  int status = 0;
  try {
    mf::JournalWriter writer(out_path);
    try {
      mf::SimulatedClock clock(start, step);
      mf::Sequencer sequencer(clock, writer);
      for (const auto& command : config) sequencer.sequence(command);
      std::string line;
      for (std::size_t line_no = 1; std::getline(in, line); ++line_no) {
        const auto parsed = mf::parse_line(line);
        if (!parsed) continue;
        if (const auto* set = std::get_if<mf::ClockSet>(&*parsed)) {
          if (set->timestamp < clock.peek()) {
            std::cerr << "mf-journal: line " << line_no << ": time " << set->timestamp
                      << " is before " << clock.peek() << '\n';
            status = 2;
            break;
          }
          clock.set(set->timestamp);
          continue;
        }
        const auto* command = &std::get<mf::Command>(*parsed);
        const mf::SeqNo given = mf::seq_no_of(*command);
        const mf::SeqNo next = sequencer.last_seq_no() + 1;
        if (given != next) {
          std::cerr << "mf-journal: line " << line_no << ": seq_no " << given
                    << " does not match the journal's next seq_no " << next << '\n';
          status = 2;
          break;
        }
        sequencer.sequence(*command);
      }
    } catch (const mf::ParseError& e) {
      std::cerr << "mf-journal: " << e.what() << '\n';
      status = 2;
    } catch (const mf::JournalError& e) {
      std::cerr << "mf-journal: " << e.what() << '\n';
      status = 1;
    }
  } catch (const mf::JournalError& e) {  // could not create the output
    std::cerr << "mf-journal: " << e.what() << '\n';
    return 1;
  }
  if (status != 0) std::remove(out_path.c_str());  // only reached if we created it
  return status;
}
