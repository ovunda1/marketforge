#include "mf/risk_config.hpp"

#include <yaml-cpp/yaml.h>

#include <algorithm>
#include <charconv>
#include <fstream>
#include <limits>
#include <map>
#include <sstream>

#include "mf/risk.hpp"

namespace mf {

namespace {

[[noreturn]] void error(const std::string& where, const std::string& what) {
  throw ConfigError(where + ": " + what);
}

template <class T>
bool parse_whole(const std::string& text, T& out) {
  const auto [end, ec] = std::from_chars(text.data(), text.data() + text.size(), out);
  return ec == std::errc{} && end == text.data() + text.size();
}

std::int64_t integer(const YAML::Node& node, const std::string& where) {
  std::int64_t v{};
  if (!node.IsScalar() || !parse_whole(node.Scalar(), v)) {
    error(where, "expected an integer, got '" + (node.IsScalar() ? node.Scalar() : "?") + "'");
  }
  return v;
}

std::int64_t in_range(const YAML::Node& node, const std::string& where, std::int64_t lo,
                      std::int64_t hi) {
  const std::int64_t v = integer(node, where);
  if (v < lo || v > hi) {
    error(where, std::to_string(v) + " is outside " + std::to_string(lo) + ".." + std::to_string(hi));
  }
  return v;
}

// Returns false for an absent or null node; throws unless it is a mapping whose keys
// are all in `allowed`.
bool mapping(const YAML::Node& node, const std::string& where,
             std::initializer_list<std::string_view> allowed) {
  if (!node || node.IsNull()) return false;
  if (!node.IsMap()) error(where, "expected a mapping");
  for (const auto& entry : node) {
    const std::string key = entry.first.as<std::string>();
    if (std::find(allowed.begin(), allowed.end(), key) == allowed.end()) {
      error(where.empty() ? key : where + "." + key, "unknown key");
    }
  }
  return true;
}

constexpr std::int64_t kMax = std::numeric_limits<std::int64_t>::max();

std::optional<std::int64_t> field(const YAML::Node& map, const std::string& where,
                                  const char* key, std::int64_t lo, std::int64_t hi = kMax) {
  const YAML::Node node = map[key];
  if (!node) return std::nullopt;
  return in_range(node, where + "." + key, lo, hi);
}

InstrumentLimits read_instrument(const YAML::Node& node) {
  InstrumentLimits l;
  if (!mapping(node, "instrument", {"max_qty", "price_band_bps", "reference_price"})) return l;
  l.max_qty = field(node, "instrument", "max_qty", 1);
  l.band_bps = field(node, "instrument", "price_band_bps", 0, kMaxBandBps);
  l.reference_price = field(node, "instrument", "reference_price", 1);
  return l;
}

AccountLimits read_account(const YAML::Node& node, const std::string& where) {
  AccountLimits l;
  if (!mapping(node, where,
               {"max_notional", "position_limit", "credit", "max_open_orders", "rate"})) {
    return l;
  }
  l.max_notional = field(node, where, "max_notional", 0);
  l.position_limit = field(node, where, "position_limit", 0);
  l.credit = field(node, where, "credit", 0);
  l.max_open_orders = field(node, where, "max_open_orders", 0);
  const std::string rate_where = where + ".rate";
  if (mapping(node["rate"], rate_where, {"per_sec", "burst"})) {
    const auto per_sec = field(node["rate"], rate_where, "per_sec", 1, kMaxRate);
    const auto burst = field(node["rate"], rate_where, "burst", 1, kMaxRate);
    if (!per_sec) error(rate_where + ".per_sec", "required");
    if (!burst) error(rate_where + ".burst", "required");
    l.rate = RateLimit{.per_sec = *per_sec, .burst = *burst};
  }
  return l;
}

}  // namespace

std::vector<Command> parse_risk_config(std::string_view yaml) {
  YAML::Node root;
  try {
    root = YAML::Load(std::string(yaml));
  } catch (const YAML::Exception& e) {
    throw ConfigError(std::string("yaml: ") + e.what());
  }
  std::vector<Command> commands;
  if (!mapping(root, "", {"instrument", "accounts"})) return commands;

  if (root["instrument"]) {
    commands.push_back(SetInstrumentLimits{.limits = read_instrument(root["instrument"])});
  }
  std::optional<AccountLimits> defaults;
  std::map<AccountId, AccountLimits> accounts;  // ascending, whatever the file's order
  const YAML::Node accounts_node = root["accounts"];
  if (accounts_node && !accounts_node.IsNull()) {
    if (!accounts_node.IsMap()) error("accounts", "expected a mapping");
    for (const auto& entry : accounts_node) {
      const std::string key = entry.first.as<std::string>();
      const std::string where = "accounts." + key;
      if (key == "default") {
        defaults = read_account(entry.second, where);
        continue;
      }
      AccountId id{};
      if (!parse_whole(key, id)) error(where, "account id must be a non-negative integer or 'default'");
      if (!accounts.emplace(id, read_account(entry.second, where)).second) {
        error(where, "duplicate account " + std::to_string(id));
      }
    }
  }
  if (defaults) commands.push_back(SetAccountLimits{.account_id = std::nullopt, .limits = *defaults});
  for (const auto& [id, limits] : accounts) {
    commands.push_back(SetAccountLimits{.account_id = id, .limits = limits});
  }
  return commands;
}

std::vector<Command> load_risk_config(const std::string& path) {
  std::ifstream in(path);
  if (!in) throw ConfigError(path + ": cannot open risk config");
  std::stringstream text;
  text << in.rdbuf();
  try {
    return parse_risk_config(text.str());
  } catch (const ConfigError& e) {
    throw ConfigError(path + ": " + e.what());
  }
}

}  // namespace mf
