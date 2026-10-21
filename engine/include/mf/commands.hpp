#pragma once

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

using Command = std::variant<NewOrder, Cancel, Replace>;

}  // namespace mf
