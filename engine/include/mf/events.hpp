#pragma once

#include <cstdint>
#include <string>
#include <variant>

#include "mf/types.hpp"

namespace mf {

// Outbound events. Every event carries the seq_no of the command that caused it.
//
// Ordering within one command is fixed: Ack (or Reject) first, then for each match
// Trade, maker Fill, taker Fill; Cancelled is emitted alone.

enum class RejectReason : std::uint8_t {
  InvalidPrice,      // price <= 0
  InvalidQty,        // qty <= 0
  DuplicateOrderId,  // order_id is already resting on the book
  UnknownOrder,      // cancel of an order that is not resting
};

enum class Liquidity : std::uint8_t { Maker, Taker };

struct Ack {
  SeqNo seq_no{};
  OrderId order_id{};
  Side side{};
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
  Qty cancelled_qty{};  // the open quantity removed from the book

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

using Event = std::variant<Ack, Reject, Fill, Cancelled, Trade>;

// Human-readable single-line form, for test failure messages and debugging.
std::string to_string(const Event& event);

}  // namespace mf
