#include <gtest/gtest.h>

#include <string>
#include <vector>

#include "mf/risk_config.hpp"
#include "temp_dir.hpp"

// Risk limits from YAML (docs/risk.md, "Configuration"): parsed into admin commands in
// a fixed order, with errors that name the offending key.

namespace mf {
namespace {

using test::TempDir;
using test::write_bytes;

constexpr const char* kFull = R"(
# Instrument-wide limits.
instrument:
  max_qty: 1000
  price_band_bps: 500
  reference_price: 10000
accounts:
  1002:
    credit: 50000
  default:
    max_notional: 1000000
    position_limit: 500
    credit: 5000000
    max_open_orders: 50
    rate: {per_sec: 100, burst: 20}
  1001:
    max_open_orders: 5
    rate:
      per_sec: 10
      burst: 2
)";

TEST(RiskConfig, ParsesIntoAdminCommandsInAFixedOrder) {
  const std::vector<Command> expected = {
      SetInstrumentLimits{.limits = {.max_qty = 1000, .band_bps = 500,
                                     .reference_price = 10000}},
      SetAccountLimits{.account_id = std::nullopt,
                       .limits = {.max_notional = 1'000'000, .position_limit = 500,
                                  .credit = 5'000'000, .max_open_orders = 50,
                                  .rate = RateLimit{.per_sec = 100, .burst = 20}}},
      SetAccountLimits{.account_id = 1001,
                       .limits = {.max_open_orders = 5,
                                  .rate = RateLimit{.per_sec = 10, .burst = 2}}},
      SetAccountLimits{.account_id = 1002, .limits = {.credit = 50'000}},
  };
  EXPECT_EQ(parse_risk_config(kFull), expected);
}

TEST(RiskConfig, SameDocumentGivesTheSameCommands) {
  EXPECT_EQ(parse_risk_config(kFull), parse_risk_config(kFull));
}

TEST(RiskConfig, SectionsAreOptional) {
  EXPECT_EQ(parse_risk_config(""), std::vector<Command>{});
  EXPECT_EQ(parse_risk_config("instrument:\n  max_qty: 5\n"),
            (std::vector<Command>{SetInstrumentLimits{.limits = {.max_qty = 5}}}));
  EXPECT_EQ(parse_risk_config("accounts:\n  7: {}\n"),
            (std::vector<Command>{SetAccountLimits{.account_id = 7, .limits = {}}}));
}

void expect_error(const std::string& yaml, const std::string& fragment) {
  try {
    parse_risk_config(yaml);
    ADD_FAILURE() << "no error for:\n" << yaml;
  } catch (const ConfigError& e) {
    EXPECT_NE(std::string(e.what()).find(fragment), std::string::npos)
        << "message '" << e.what() << "' does not mention '" << fragment << "'";
  }
}

TEST(RiskConfig, RejectsBadDocumentsNamingTheKey) {
  expect_error("instrument:\n  max_qtty: 5\n", "max_qtty");
  expect_error("bogus: 1\n", "bogus");
  expect_error("instrument:\n  max_qty: -5\n", "max_qty");
  expect_error("instrument:\n  max_qty: 0\n", "max_qty");
  expect_error("instrument:\n  price_band_bps: 10001\n", "price_band_bps");
  expect_error("instrument:\n  max_qty: abc\n", "max_qty");
  expect_error("instrument:\n  max_qty: 1.5\n", "max_qty");
  expect_error("instrument: [1, 2]\n", "instrument");
  expect_error("accounts:\n  default:\n    credit: -1\n", "credit");
  expect_error("accounts:\n  default:\n    rate: {per_sec: 10}\n", "burst");
  expect_error("accounts:\n  default:\n    rate: {per_sec: 0, burst: 1}\n", "rate");
  expect_error("accounts:\n  abc:\n    credit: 1\n", "abc");
  expect_error("accounts:\n  -1:\n    credit: 1\n", "-1");
  expect_error("accounts:\n  1:\n    credit: 1\n  01:\n    credit: 2\n", "1");
  expect_error("instrument: {max_qty: 1\n", "yaml");
}

TEST(RiskConfig, LoadsFromAFile) {
  TempDir dir;
  write_bytes(dir.file("risk.yaml"), "instrument:\n  max_qty: 9\n");
  EXPECT_EQ(load_risk_config(dir.file("risk.yaml")),
            (std::vector<Command>{SetInstrumentLimits{.limits = {.max_qty = 9}}}));
  try {
    load_risk_config(dir.file("missing.yaml"));
    ADD_FAILURE() << "no error for a missing file";
  } catch (const ConfigError& e) {
    EXPECT_NE(std::string(e.what()).find("missing.yaml"), std::string::npos) << e.what();
  }
}

}  // namespace
}  // namespace mf
