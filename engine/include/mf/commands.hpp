#pragma once

#include <cstdint>
#include <optional>
#include <variant>

#include "mf/types.hpp"

namespace mf {

// Inbound commands. seq_no is assigned by the sequencer and must be strictly increasing.

struct NewOrder {
  SeqNo seq_no{};
  OrderId order_id{};
  AccountId account_id{};
  Side side{};
  OrderType type{OrderType::Limit};
  TimeInForce tif{TimeInForce::GTC};
  bool post_only{};  // rest or reject; only valid on a GTC limit order
  StpMode stp{StpMode::CancelNewest};
  Price price{};  // limit price in ticks; must be 0 for a market order
  Qty qty{};

  bool operator==(const NewOrder&) const = default;
};

struct Cancel {
  SeqNo seq_no{};
  OrderId order_id{};

  bool operator==(const Cancel&) const = default;
};

// Cancel-replace of a resting order, keeping its order_id. A quantity decrease (or no
// change) at the same price keeps time priority; a quantity increase or a price change
// loses it. A price change behaves like cancel + new and can match.
struct Replace {
  SeqNo seq_no{};
  OrderId order_id{};
  Price price{};  // new limit price in ticks
  Qty qty{};      // new total quantity, including what has already filled

  bool operator==(const Replace&) const = default;
};

// --- Admin commands (M5) -------------------------------------------------------------
//
// Risk limits, kill switches and session transitions are commands too: the sequencer
// journals them like orders, so replay sees every change at the seq_no it took effect.
// Each takes effect from the next command. See docs/risk.md.

struct SetSession {
  SeqNo seq_no{};
  SessionState state{};

  bool operator==(const SetSession&) const = default;
};

// Per-instrument limits. An unset limit is not checked.
struct InstrumentLimits {
  std::optional<Qty> max_qty;                // MAX_QTY: order qty <= max_qty
  std::optional<std::int64_t> band_bps;      // PRICE_BAND: |px - ref| <= ref * bps / 10000
  std::optional<Price> reference_price;      // band reference when there is no trade or mid

  bool operator==(const InstrumentLimits&) const = default;
};

// Token bucket: refills per_sec tokens per second up to burst; an order costs one.
struct RateLimit {
  std::int64_t per_sec{};
  std::int64_t burst{};

  bool operator==(const RateLimit&) const = default;
};

// Per-account limits. An unset limit is not checked.
struct AccountLimits {
  std::optional<std::int64_t> max_notional;     // MAX_NOTIONAL: px * qty per order
  std::optional<Qty> position_limit;            // POSITION_LIMIT: |worst-case position|
  std::optional<std::int64_t> credit;           // INSUFFICIENT_CREDIT: open notional
  std::optional<std::int64_t> max_open_orders;  // MAX_OPEN_ORDERS
  std::optional<RateLimit> rate;                // RATE_LIMIT

  bool operator==(const AccountLimits&) const = default;
};

struct SetInstrumentLimits {
  SeqNo seq_no{};
  InstrumentLimits limits;  // replaces the whole set

  bool operator==(const SetInstrumentLimits&) const = default;
};

// Replaces an account's whole limit set, so one record states the account's limits.
// No account: the defaults, used by every account without limits of its own.
struct SetAccountLimits {
  SeqNo seq_no{};
  std::optional<AccountId> account_id;
  AccountLimits limits;

  bool operator==(const SetAccountLimits&) const = default;
};

// Engaged: new orders and replaces are rejected (KILL_SWITCH); cancels still work.
// cancel_open (only with engaged) also cancels the resting orders in scope.
// No account: the global switch, covering every account.
struct KillSwitch {
  SeqNo seq_no{};
  std::optional<AccountId> account_id;
  bool engaged{};
  bool cancel_open{};

  bool operator==(const KillSwitch&) const = default;
};

using Command = std::variant<NewOrder, Cancel, Replace, SetSession, SetInstrumentLimits,
                             SetAccountLimits, KillSwitch>;

// The commands an OrderBook processes; the Engine handles the rest.
using OrderCommand = std::variant<NewOrder, Cancel, Replace>;

inline SeqNo seq_no_of(const Command& command) {
  return std::visit([](const auto& c) { return c.seq_no; }, command);
}

}  // namespace mf
