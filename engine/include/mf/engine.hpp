#pragma once

#include <memory>
#include <optional>
#include <vector>

#include "mf/commands.hpp"
#include "mf/events.hpp"
#include "mf/order_book.hpp"
#include "mf/risk.hpp"
#include "mf/types.hpp"

namespace mf {

// Complete state of one symbol's engine: what a snapshot stores.
struct EngineState {
  BookState book;
  RiskState risk;

  bool operator==(const EngineState&) const = default;
};

// One symbol's engine: the risk gate in front of the order book, plus session and admin
// handling (docs/risk.md). Every sequenced command, live or replayed, goes through
// process(), so its output depends only on the journal: the commands and their
// timestamps. A fresh engine is CONTINUOUS with no limits, which is the M4 behavior.
class Engine {
 public:
  Engine();
  Engine(const Engine&) = delete;
  Engine& operator=(const Engine&) = delete;

  // Applies one command with its journal timestamp and appends its events. Throws
  // SequenceError, before changing anything, on a non-increasing seq_no or a timestamp
  // earlier than the previous command's. Any other exception (an InvariantViolation
  // from the invariant walk) may come after the command began changing state.
  void process(const Command& command, Timestamp timestamp, std::vector<Event>& events_out);

  const OrderBook& book() const { return *book_; }
  const RiskGate& risk() const { return risk_; }
  // Timestamp of the last command processed (or the restored lower bound).
  std::optional<Timestamp> last_timestamp() const { return last_timestamp_; }

  EngineState state() const;
  // Throws InvariantViolation if the state is inconsistent. Later commands may not be
  // earlier than `last_timestamp` (a snapshot's) or than any rate bucket's last refill.
  static std::unique_ptr<Engine> from_state(const EngineState& state,
                                            std::optional<Timestamp> last_timestamp = std::nullopt);

  // Book invariants plus risk exposure == exposure rebuilt from the book. O(N).
  void check_invariants() const;

 private:
  void on_session(const SetSession& command, std::vector<Event>& out);
  void on_kill_switch(const KillSwitch& command, std::vector<Event>& out);

  std::unique_ptr<OrderBook> book_;
  RiskGate risk_;
  std::optional<Timestamp> last_timestamp_;
};

}  // namespace mf
