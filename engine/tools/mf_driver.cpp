// mf_driver: reads canonical commands from a file, runs them through one Engine (risk
// gate + order book) and writes the canonical event stream (docs/event-format.md). The
// Python reference engine (reference/run.py) writes the same format for the book-only
// subset, so the differential test compares the two outputs line by line.
//
// Usage: mf_driver [--config risk.yaml] <commands-file> [<events-file>]
//   (events go to stdout by default). --config runs the YAML's admin commands first,
//   as seq_nos 1..k at time 0, exactly as mf-journal --config journals them.
// Exit codes: 0 ok, 1 usage or I/O error, 2 malformed command or bad config,
// 3 invariant violation.

#include <fstream>
#include <iostream>
#include <string_view>
#include <vector>

#include "mf/risk_config.hpp"
#include "mf/text_format.hpp"

int main(int argc, char** argv) {
  std::vector<mf::Command> config;
  if (argc >= 3 && std::string_view(argv[1]) == "--config") {
    try {
      config = mf::load_risk_config(argv[2]);
    } catch (const mf::ConfigError& e) {
      std::cerr << "mf_driver: " << e.what() << '\n';
      return 2;
    }
    argv += 2;
    argc -= 2;
  }
  if (argc < 2 || argc > 3) {
    std::cerr << "usage: mf_driver [--config risk.yaml] <commands-file> [<events-file>]\n";
    return 1;
  }
  std::ifstream in(argv[1]);
  if (!in) {
    std::cerr << "mf_driver: cannot open " << argv[1] << '\n';
    return 1;
  }
  std::ofstream file_out;
  if (argc == 3) {
    file_out.open(argv[2]);
    if (!file_out) {
      std::cerr << "mf_driver: cannot open " << argv[2] << '\n';
      return 1;
    }
  }
  std::ostream& out = argc == 3 ? static_cast<std::ostream&>(file_out) : std::cout;

  try {
    mf::run_commands(in, out, config);
  } catch (const mf::ParseError& e) {
    std::cerr << "mf_driver: " << e.what() << '\n';
    return 2;
  } catch (const mf::InvariantViolation& e) {
    std::cerr << "mf_driver: invariant violation: " << e.what() << '\n';
    return 3;
  }
  out.flush();
  return out ? 0 : 1;
}
