// mf-journal: converts canonical text commands (docs/event-format.md) into a binary
// journal (docs/journal-format.md) by feeding them through the real Sequencer with a
// SimulatedClock. The input's seq_nos must already be 1, 2, 3, ... so the journal
// replays to exactly what mf_driver writes for the text file.
//
// Usage: mf-journal <commands-file> <journal-file> [--start-ns T] [--step-ns S]
//   Timestamps start at T (default 0) and advance S ns per command (default 1000).
// Exit codes: 0 ok, 1 usage or I/O error, 2 malformed command or seq_no mismatch.
// Never overwrites an existing journal; on failure the partial output is removed.

#include <charconv>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <string>
#include <string_view>

#include "mf/clock.hpp"
#include "mf/journal.hpp"
#include "mf/sequencer.hpp"
#include "mf/text_format.hpp"

namespace {

int usage() {
  std::cerr << "usage: mf-journal <commands-file> <journal-file> [--start-ns T] [--step-ns S]\n";
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
  mf::Timestamp step = 1000;
  for (int i = 3; i < argc; i += 2) {
    const std::string_view flag = argv[i];
    if (i + 1 >= argc) return usage();
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
      std::string line;
      for (std::size_t line_no = 1; std::getline(in, line); ++line_no) {
        const auto command = mf::parse_command(line);
        if (!command) continue;
        const mf::SeqNo given = std::visit([](const auto& c) { return c.seq_no; }, *command);
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
