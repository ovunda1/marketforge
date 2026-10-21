#pragma once

#include <gtest/gtest.h>

#include <cstdint>
#include <optional>
#include <string>
#include <vector>

#include "mf/engine.hpp"
#include "test_helpers.hpp"

namespace mf::test {

inline std::int64_t i64(Notional n) { return static_cast<std::int64_t>(n); }

inline std::string describe(const std::vector<Event>& events) {
  std::string out;
  for (const auto& e : events) (out += "\n  ") += to_string(e);
  return out.empty() ? "\n  <no events>" : out;
}

// The command's only event is a Reject with this reason.
inline ::testing::AssertionResult rejected(const std::vector<Event>& events, RejectReason reason) {
  if (events.size() == 1) {
    if (const auto* r = std::get_if<Reject>(&events[0]); r && r->reason == reason) {
      return ::testing::AssertionSuccess();
    }
  }
  return ::testing::AssertionFailure() << "expected a single " << to_string(Event{Reject{.reason = reason}})
                                       << ", got:" << describe(events);
}

// The command was accepted: its first event is not a Reject.
inline ::testing::AssertionResult acked(const std::vector<Event>& events) {
  if (!events.empty() && !std::holds_alternative<Reject>(events[0])) {
    return ::testing::AssertionSuccess();
  }
  return ::testing::AssertionFailure() << "expected the command to be accepted, got:"
                                       << describe(events);
}

// An engine plus a seq_no counter and the current simulated time, with one helper per
// command. Usable directly or as a gtest fixture base.
struct RiskFixture {
  Engine engine;
  SeqNo seq = 0;
  Timestamp now = 0;

  std::vector<Event> send(Command command) {
    std::visit([&](auto& c) { c.seq_no = ++seq; }, command);
    return run(engine, command, now);
  }
  void at(Timestamp t) { now = t; }

  std::vector<Event> limit(OrderId id, AccountId acct, Side side, Price px, Qty qty,
                           TimeInForce tif = TimeInForce::GTC) {
    return send(NewOrder{.order_id = id, .account_id = acct, .side = side, .tif = tif,
                         .price = px, .qty = qty});
  }
  std::vector<Event> market(OrderId id, AccountId acct, Side side, Qty qty) {
    return send(NewOrder{.order_id = id, .account_id = acct, .side = side,
                         .type = OrderType::Market, .tif = TimeInForce::IOC, .qty = qty});
  }
  std::vector<Event> cancel(OrderId id) { return send(Cancel{.order_id = id}); }
  std::vector<Event> replace(OrderId id, Price px, Qty qty) {
    return send(Replace{.order_id = id, .price = px, .qty = qty});
  }

  void set_instrument(const InstrumentLimits& limits) {
    const auto ev = send(SetInstrumentLimits{.limits = limits});
    EXPECT_EQ(ev, (std::vector<Event>{AdminAck{.seq_no = seq, .kind = AdminKind::InstrumentLimits}}));
  }
  void set_account(std::optional<AccountId> account, const AccountLimits& limits) {
    const auto ev = send(SetAccountLimits{.account_id = account, .limits = limits});
    EXPECT_EQ(ev, (std::vector<Event>{AdminAck{.seq_no = seq, .kind = AdminKind::AccountLimits}}));
  }
  std::vector<Event> kill(std::optional<AccountId> account, bool engaged, bool cancel_open = false) {
    auto ev = send(KillSwitch{.account_id = account, .engaged = engaged, .cancel_open = cancel_open});
    EXPECT_FALSE(ev.empty() || !std::holds_alternative<AdminAck>(ev[0])) << describe(ev);
    return ev;
  }
  std::vector<Event> session(SessionState state) {
    auto ev = send(SetSession{.state = state});
    EXPECT_FALSE(ev.empty() || !std::holds_alternative<SessionChanged>(ev[0])) << describe(ev);
    return ev;
  }
  // A one-lot trade at `px` between two accounts nobody else uses: sets the last trade.
  void trade_at(Price px) {
    limit(next_trade_id_, 98, Side::Sell, px, 1);
    limit(next_trade_id_ + 1, 99, Side::Buy, px, 1);
    next_trade_id_ += 2;
  }

 private:
  OrderId next_trade_id_ = 9'001;
};

}  // namespace mf::test
