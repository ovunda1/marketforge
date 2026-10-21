#pragma once

#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include "mf/commands.hpp"

namespace mf {

// Risk limits from YAML (docs/risk.md, "Configuration"). The engine never reads YAML:
// the file is turned into admin commands, which are sequenced and journaled before the
// first order like any other command, so replay needs only the journal.
//
// Built as its own library (mf_config) so the engine core has no YAML dependency.

class ConfigError : public std::runtime_error {
 public:
  using std::runtime_error::runtime_error;
};

// Parses a YAML document into admin commands with seq_no 0, in a fixed order: the
// instrument limits, then the default account limits, then each account's limits in
// ascending account id. The same document always gives the same commands. Throws
// ConfigError naming the key on unknown keys, wrong types or out-of-range values.
std::vector<Command> parse_risk_config(std::string_view yaml);
// Reads `path` and parses it; ConfigError messages are prefixed with the path.
std::vector<Command> load_risk_config(const std::string& path);

}  // namespace mf
