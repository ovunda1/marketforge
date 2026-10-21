#pragma once

#include <cstdint>
#include <map>
#include <optional>
#include <span>
#include <vector>

#include "mf/commands.hpp"
#include "mf/events.hpp"
#include "mf/flat_hash_map.hpp"
#include "mf/order_book.hpp"
#include "mf/types.hpp"

namespace mf {

// Pre-trade risk gate for one symbol (docs/risk.md). It runs between the sequencer and
// the book, live and on replay, so every risk reject is reproduced from the journal.
// Its inputs are the command, the record's timestamp and the book; it reads no clock.
//
// Risk state changes only through the engine's own events (apply()) and admin
// commands, never as a side effect of a check, except for the rate limiter's tokens:
// every New and Replace that reaches the rate check costs one, even if a later check
// rejects it.

// Rate-limiter tokens are fixed point: one order costs kTokenScale.
inline constexpr std::int64_t kTokenScale = 1'000'000'000;
// Limits on admin values keep every product inside 128 bits and burst * kTokenScale
// inside int64.
inline constexpr std::int64_t kMaxRate = 1'000'000'000;
inline constexpr std::int64_t kMaxBandBps = 10'000;

// Everything the gate's future decisions depend on that the book does not hold, as
// plain data: what a snapshot stores (docs/snapshot-format.md). Open-order exposure is
// not here: it is rebuilt from the book's resting orders, so the two cannot disagree.
// Every list is ascending by account id.
struct RiskState {
  struct AccountEntry {
    AccountId account_id{};
    AccountLimits limits;

    bool operator==(const AccountEntry&) const = default;
  };
  struct Position {
    AccountId account_id{};
    Qty qty{};  // non-zero; + long, - short

    bool operator==(const Position&) const = default;
  };
  struct Bucket {
    AccountId account_id{};
    std::int64_t tokens{};   // units of 1 / kTokenScale order
    Timestamp last_refill{};

    bool operator==(const Bucket&) const = default;
  };

  SessionState session{SessionState::Continuous};
  InstrumentLimits instrument;
  std::optional<AccountLimits> default_limits;
  std::vector<AccountEntry> account_limits;
  bool global_kill{};
  std::vector<AccountId> killed_accounts;
  std::vector<Position> positions;
  std::vector<Bucket> buckets;
  std::optional<Price> last_trade_price;

  bool operator==(const RiskState&) const = default;
};

// Range checks for admin values (AREJ INVALID_LIMITS); shared with the YAML loader.
bool limits_valid(const InstrumentLimits& limits);
bool limits_valid(const AccountLimits& limits);

// Allowed session transitions: CLOSED -> PRE_OPEN (session open), PRE_OPEN ->
// CONTINUOUS, CONTINUOUS <-> HALTED, and PRE_OPEN / CONTINUOUS / HALTED -> CLOSED.
bool transition_allowed(SessionState from, SessionState to);

class RiskGate {
 public:
  // Open-order totals of one account, kept from events.
  struct Exposure {
    Notional open_notional{};  // sum of leaves * price
    Notional open_buy_qty{};
    Notional open_sell_qty{};
    std::int64_t open_orders{};

    bool operator==(const Exposure&) const = default;
  };

  struct NewOrderDecision {
    std::optional<RejectReason> reject;
    std::optional<Price> market_limit;  // band edge for a market order
  };

  // Checks a new order in the documented order, including the book's own static
  // checks and duplicate id, so the first failure is reported. Consumes a rate token.
  NewOrderDecision check_new(const NewOrder& order, Timestamp now, const OrderBook& book);
  // Same for a replace. A replace that keeps its price and does not increase its
  // quantity only reduces risk and skips the limit checks.
  std::optional<RejectReason> check_replace(const Replace& replace, Timestamp now,
                                            const OrderBook& book);

  // Updates positions, exposure and the last trade price from one command's events.
  // `new_order_account` is the account of the command's NewOrder (an Ack has none).
  void apply(std::span<const Event> events, AccountId new_order_account);

  // Admin changes; the caller has validated them.
  void set_session(SessionState state) { session_ = state; }
  void set_instrument_limits(const InstrumentLimits& limits) { instrument_ = limits; }
  void set_account_limits(std::optional<AccountId> account_id, const AccountLimits& limits);
  void set_kill_switch(std::optional<AccountId> account_id, bool engaged);

  SessionState session() const { return session_; }
  Qty position(AccountId account_id) const;
  Exposure exposure(AccountId account_id) const;
  // The price-band reference, as a fraction num / den (den 2 for a mid): last trade,
  // else mid, else the instrument's reference price.
  struct Reference {
    Notional num{};
    std::int64_t den{1};
  };
  std::optional<Reference> reference(const OrderBook& book) const;

  RiskState state() const;
  // Restores from a state plus the book it goes with (exposure is rebuilt from the
  // book's resting orders). Throws InvariantViolation if the state is inconsistent.
  static RiskGate from_state(const RiskState& state, const BookState& book);

  // Exposure kept from events equals exposure rebuilt from the book. O(N).
  void check_consistency(const OrderBook& book) const;

 private:
  struct Account {
    Qty position{};
    Exposure exposure;
    std::optional<AccountLimits> limits;  // own limits; else the defaults
    bool killed{};
    std::optional<RiskState::Bucket> bucket;  // set once the account has a rate limit
  };
  struct OpenOrder {
    AccountId account_id{};
    Side side{};
    Price price{};
    Qty leaves{};
  };

  const AccountLimits* limits_for(AccountId account_id) const;
  // Refills and takes one token; false if the bucket is empty.
  bool take_token(AccountId account_id, const RateLimit& rate, Timestamp now);
  // After a limits change: clamp each bucket to its new burst, or drop it when the
  // account no longer has a rate limit.
  void refit_buckets();
  bool in_band(Price price, const Reference& ref) const;
  Price band_edge(Side side, const Reference& ref) const;
  // Removes `qty` of an open order's leaves from its account's exposure.
  void reduce(OrderId order_id, Qty qty);
  void add_exposure(Account& account, Side side, Price price, Qty qty, int sign);

  SessionState session_{SessionState::Continuous};
  InstrumentLimits instrument_;
  std::optional<AccountLimits> default_limits_;
  bool global_kill_{};
  std::optional<Price> last_trade_price_;
  // Ordered, so state() and refit_buckets() walk accounts in id order.
  std::map<AccountId, Account> accounts_;
  // Lookup only, never iterated to produce output. Flat, like the book's order index.
  FlatHashMap<OrderId, OpenOrder> open_;
};

}  // namespace mf
