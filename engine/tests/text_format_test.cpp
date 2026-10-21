#include <gtest/gtest.h>

#include <sstream>
#include <string>

#include "mf/text_format.hpp"
#include "test_helpers.hpp"

namespace mf {
namespace {

using test::run;

TEST(TextFormat, ParsesNewOrder) {
  const auto c = parse_command("N 7 42 3 S MKT IOC 0 CB 0 15");
  ASSERT_TRUE(c.has_value());
  EXPECT_EQ(*c, (Command{NewOrder{.seq_no = 7, .order_id = 42, .account_id = 3,
                                  .side = Side::Sell, .type = OrderType::Market,
                                  .tif = TimeInForce::IOC, .post_only = false,
                                  .stp = StpMode::CancelBoth, .price = 0, .qty = 15}}));
  const auto po = parse_command("N 8 43 1 B LMT GTC 1 CO 101 2");
  ASSERT_TRUE(po.has_value());
  EXPECT_EQ(*po, (Command{NewOrder{.seq_no = 8, .order_id = 43, .account_id = 1,
                                   .side = Side::Buy, .type = OrderType::Limit,
                                   .tif = TimeInForce::GTC, .post_only = true,
                                   .stp = StpMode::CancelOldest, .price = 101, .qty = 2}}));
}

TEST(TextFormat, ParsesNegativeFieldsSoInvalidOrdersCanBeTested) {
  const auto c = parse_command("N 1 1 1 B LMT FOK 0 CN -5 -3");
  ASSERT_TRUE(c.has_value());
  const auto& o = std::get<NewOrder>(*c);
  EXPECT_EQ(o.price, -5);
  EXPECT_EQ(o.qty, -3);
}

TEST(TextFormat, ParsesCancelAndReplace) {
  EXPECT_EQ(parse_command("C 3 9"), (std::optional<Command>{Cancel{.seq_no = 3, .order_id = 9}}));
  EXPECT_EQ(parse_command("R 4 9 101 12"),
            (std::optional<Command>{Replace{.seq_no = 4, .order_id = 9, .price = 101, .qty = 12}}));
}

TEST(TextFormat, IgnoresBlankLinesAndComments) {
  EXPECT_EQ(parse_command(""), std::nullopt);
  EXPECT_EQ(parse_command("   "), std::nullopt);
  EXPECT_EQ(parse_command("# seed=1"), std::nullopt);
  EXPECT_EQ(parse_command("C 3 9  # trailing comment"),
            (std::optional<Command>{Cancel{.seq_no = 3, .order_id = 9}}));
}

TEST(TextFormat, RejectsMalformedLines) {
  for (const char* bad : {
           "X 1 2",                           // unknown tag
           "C 1",                             // too few fields
           "C 1 2 3",                         // too many fields
           "C 1 x",                           // not a number
           "C -1 2",                          // seq_no is unsigned
           "N 1 1 1 Q LMT GTC 0 CN 100 5",    // bad side
           "N 1 1 1 B STP GTC 0 CN 100 5",    // bad type
           "N 1 1 1 B LMT GTD 0 CN 100 5",    // bad tif
           "N 1 1 1 B LMT GTC 2 CN 100 5",    // post-only must be 0 or 1
           "N 1 1 1 B LMT GTC 0 XX 100 5",    // bad stp
           "R 1 1 100 5x",                    // trailing garbage in a number
       }) {
    EXPECT_THROW(parse_command(bad), ParseError) << bad;
  }
}

TEST(TextFormat, FormatsEveryEventKind) {
  EXPECT_EQ(to_string(Ack{.seq_no = 1, .order_id = 10, .side = Side::Buy,
                          .type = OrderType::Market, .tif = TimeInForce::IOC,
                          .post_only = false, .stp = StpMode::CancelOldest, .price = 0,
                          .qty = 5}),
            "ACK seq=1 id=10 side=B type=MKT tif=IOC po=0 stp=CO px=0 qty=5");
  EXPECT_EQ(to_string(Ack{.seq_no = 2, .order_id = 11, .side = Side::Sell,
                          .tif = TimeInForce::GTC, .post_only = true,
                          .stp = StpMode::CancelBoth, .price = 101, .qty = 3}),
            "ACK seq=2 id=11 side=S type=LMT tif=GTC po=1 stp=CB px=101 qty=3");
  EXPECT_EQ(to_string(Reject{.seq_no = 3, .order_id = 12,
                             .reason = RejectReason::PostOnlyWouldCross}),
            "REJ seq=3 id=12 reason=POST_ONLY_WOULD_CROSS");
  EXPECT_EQ(to_string(Trade{.seq_no = 4, .trade_id = 1, .price = 100, .qty = 5,
                            .maker_order_id = 10, .taker_order_id = 12,
                            .aggressor_side = Side::Sell}),
            "TRD seq=4 trade=1 px=100 qty=5 maker=10 taker=12 aggr=S");
  EXPECT_EQ(to_string(Fill{.seq_no = 4, .trade_id = 1, .order_id = 10, .account_id = 7,
                           .side = Side::Buy, .liquidity = Liquidity::Maker, .price = 100,
                           .qty = 5, .leaves_qty = 0, .cum_qty = 5}),
            "FILL seq=4 trade=1 id=10 acct=7 side=B liq=M px=100 qty=5 leaves=0 cum=5");
  EXPECT_EQ(to_string(Fill{.seq_no = 4, .trade_id = 1, .order_id = 12, .account_id = 8,
                           .side = Side::Sell, .liquidity = Liquidity::Taker, .price = 100,
                           .qty = 5, .leaves_qty = 3, .cum_qty = 5}),
            "FILL seq=4 trade=1 id=12 acct=8 side=S liq=T px=100 qty=5 leaves=3 cum=5");
  EXPECT_EQ(to_string(Cancelled{.seq_no = 5, .order_id = 12, .cancelled_qty = 3,
                                .reason = CancelReason::Unfilled}),
            "CXL seq=5 id=12 qty=3 reason=UNFILLED");
  EXPECT_EQ(to_string(Cancelled{.seq_no = 5, .order_id = 9, .cancelled_qty = 1,
                                .reason = CancelReason::SelfTrade}),
            "CXL seq=5 id=9 qty=1 reason=SELF_TRADE");
  EXPECT_EQ(to_string(Cancelled{.seq_no = 6, .order_id = 9, .cancelled_qty = 1,
                                .reason = CancelReason::User}),
            "CXL seq=6 id=9 qty=1 reason=USER");
  EXPECT_EQ(to_string(Replaced{.seq_no = 7, .order_id = 9, .price = 101, .qty = 8,
                               .leaves_qty = 6, .priority_kept = false}),
            "RPL seq=7 id=9 px=101 qty=8 leaves=6 prio=LOST");
  EXPECT_EQ(to_string(Replaced{.seq_no = 8, .order_id = 9, .price = 101, .qty = 7,
                               .leaves_qty = 5, .priority_kept = true}),
            "RPL seq=8 id=9 px=101 qty=7 leaves=5 prio=KEPT");
}

TEST(TextFormat, FormatsEveryRejectReason) {
  const std::pair<RejectReason, const char*> cases[] = {
      {RejectReason::InvalidPrice, "INVALID_PRICE"},
      {RejectReason::InvalidQty, "INVALID_QTY"},
      {RejectReason::InvalidOrderType, "INVALID_ORDER_TYPE"},
      {RejectReason::DuplicateOrderId, "DUPLICATE_ORDER_ID"},
      {RejectReason::UnknownOrder, "UNKNOWN_ORDER"},
      {RejectReason::NoLiquidity, "NO_LIQUIDITY"},
      {RejectReason::FokNotFillable, "FOK_NOT_FILLABLE"},
      {RejectReason::PostOnlyWouldCross, "POST_ONLY_WOULD_CROSS"},
      {RejectReason::ReplaceQtyTooLow, "REPLACE_QTY_TOO_LOW"},
      {RejectReason::MarketClosed, "MARKET_CLOSED"},
      {RejectReason::KillSwitch, "KILL_SWITCH"},
      {RejectReason::RateLimit, "RATE_LIMIT"},
      {RejectReason::MaxQty, "MAX_QTY"},
      {RejectReason::PriceBand, "PRICE_BAND"},
      {RejectReason::MaxNotional, "MAX_NOTIONAL"},
      {RejectReason::MaxOpenOrders, "MAX_OPEN_ORDERS"},
      {RejectReason::PositionLimit, "POSITION_LIMIT"},
      {RejectReason::InsufficientCredit, "INSUFFICIENT_CREDIT"},
  };
  for (const auto& [reason, text] : cases) {
    EXPECT_EQ(to_string(Reject{.seq_no = 1, .order_id = 2, .reason = reason}),
              std::string("REJ seq=1 id=2 reason=") + text);
  }
}

TEST(TextFormat, FormatsTopOfBookAndDepth) {
  OrderBook book;
  EXPECT_EQ(format_top_of_book(0, book), "BOOK seq=0 bid=- ask=-");
  EXPECT_EQ(format_depth(book), "");

  run(book, NewOrder{.seq_no = 1, .order_id = 1, .account_id = 1, .side = Side::Buy,
                     .price = 100, .qty = 5});
  run(book, NewOrder{.seq_no = 2, .order_id = 2, .account_id = 2, .side = Side::Buy,
                     .price = 100, .qty = 2});
  run(book, NewOrder{.seq_no = 3, .order_id = 3, .account_id = 1, .side = Side::Buy,
                     .price = 99, .qty = 1});
  run(book, NewOrder{.seq_no = 4, .order_id = 4, .account_id = 1, .side = Side::Sell,
                     .price = 102, .qty = 8});
  EXPECT_EQ(format_top_of_book(4, book), "BOOK seq=4 bid=100x7 ask=102x8");
  EXPECT_EQ(format_depth(book),
            "LVL side=B px=100 qty=7 orders=1:5,2:2\n"
            "LVL side=B px=99 qty=1 orders=3:1\n"
            "LVL side=S px=102 qty=8 orders=4:8\n");
}

TEST(TextFormat, RunCommandsWritesEventsBookLinesAndDepth) {
  std::istringstream in(
      "# comment\n"
      "N 1 1 1 S LMT GTC 0 CN 100 5\n"
      "\n"
      "N 2 2 2 B LMT IOC 0 CN 100 2\n");
  std::ostringstream out;
  run_commands(in, out);
  EXPECT_EQ(out.str(),
            "ACK seq=1 id=1 side=S type=LMT tif=GTC po=0 stp=CN px=100 qty=5\n"
            "BOOK seq=1 bid=- ask=100x5\n"
            "ACK seq=2 id=2 side=B type=LMT tif=IOC po=0 stp=CN px=100 qty=2\n"
            "TRD seq=2 trade=1 px=100 qty=2 maker=1 taker=2 aggr=B\n"
            "FILL seq=2 trade=1 id=1 acct=1 side=S liq=M px=100 qty=2 leaves=3 cum=2\n"
            "FILL seq=2 trade=1 id=2 acct=2 side=B liq=T px=100 qty=2 leaves=0 cum=2\n"
            "BOOK seq=2 bid=- ask=100x3\n"
            "LVL side=S px=100 qty=3 orders=1:3\n");
}

TEST(TextFormat, RunCommandsReportsLineNumberOfMalformedCommand) {
  std::istringstream in("N 1 1 1 S LMT GTC 0 CN 100 5\nC 2\n");
  std::ostringstream out;
  try {
    run_commands(in, out);
    FAIL() << "expected ParseError";
  } catch (const ParseError& e) {
    EXPECT_EQ(std::string(e.what()).rfind("line 2: ", 0), 0u) << e.what();
  }
}

// --- M5: DAY, admin commands, clock lines and their events --------------------------

TEST(TextFormat, ParsesDayOrders) {
  const auto c = parse_command("N 1 1 1 B LMT DAY 1 CN 100 5");
  ASSERT_TRUE(c.has_value());
  EXPECT_EQ(std::get<NewOrder>(*c).tif, TimeInForce::Day);
  EXPECT_TRUE(std::get<NewOrder>(*c).post_only);
}

TEST(TextFormat, ParsesAdminCommands) {
  EXPECT_EQ(parse_command("S 1 PRE_OPEN"),
            (std::optional<Command>{SetSession{.seq_no = 1, .state = SessionState::PreOpen}}));
  EXPECT_EQ(parse_command("S 2 CONTINUOUS"),
            (std::optional<Command>{SetSession{.seq_no = 2, .state = SessionState::Continuous}}));
  EXPECT_EQ(parse_command("S 3 HALTED"),
            (std::optional<Command>{SetSession{.seq_no = 3, .state = SessionState::Halted}}));
  EXPECT_EQ(parse_command("S 4 CLOSED"),
            (std::optional<Command>{SetSession{.seq_no = 4, .state = SessionState::Closed}}));
  EXPECT_EQ(parse_command("L 5 40 - 100"),
            (std::optional<Command>{SetInstrumentLimits{
                .seq_no = 5, .limits = {.max_qty = 40, .reference_price = 100}}}));
  EXPECT_EQ(parse_command("L 6 - - -"),
            (std::optional<Command>{SetInstrumentLimits{.seq_no = 6, .limits = {}}}));
  EXPECT_EQ(parse_command("A 7 * 3000 60 - 6 20 5"),
            (std::optional<Command>{SetAccountLimits{
                .seq_no = 7, .account_id = std::nullopt,
                .limits = {.max_notional = 3000, .position_limit = 60, .max_open_orders = 6,
                           .rate = RateLimit{.per_sec = 20, .burst = 5}}}}));
  EXPECT_EQ(parse_command("A 8 12 - - -1 - - -"),
            (std::optional<Command>{SetAccountLimits{
                .seq_no = 8, .account_id = 12, .limits = {.credit = -1}}}));
  EXPECT_EQ(parse_command("K 9 * ON CANCEL"),
            (std::optional<Command>{KillSwitch{.seq_no = 9, .account_id = std::nullopt,
                                               .engaged = true, .cancel_open = true}}));
  EXPECT_EQ(parse_command("K 10 3 OFF KEEP"),
            (std::optional<Command>{KillSwitch{.seq_no = 10, .account_id = 3,
                                               .engaged = false, .cancel_open = false}}));
}

TEST(TextFormat, ParsesClockLines) {
  EXPECT_EQ(parse_line("T 1500"), (std::optional<TextLine>{ClockSet{.timestamp = 1500}}));
  EXPECT_EQ(parse_line("C 1 2"), (std::optional<TextLine>{Command{Cancel{.seq_no = 1, .order_id = 2}}}));
  EXPECT_EQ(parse_line("# x"), std::nullopt);
  EXPECT_THROW(parse_command("T 1500"), ParseError);  // not a command
}

TEST(TextFormat, RejectsMalformedAdminLines) {
  for (const char* bad : {
           "S 1 OPEN",                  // bad state
           "S 1",                       // missing state
           "L 1 40 -",                  // too few fields
           "L 1 x - -",                 // not a number
           "A 1 * 1 2 3 4 5",           // too few fields
           "A 1 * - - - - 20 -",        // rate without burst
           "A 1 * - - - - - 5",         // burst without rate
           "A 1 x - - - - - -",         // bad account
           "K 1 * MAYBE KEEP",          // bad engaged
           "K 1 * ON NOW",              // bad action
           "T",                         // missing time
           "T -5",                      // negative time
           "T 5 6",                     // too many fields
       }) {
    EXPECT_THROW(parse_line(bad), ParseError) << bad;
  }
}

TEST(TextFormat, FormatsAdminEventsAndNewCancelReasons) {
  EXPECT_EQ(to_string(SessionChanged{.seq_no = 1, .state = SessionState::PreOpen}),
            "SES seq=1 state=PRE_OPEN");
  EXPECT_EQ(to_string(SessionChanged{.seq_no = 1, .state = SessionState::Continuous}),
            "SES seq=1 state=CONTINUOUS");
  EXPECT_EQ(to_string(SessionChanged{.seq_no = 1, .state = SessionState::Halted}),
            "SES seq=1 state=HALTED");
  EXPECT_EQ(to_string(SessionChanged{.seq_no = 1, .state = SessionState::Closed}),
            "SES seq=1 state=CLOSED");
  EXPECT_EQ(to_string(AdminAck{.seq_no = 2, .kind = AdminKind::InstrumentLimits}),
            "ADM seq=2 kind=INSTRUMENT_LIMITS");
  EXPECT_EQ(to_string(AdminAck{.seq_no = 2, .kind = AdminKind::AccountLimits}),
            "ADM seq=2 kind=ACCOUNT_LIMITS");
  EXPECT_EQ(to_string(AdminAck{.seq_no = 2, .kind = AdminKind::KillSwitch}),
            "ADM seq=2 kind=KILL_SWITCH");
  EXPECT_EQ(to_string(AdminReject{.seq_no = 3, .reason = AdminRejectReason::InvalidTransition}),
            "AREJ seq=3 reason=INVALID_TRANSITION");
  EXPECT_EQ(to_string(AdminReject{.seq_no = 3, .reason = AdminRejectReason::InvalidLimits}),
            "AREJ seq=3 reason=INVALID_LIMITS");
  EXPECT_EQ(to_string(AdminReject{.seq_no = 3, .reason = AdminRejectReason::InvalidKillSwitch}),
            "AREJ seq=3 reason=INVALID_KILL_SWITCH");
  EXPECT_EQ(to_string(Cancelled{.seq_no = 4, .order_id = 5, .cancelled_qty = 6,
                                .reason = CancelReason::Expired}),
            "CXL seq=4 id=5 qty=6 reason=EXPIRED");
  EXPECT_EQ(to_string(Cancelled{.seq_no = 4, .order_id = 5, .cancelled_qty = 6,
                                .reason = CancelReason::KillSwitch}),
            "CXL seq=4 id=5 qty=6 reason=KILL_SWITCH");
  EXPECT_EQ(to_string(Ack{.seq_no = 1, .order_id = 1, .tif = TimeInForce::Day, .price = 1,
                          .qty = 1}),
            "ACK seq=1 id=1 side=B type=LMT tif=DAY po=0 stp=CN px=1 qty=1");
}

TEST(TextFormat, RunCommandsRunsThePrefixFirstAndHonorsClockLines) {
  // The prefix is numbered 1..k; the file continues at k + 1.
  const std::vector<Command> prefix = {
      SetAccountLimits{.account_id = 1, .limits = {.rate = RateLimit{.per_sec = 1, .burst = 1}}}};
  std::istringstream in(
      "N 2 1 1 B LMT GTC 0 CN 100 1\n"
      "N 3 2 1 B LMT GTC 0 CN 100 1\n"
      "T 1000000000\n"
      "N 4 3 1 B LMT GTC 0 CN 100 1\n");
  std::ostringstream out;
  run_commands(in, out, prefix);
  EXPECT_EQ(out.str(),
            "ADM seq=1 kind=ACCOUNT_LIMITS\n"
            "BOOK seq=1 bid=- ask=-\n"
            "ACK seq=2 id=1 side=B type=LMT tif=GTC po=0 stp=CN px=100 qty=1\n"
            "BOOK seq=2 bid=100x1 ask=-\n"
            "REJ seq=3 id=2 reason=RATE_LIMIT\n"
            "BOOK seq=3 bid=100x1 ask=-\n"
            "ACK seq=4 id=3 side=B type=LMT tif=GTC po=0 stp=CN px=100 qty=1\n"
            "BOOK seq=4 bid=100x2 ask=-\n"
            "LVL side=B px=100 qty=2 orders=1:1,3:1\n");
}

TEST(TextFormat, RunCommandsRejectsTimeGoingBackwards) {
  std::istringstream in("T 10\nC 1 1\nT 9\nC 2 1\n");
  std::ostringstream out;
  try {
    run_commands(in, out);
    FAIL() << "expected ParseError";
  } catch (const ParseError& e) {
    EXPECT_EQ(std::string(e.what()).rfind("line 3: ", 0), 0u) << e.what();
  }
}

}  // namespace
}  // namespace mf
