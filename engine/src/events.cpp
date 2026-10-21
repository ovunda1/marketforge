#include "mf/events.hpp"

#include <sstream>
#include <type_traits>

namespace mf {

namespace {

const char* to_string(Side side) { return side == Side::Buy ? "B" : "S"; }

const char* to_string(OrderType type) { return type == OrderType::Limit ? "LMT" : "MKT"; }

const char* to_string(TimeInForce tif) {
  switch (tif) {
    case TimeInForce::GTC: return "GTC";
    case TimeInForce::IOC: return "IOC";
    case TimeInForce::FOK: return "FOK";
    case TimeInForce::Day: return "DAY";
  }
  return "?";
}

const char* to_string(StpMode stp) {
  switch (stp) {
    case StpMode::CancelNewest: return "CN";
    case StpMode::CancelOldest: return "CO";
    case StpMode::CancelBoth: return "CB";
  }
  return "?";
}

const char* to_string(Liquidity liquidity) { return liquidity == Liquidity::Maker ? "M" : "T"; }

const char* to_string(RejectReason reason) {
  switch (reason) {
    case RejectReason::InvalidPrice: return "INVALID_PRICE";
    case RejectReason::InvalidQty: return "INVALID_QTY";
    case RejectReason::InvalidOrderType: return "INVALID_ORDER_TYPE";
    case RejectReason::DuplicateOrderId: return "DUPLICATE_ORDER_ID";
    case RejectReason::UnknownOrder: return "UNKNOWN_ORDER";
    case RejectReason::NoLiquidity: return "NO_LIQUIDITY";
    case RejectReason::FokNotFillable: return "FOK_NOT_FILLABLE";
    case RejectReason::PostOnlyWouldCross: return "POST_ONLY_WOULD_CROSS";
    case RejectReason::ReplaceQtyTooLow: return "REPLACE_QTY_TOO_LOW";
    case RejectReason::MarketClosed: return "MARKET_CLOSED";
    case RejectReason::KillSwitch: return "KILL_SWITCH";
    case RejectReason::RateLimit: return "RATE_LIMIT";
    case RejectReason::MaxQty: return "MAX_QTY";
    case RejectReason::PriceBand: return "PRICE_BAND";
    case RejectReason::MaxNotional: return "MAX_NOTIONAL";
    case RejectReason::MaxOpenOrders: return "MAX_OPEN_ORDERS";
    case RejectReason::PositionLimit: return "POSITION_LIMIT";
    case RejectReason::InsufficientCredit: return "INSUFFICIENT_CREDIT";
  }
  return "?";
}

const char* to_string(CancelReason reason) {
  switch (reason) {
    case CancelReason::User: return "USER";
    case CancelReason::Unfilled: return "UNFILLED";
    case CancelReason::SelfTrade: return "SELF_TRADE";
    case CancelReason::Expired: return "EXPIRED";
    case CancelReason::KillSwitch: return "KILL_SWITCH";
  }
  return "?";
}

const char* to_string(SessionState state) {
  switch (state) {
    case SessionState::PreOpen: return "PRE_OPEN";
    case SessionState::Continuous: return "CONTINUOUS";
    case SessionState::Halted: return "HALTED";
    case SessionState::Closed: return "CLOSED";
  }
  return "?";
}

const char* to_string(AdminKind kind) {
  switch (kind) {
    case AdminKind::InstrumentLimits: return "INSTRUMENT_LIMITS";
    case AdminKind::AccountLimits: return "ACCOUNT_LIMITS";
    case AdminKind::KillSwitch: return "KILL_SWITCH";
  }
  return "?";
}

const char* to_string(AdminRejectReason reason) {
  switch (reason) {
    case AdminRejectReason::InvalidTransition: return "INVALID_TRANSITION";
    case AdminRejectReason::InvalidLimits: return "INVALID_LIMITS";
    case AdminRejectReason::InvalidKillSwitch: return "INVALID_KILL_SWITCH";
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
          os << "ACK seq=" << e.seq_no << " id=" << e.order_id << " side=" << to_string(e.side)
             << " type=" << to_string(e.type) << " tif=" << to_string(e.tif)
             << " po=" << (e.post_only ? 1 : 0) << " stp=" << to_string(e.stp)
             << " px=" << e.price << " qty=" << e.qty;
        } else if constexpr (std::is_same_v<T, Reject>) {
          os << "REJ seq=" << e.seq_no << " id=" << e.order_id
             << " reason=" << to_string(e.reason);
        } else if constexpr (std::is_same_v<T, Fill>) {
          os << "FILL seq=" << e.seq_no << " trade=" << e.trade_id << " id=" << e.order_id
             << " acct=" << e.account_id << " side=" << to_string(e.side)
             << " liq=" << to_string(e.liquidity) << " px=" << e.price << " qty=" << e.qty
             << " leaves=" << e.leaves_qty << " cum=" << e.cum_qty;
        } else if constexpr (std::is_same_v<T, Cancelled>) {
          os << "CXL seq=" << e.seq_no << " id=" << e.order_id << " qty=" << e.cancelled_qty
             << " reason=" << to_string(e.reason);
        } else if constexpr (std::is_same_v<T, Trade>) {
          os << "TRD seq=" << e.seq_no << " trade=" << e.trade_id << " px=" << e.price
             << " qty=" << e.qty << " maker=" << e.maker_order_id
             << " taker=" << e.taker_order_id << " aggr=" << to_string(e.aggressor_side);
        } else if constexpr (std::is_same_v<T, Replaced>) {
          os << "RPL seq=" << e.seq_no << " id=" << e.order_id << " px=" << e.price
             << " qty=" << e.qty << " leaves=" << e.leaves_qty
             << " prio=" << (e.priority_kept ? "KEPT" : "LOST");
        } else if constexpr (std::is_same_v<T, SessionChanged>) {
          os << "SES seq=" << e.seq_no << " state=" << to_string(e.state);
        } else if constexpr (std::is_same_v<T, AdminAck>) {
          os << "ADM seq=" << e.seq_no << " kind=" << to_string(e.kind);
        } else if constexpr (std::is_same_v<T, AdminReject>) {
          os << "AREJ seq=" << e.seq_no << " reason=" << to_string(e.reason);
        }
      },
      event);
  return os.str();
}

}  // namespace mf
