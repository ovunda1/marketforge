#include "mf/events.hpp"

#include <sstream>
#include <type_traits>

namespace mf {

namespace {

const char* to_string(Side side) { return side == Side::Buy ? "Buy" : "Sell"; }

const char* to_string(Liquidity liquidity) {
  return liquidity == Liquidity::Maker ? "Maker" : "Taker";
}

const char* to_string(RejectReason reason) {
  switch (reason) {
    case RejectReason::InvalidPrice: return "InvalidPrice";
    case RejectReason::InvalidQty: return "InvalidQty";
    case RejectReason::DuplicateOrderId: return "DuplicateOrderId";
    case RejectReason::UnknownOrder: return "UnknownOrder";
  }
  return "?";
}

}  // namespace

std::string to_string(const Event& event) {
  std::ostringstream os;
  std::visit(
      [&os](const auto& e) {
        using T = std::decay_t<decltype(e)>;
        if constexpr (std::is_same_v<T, Ack>) {
          os << "Ack{seq=" << e.seq_no << " id=" << e.order_id << ' ' << to_string(e.side)
             << ' ' << e.qty << '@' << e.price << '}';
        } else if constexpr (std::is_same_v<T, Reject>) {
          os << "Reject{seq=" << e.seq_no << " id=" << e.order_id << ' '
             << to_string(e.reason) << '}';
        } else if constexpr (std::is_same_v<T, Fill>) {
          os << "Fill{seq=" << e.seq_no << " trade=" << e.trade_id << " id=" << e.order_id
             << " acct=" << e.account_id << ' ' << to_string(e.side) << ' '
             << to_string(e.liquidity) << ' ' << e.qty << '@' << e.price
             << " leaves=" << e.leaves_qty << " cum=" << e.cum_qty << '}';
        } else if constexpr (std::is_same_v<T, Cancelled>) {
          os << "Cancelled{seq=" << e.seq_no << " id=" << e.order_id
             << " qty=" << e.cancelled_qty << '}';
        } else if constexpr (std::is_same_v<T, Trade>) {
          os << "Trade{seq=" << e.seq_no << " trade=" << e.trade_id << ' ' << e.qty << '@'
             << e.price << " maker=" << e.maker_order_id << " taker=" << e.taker_order_id
             << " aggressor=" << to_string(e.aggressor_side) << '}';
        }
      },
      event);
  return os.str();
}

}  // namespace mf
