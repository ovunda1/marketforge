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
  Price price{};  // limit price in ticks
  Qty qty{};

  bool operator==(const NewOrder&) const = default;
};

struct Cancel {
  SeqNo seq_no{};
  OrderId order_id{};

  bool operator==(const Cancel&) const = default;
};

using Command = std::variant<NewOrder, Cancel>;

}  // namespace mf
