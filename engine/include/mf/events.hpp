#pragma once

#include <cstdint>
#include <string>
#include <variant>

#include "mf/types.hpp"

namespace mf {

// Outbound events. Every event carries the seq_no of the command that caused it.
//
// Ordering within one command is fixed:
//   1. Ack, Replaced or Reject. A Reject is the only event of its command.
//   2. For each match: Trade, maker Fill, taker Fill. A self-trade cancel of a resting
//      order appears at the point in the sweep where it happens.
//   3. The incoming order's own Cancelled (unfilled remainder or self-trade), if any.
// A user Cancel produces a single Cancelled.
//
// Reject vs Cancelled: reject when the order's contract can't be honored; Ack + Cancel
// when the outcome is within its contract. A Reject means the order was never accepted
// and the book is unchanged (market into an empty side, unfillable FOK, crossing
// post-only). An IOC that finds nothing to fill was honored, so it is acked and its
// remainder cancelled. See docs/order-types.md.

enum class RejectReason : std::uint8_t {
  InvalidPrice,        // limit price <= 0, or a market order with a price
  InvalidQty,          // qty <= 0
  InvalidOrderType,    // market not IOC, or post-only not a GTC limit
  DuplicateOrderId,    // order_id is already resting on the book
  UnknownOrder,        // cancel or replace of an order that is not resting
  NoLiquidity,         // market order into an empty opposite side
  FokNotFillable,      // FOK cannot be filled completely within its limit
  PostOnlyWouldCross,  // post-only (new or replaced) would take liquidity
  ReplaceQtyTooLow,    // replace to a total at or below the filled quantity
};

enum class CancelReason : std::uint8_t {
  User,       // Cancel command
  Unfilled,   // IOC or market remainder
  SelfTrade,  // self-trade prevention
};

enum class Liquidity : std::uint8_t { Maker, Taker };

struct Ack {
  SeqNo seq_no{};
  OrderId order_id{};
  Side side{};
  OrderType type{OrderType::Limit};
  TimeInForce tif{TimeInForce::GTC};
  bool post_only{};
  StpMode stp{StpMode::CancelNewest};
  Price price{};
  Qty qty{};

  bool operator==(const Ack&) const = default;
};

struct Reject {
  SeqNo seq_no{};
  OrderId order_id{};
  RejectReason reason{};

  bool operator==(const Reject&) const = default;
};

// One Fill per side of a match. A partial fill is a Fill with leaves_qty > 0.
struct Fill {
  SeqNo seq_no{};
  TradeId trade_id{};
  OrderId order_id{};
  AccountId account_id{};
  Side side{};
  Liquidity liquidity{};
  Price price{};
  Qty qty{};
  Qty leaves_qty{};  // still open after this fill
  Qty cum_qty{};     // total filled so far, including this fill

  bool operator==(const Fill&) const = default;
};

struct Cancelled {
  SeqNo seq_no{};
  OrderId order_id{};
  Qty cancelled_qty{};  // the open quantity removed
  CancelReason reason{};

  bool operator==(const Cancelled&) const = default;
};

// Public trade print. Price is always the resting (maker) order's price.
struct Trade {
  SeqNo seq_no{};
  TradeId trade_id{};
  Price price{};
  Qty qty{};
  OrderId maker_order_id{};
  OrderId taker_order_id{};
  Side aggressor_side{};

  bool operator==(const Trade&) const = default;
};

// Accepted cancel-replace. Any matching caused by a price change follows it.
struct Replaced {
  SeqNo seq_no{};
  OrderId order_id{};
  Price price{};
  Qty qty{};         // new total quantity
  Qty leaves_qty{};  // qty minus filled, before any matching
  bool priority_kept{};

  bool operator==(const Replaced&) const = default;
};

using Event = std::variant<Ack, Reject, Fill, Cancelled, Trade, Replaced>;

// Canonical single-line text form (docs/event-format.md), shared byte for byte with the
// Python reference engine. Also used in test failure messages.
std::string to_string(const Event& event);

}  // namespace mf
