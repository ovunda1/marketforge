#include "mf/engine.hpp"

#include <span>
#include <type_traits>

namespace mf {

Engine::Engine() : book_(std::make_unique<OrderBook>()) {}

// Order: seq_no check, risk gate (which runs the book's static checks in its own
// order), book, then risk state from the command's events. A risk reject never reaches
// the book; admin commands only touch the book through session or kill-switch actions.
void Engine::process(const Command& command, Timestamp timestamp, std::vector<Event>& out) {
  const SeqNo seq = seq_no_of(command);
  book_->check_seq_no(seq);
  const std::size_t first = out.size();
  AccountId new_order_account = 0;

  std::visit(
      [&](const auto& c) {
        using T = std::decay_t<decltype(c)>;
        if constexpr (std::is_same_v<T, NewOrder>) {
          new_order_account = c.account_id;
          const auto decision = risk_.check_new(c, timestamp, *book_);
          if (decision.reject) {
            book_->skip_seq_no(seq);
            out.push_back(Reject{.seq_no = seq, .order_id = c.order_id, .reason = *decision.reject});
            return;
          }
          book_->process(c, out, decision.market_limit);
        } else if constexpr (std::is_same_v<T, Replace>) {
          if (const auto reason = risk_.check_replace(c, timestamp, *book_)) {
            book_->skip_seq_no(seq);
            out.push_back(Reject{.seq_no = seq, .order_id = c.order_id, .reason = *reason});
            return;
          }
          book_->process(c, out);
        } else if constexpr (std::is_same_v<T, Cancel>) {
          book_->process(c, out);  // accepted in every session state, never limited
        } else {
          book_->skip_seq_no(seq);
          if constexpr (std::is_same_v<T, SetSession>) {
            on_session(c, out);
          } else if constexpr (std::is_same_v<T, KillSwitch>) {
            on_kill_switch(c, out);
          } else if constexpr (std::is_same_v<T, SetInstrumentLimits>) {
            if (!limits_valid(c.limits)) {
              out.push_back(AdminReject{.seq_no = seq, .reason = AdminRejectReason::InvalidLimits});
              return;
            }
            risk_.set_instrument_limits(c.limits);
            out.push_back(AdminAck{.seq_no = seq, .kind = AdminKind::InstrumentLimits});
          } else {
            static_assert(std::is_same_v<T, SetAccountLimits>);
            if (!limits_valid(c.limits)) {
              out.push_back(AdminReject{.seq_no = seq, .reason = AdminRejectReason::InvalidLimits});
              return;
            }
            risk_.set_account_limits(c.account_id, c.limits);
            out.push_back(AdminAck{.seq_no = seq, .kind = AdminKind::AccountLimits});
          }
        }
      },
      command);

  risk_.apply(std::span<const Event>(out).subspan(first), new_order_account);
  if constexpr (kInvariantChecksEnabled) risk_.check_consistency(*book_);
}

// Session open (CLOSED -> PRE_OPEN) forgets the previous session's ids; the close
// expires DAY orders, in book order, after the SessionChanged event.
void Engine::on_session(const SetSession& c, std::vector<Event>& out) {
  if (!transition_allowed(risk_.session(), c.state)) {
    out.push_back(AdminReject{.seq_no = c.seq_no, .reason = AdminRejectReason::InvalidTransition});
    return;
  }
  const SessionState from = risk_.session();
  risk_.set_session(c.state);
  out.push_back(SessionChanged{.seq_no = c.seq_no, .state = c.state});
  if (from == SessionState::Closed && c.state == SessionState::PreOpen) book_->reset_used_ids();
  if (c.state == SessionState::Closed) {
    book_->cancel_where(
        c.seq_no, [](const OrderBook::OrderView&, TimeInForce tif) { return tif == TimeInForce::Day; },
        CancelReason::Expired, out);
  }
}

void Engine::on_kill_switch(const KillSwitch& c, std::vector<Event>& out) {
  if (c.cancel_open && !c.engaged) {
    out.push_back(AdminReject{.seq_no = c.seq_no, .reason = AdminRejectReason::InvalidKillSwitch});
    return;
  }
  risk_.set_kill_switch(c.account_id, c.engaged);
  out.push_back(AdminAck{.seq_no = c.seq_no, .kind = AdminKind::KillSwitch});
  if (c.cancel_open) {
    const auto account = c.account_id;
    book_->cancel_where(
        c.seq_no,
        [account](const OrderBook::OrderView& o, TimeInForce) {
          return !account || o.account_id == *account;
        },
        CancelReason::KillSwitch, out);
  }
}

EngineState Engine::state() const { return {.book = book_->state(), .risk = risk_.state()}; }

std::unique_ptr<Engine> Engine::from_state(const EngineState& state) {
  auto engine = std::make_unique<Engine>();
  engine->book_ = OrderBook::from_state(state.book);
  engine->risk_ = RiskGate::from_state(state.risk, state.book);
  return engine;
}

void Engine::check_invariants() const {
  book_->check_invariants();
  risk_.check_consistency(*book_);
}

}  // namespace mf
