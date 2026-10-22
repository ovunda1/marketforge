// Enums, commands, events and the other plain values, as immutable Python classes.
// Every attribute read returns a copy, so no Python object refers into engine memory.

#include <pybind11/native_enum.h>

#include "bindings.hpp"
#include "convert.hpp"
#include "mf/commands.hpp"
#include "mf/events.hpp"
#include "mf/journal.hpp"
#include "mf/order_book.hpp"
#include "mf/risk.hpp"
#include "mf/snapshot.hpp"
#include "mf/text_format.hpp"

namespace mfpy {
namespace {

void bind_enums(py::module_& m) {
  py::native_enum<mf::Side>(m, "Side", "enum.Enum")
      .value("BUY", mf::Side::Buy)
      .value("SELL", mf::Side::Sell)
      .finalize();
  py::native_enum<mf::OrderType>(m, "OrderType", "enum.Enum")
      .value("LIMIT", mf::OrderType::Limit)
      .value("MARKET", mf::OrderType::Market)
      .finalize();
  py::native_enum<mf::TimeInForce>(m, "TimeInForce", "enum.Enum")
      .value("GTC", mf::TimeInForce::GTC)
      .value("IOC", mf::TimeInForce::IOC)
      .value("FOK", mf::TimeInForce::FOK)
      .value("DAY", mf::TimeInForce::Day)
      .finalize();
  py::native_enum<mf::SessionState>(m, "SessionState", "enum.Enum")
      .value("PRE_OPEN", mf::SessionState::PreOpen)
      .value("CONTINUOUS", mf::SessionState::Continuous)
      .value("HALTED", mf::SessionState::Halted)
      .value("CLOSED", mf::SessionState::Closed)
      .finalize();
  py::native_enum<mf::StpMode>(m, "StpMode", "enum.Enum")
      .value("CANCEL_NEWEST", mf::StpMode::CancelNewest)
      .value("CANCEL_OLDEST", mf::StpMode::CancelOldest)
      .value("CANCEL_BOTH", mf::StpMode::CancelBoth)
      .finalize();
  py::native_enum<mf::RejectReason>(m, "RejectReason", "enum.Enum")
      .value("INVALID_PRICE", mf::RejectReason::InvalidPrice)
      .value("INVALID_QTY", mf::RejectReason::InvalidQty)
      .value("INVALID_ORDER_TYPE", mf::RejectReason::InvalidOrderType)
      .value("DUPLICATE_ORDER_ID", mf::RejectReason::DuplicateOrderId)
      .value("UNKNOWN_ORDER", mf::RejectReason::UnknownOrder)
      .value("NO_LIQUIDITY", mf::RejectReason::NoLiquidity)
      .value("FOK_NOT_FILLABLE", mf::RejectReason::FokNotFillable)
      .value("POST_ONLY_WOULD_CROSS", mf::RejectReason::PostOnlyWouldCross)
      .value("REPLACE_QTY_TOO_LOW", mf::RejectReason::ReplaceQtyTooLow)
      .value("MARKET_CLOSED", mf::RejectReason::MarketClosed)
      .value("KILL_SWITCH", mf::RejectReason::KillSwitch)
      .value("RATE_LIMIT", mf::RejectReason::RateLimit)
      .value("MAX_QTY", mf::RejectReason::MaxQty)
      .value("PRICE_BAND", mf::RejectReason::PriceBand)
      .value("MAX_NOTIONAL", mf::RejectReason::MaxNotional)
      .value("MAX_OPEN_ORDERS", mf::RejectReason::MaxOpenOrders)
      .value("POSITION_LIMIT", mf::RejectReason::PositionLimit)
      .value("INSUFFICIENT_CREDIT", mf::RejectReason::InsufficientCredit)
      .finalize();
  py::native_enum<mf::CancelReason>(m, "CancelReason", "enum.Enum")
      .value("USER", mf::CancelReason::User)
      .value("UNFILLED", mf::CancelReason::Unfilled)
      .value("SELF_TRADE", mf::CancelReason::SelfTrade)
      .value("EXPIRED", mf::CancelReason::Expired)
      .value("KILL_SWITCH", mf::CancelReason::KillSwitch)
      .finalize();
  py::native_enum<mf::AdminKind>(m, "AdminKind", "enum.Enum")
      .value("INSTRUMENT_LIMITS", mf::AdminKind::InstrumentLimits)
      .value("ACCOUNT_LIMITS", mf::AdminKind::AccountLimits)
      .value("KILL_SWITCH", mf::AdminKind::KillSwitch)
      .finalize();
  py::native_enum<mf::AdminRejectReason>(m, "AdminRejectReason", "enum.Enum")
      .value("INVALID_TRANSITION", mf::AdminRejectReason::InvalidTransition)
      .value("INVALID_LIMITS", mf::AdminRejectReason::InvalidLimits)
      .value("INVALID_KILL_SWITCH", mf::AdminRejectReason::InvalidKillSwitch)
      .finalize();
  py::native_enum<mf::Liquidity>(m, "Liquidity", "enum.Enum")
      .value("MAKER", mf::Liquidity::Maker)
      .value("TAKER", mf::Liquidity::Taker)
      .finalize();
  using Kind = mf::JournalError::Kind;
  py::native_enum<Kind>(m, "JournalErrorKind", "enum.Enum")
      .value("IO", Kind::Io)
      .value("BAD_HEADER", Kind::BadHeader)
      .value("TRUNCATED", Kind::Truncated)
      .value("CORRUPT", Kind::Corrupt)
      .value("SEQUENCE_GAP", Kind::SequenceGap)
      .value("TIMESTAMP_BACKWARDS", Kind::TimestampBackwards)
      .finalize();
}

// The command's copy with another seq_no (config commands come with seq_no 0).
template <class T>
void with_seq_no(ValueClass<T>& c) {
  c.def(
      "with_seq_no",
      [](const T& t, py::handle seq_no) {
        T copy = t;
        copy.seq_no = to_int<mf::SeqNo>(seq_no, "seq_no");
        return copy;
      },
      py::arg("seq_no"), "A copy of this command with another seq_no.");
}

void bind_commands(py::module_& m) {
  ValueClass<mf::RateLimit>(m, "RateLimit", "Token bucket: per_sec orders per second, up to burst.")
      .field("per_sec", &mf::RateLimit::per_sec)
      .field("burst", &mf::RateLimit::burst)
      .finish();
  ValueClass<mf::InstrumentLimits>(m, "InstrumentLimits", "Per-instrument limits; None is unchecked.")
      .field("max_qty", &mf::InstrumentLimits::max_qty, Arg::Optional)
      .field("band_bps", &mf::InstrumentLimits::band_bps, Arg::Optional)
      .field("reference_price", &mf::InstrumentLimits::reference_price, Arg::Optional)
      .finish();
  ValueClass<mf::AccountLimits>(m, "AccountLimits", "Per-account limits; None is unchecked.")
      .field("max_notional", &mf::AccountLimits::max_notional, Arg::Optional)
      .field("position_limit", &mf::AccountLimits::position_limit, Arg::Optional)
      .field("credit", &mf::AccountLimits::credit, Arg::Optional)
      .field("max_open_orders", &mf::AccountLimits::max_open_orders, Arg::Optional)
      .field("rate", &mf::AccountLimits::rate, Arg::Optional)
      .finish();

  ValueClass<mf::NewOrder> new_order(m, "NewOrder", "A new order. price is 0 for a market order.");
  new_order.field("seq_no", &mf::NewOrder::seq_no)
      .field("order_id", &mf::NewOrder::order_id)
      .field("account_id", &mf::NewOrder::account_id)
      .field("side", &mf::NewOrder::side)
      .field("type", &mf::NewOrder::type, Arg::Optional)
      .field("tif", &mf::NewOrder::tif, Arg::Optional)
      .field("post_only", &mf::NewOrder::post_only, Arg::Optional)
      .field("stp", &mf::NewOrder::stp, Arg::Optional)
      .field("price", &mf::NewOrder::price, Arg::Optional)
      .field("qty", &mf::NewOrder::qty);
  with_seq_no(new_order);
  new_order.finish();

  ValueClass<mf::Cancel> cancel(m, "Cancel", "Cancel a resting order.");
  cancel.field("seq_no", &mf::Cancel::seq_no).field("order_id", &mf::Cancel::order_id);
  with_seq_no(cancel);
  cancel.finish();

  ValueClass<mf::Replace> replace(m, "Replace",
                                  "Cancel-replace a resting order; qty is the new total.");
  replace.field("seq_no", &mf::Replace::seq_no)
      .field("order_id", &mf::Replace::order_id)
      .field("price", &mf::Replace::price)
      .field("qty", &mf::Replace::qty);
  with_seq_no(replace);
  replace.finish();

  ValueClass<mf::SetSession> session(m, "SetSession", "Session transition (admin).");
  session.field("seq_no", &mf::SetSession::seq_no).field("state", &mf::SetSession::state);
  with_seq_no(session);
  session.finish();

  ValueClass<mf::SetInstrumentLimits> inst(m, "SetInstrumentLimits",
                                           "Replace the instrument limits (admin).");
  inst.field("seq_no", &mf::SetInstrumentLimits::seq_no)
      .field("limits", &mf::SetInstrumentLimits::limits);
  with_seq_no(inst);
  inst.finish();

  ValueClass<mf::SetAccountLimits> acct(
      m, "SetAccountLimits", "Replace an account's limits; account_id None sets the defaults (admin).");
  acct.field("seq_no", &mf::SetAccountLimits::seq_no)
      .field("account_id", &mf::SetAccountLimits::account_id, Arg::Optional)
      .field("limits", &mf::SetAccountLimits::limits);
  with_seq_no(acct);
  acct.finish();

  ValueClass<mf::KillSwitch> kill(
      m, "KillSwitch", "Engage or release a kill switch; account_id None is global (admin).");
  kill.field("seq_no", &mf::KillSwitch::seq_no)
      .field("account_id", &mf::KillSwitch::account_id, Arg::Optional)
      .field("engaged", &mf::KillSwitch::engaged)
      .field("cancel_open", &mf::KillSwitch::cancel_open, Arg::Optional);
  with_seq_no(kill);
  kill.finish();

  ValueClass<mf::ClockSet>(m, "ClockSet", "A 'T <ns>' line: the time of the commands after it.")
      .field("timestamp", &mf::ClockSet::timestamp)
      .finish();
}

// Events also get __str__: the canonical line (docs/event-format.md).
template <class T>
ValueClass<T>& canonical_str(ValueClass<T>& c) {
  c.def("__str__", [](const T& e) { return mf::to_string(mf::Event{e}); });
  return c;
}

void bind_events(py::module_& m) {
  ValueClass<mf::Ack> ack(m, "Ack", "The order was accepted.");
  ack.field("seq_no", &mf::Ack::seq_no)
      .field("order_id", &mf::Ack::order_id)
      .field("side", &mf::Ack::side)
      .field("type", &mf::Ack::type)
      .field("tif", &mf::Ack::tif)
      .field("post_only", &mf::Ack::post_only)
      .field("stp", &mf::Ack::stp)
      .field("price", &mf::Ack::price)
      .field("qty", &mf::Ack::qty);
  canonical_str(ack).finish();

  ValueClass<mf::Reject> reject(m, "Reject", "The command was refused; the book is unchanged.");
  reject.field("seq_no", &mf::Reject::seq_no)
      .field("order_id", &mf::Reject::order_id)
      .field("reason", &mf::Reject::reason);
  canonical_str(reject).finish();

  ValueClass<mf::Fill> fill(m, "Fill", "One side of a match.");
  fill.field("seq_no", &mf::Fill::seq_no)
      .field("trade_id", &mf::Fill::trade_id)
      .field("order_id", &mf::Fill::order_id)
      .field("account_id", &mf::Fill::account_id)
      .field("side", &mf::Fill::side)
      .field("liquidity", &mf::Fill::liquidity)
      .field("price", &mf::Fill::price)
      .field("qty", &mf::Fill::qty)
      .field("leaves_qty", &mf::Fill::leaves_qty)
      .field("cum_qty", &mf::Fill::cum_qty);
  canonical_str(fill).finish();

  ValueClass<mf::Cancelled> cancelled(m, "Cancelled", "Open quantity removed from the book.");
  cancelled.field("seq_no", &mf::Cancelled::seq_no)
      .field("order_id", &mf::Cancelled::order_id)
      .field("cancelled_qty", &mf::Cancelled::cancelled_qty)
      .field("reason", &mf::Cancelled::reason);
  canonical_str(cancelled).finish();

  ValueClass<mf::Trade> trade(m, "Trade", "Public trade print at the maker's price.");
  trade.field("seq_no", &mf::Trade::seq_no)
      .field("trade_id", &mf::Trade::trade_id)
      .field("price", &mf::Trade::price)
      .field("qty", &mf::Trade::qty)
      .field("maker_order_id", &mf::Trade::maker_order_id)
      .field("taker_order_id", &mf::Trade::taker_order_id)
      .field("aggressor_side", &mf::Trade::aggressor_side);
  canonical_str(trade).finish();

  ValueClass<mf::Replaced> replaced(m, "Replaced", "Accepted cancel-replace.");
  replaced.field("seq_no", &mf::Replaced::seq_no)
      .field("order_id", &mf::Replaced::order_id)
      .field("price", &mf::Replaced::price)
      .field("qty", &mf::Replaced::qty)
      .field("leaves_qty", &mf::Replaced::leaves_qty)
      .field("priority_kept", &mf::Replaced::priority_kept);
  canonical_str(replaced).finish();

  ValueClass<mf::SessionChanged> changed(m, "SessionChanged", "Accepted session transition.");
  changed.field("seq_no", &mf::SessionChanged::seq_no).field("state", &mf::SessionChanged::state);
  canonical_str(changed).finish();

  ValueClass<mf::AdminAck> admin_ack(m, "AdminAck", "Accepted limits or kill-switch change.");
  admin_ack.field("seq_no", &mf::AdminAck::seq_no).field("kind", &mf::AdminAck::kind);
  canonical_str(admin_ack).finish();

  ValueClass<mf::AdminReject> admin_reject(m, "AdminReject", "Refused admin command.");
  admin_reject.field("seq_no", &mf::AdminReject::seq_no)
      .field("reason", &mf::AdminReject::reason);
  canonical_str(admin_reject).finish();
}

void bind_results(py::module_& m) {
  using View = mf::OrderBook::OrderView;
  ValueClass<View>(m, "OrderView", "A resting order.")
      .field("account_id", &View::account_id)
      .field("side", &View::side)
      .field("price", &View::price)
      .field("qty", &View::qty)
      .field("leaves_qty", &View::leaves_qty)
      .field("filled_qty", &View::filled_qty)
      .finish(/*result_only=*/true);

  using Exposure = mf::RiskGate::Exposure;
  ValueClass<Exposure>(m, "Exposure", "An account's open-order totals (exact integers).")
      .computed("open_notional", [](const Exposure& x) { return to_python(x.open_notional); })
      .computed("open_buy_qty", [](const Exposure& x) { return to_python(x.open_buy_qty); })
      .computed("open_sell_qty", [](const Exposure& x) { return to_python(x.open_sell_qty); })
      .computed("open_orders", [](const Exposure& x) { return py::int_(x.open_orders); })
      .finish(/*result_only=*/true);

  ValueClass<mf::Snapshot>(m, "Snapshot",
                           "A point-in-time copy of an engine plus where to resume its journal.")
      .computed("last_seq_no",
                [](const mf::Snapshot& s) { return py::cast(s.book.last_seq_no); })
      .computed("last_timestamp", [](const mf::Snapshot& s) { return py::int_(s.last_timestamp); })
      .computed("journal_offset", [](const mf::Snapshot& s) { return py::int_(s.journal_offset); })
      .finish(/*result_only=*/true);

  ValueClass<mf::JournalRecord>(m, "JournalRecord", "One journaled command with its timestamp.")
      .field("timestamp", &mf::JournalRecord::timestamp)
      .field("command", &mf::JournalRecord::command)
      .computed("seq_no", [](const mf::JournalRecord& r) { return py::int_(r.seq_no()); })
      .finish();
}

}  // namespace

void bind_values(py::module_& m) {
  bind_enums(m);
  bind_commands(m);
  bind_events(m);
  bind_results(m);
}

}  // namespace mfpy
