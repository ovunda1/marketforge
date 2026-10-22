#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>
#include <string_view>
#include <tuple>
#include <vector>

#include "mf/engine.hpp"
#include "mf/order_book.hpp"
#include "mf/risk.hpp"

namespace mf::bench {

// Microbenchmark scenarios. Each timed batch runs `batch` operations of one kind against
// a book of a fixed shape; prepare() and restore() run untimed around it, so the book
// is back to the same shape before every batch and the benchmark cannot drift into
// measuring something else (a growing book, or cheap rejects).
//
// The base book: kBaseLevels bid levels just below kMid and kBaseLevels ask levels
// above it, kBaseOrdersPerLevel orders each. The match scenarios put their makers
// between the two, so a taker never reaches the base asks.
enum class Scenario : std::uint8_t {
  AddExistingLevel,    // passive add to an existing level
  AddNewLevel,         // passive add that creates a level
  CancelKeepsLevel,    // cancel; the level survives
  CancelRemovesLevel,  // cancel of a level's only order
  ReplaceQtyDown,      // same price, lower qty: keeps priority
  ReplaceReprice,      // new price, no match: loses priority, moves level
  MatchOneLevel,       // IOC taker fills `param` makers at one level
  MatchMultiLevel,     // IOC taker sweeps `param` levels, one maker each
  RiskCheck,           // RiskGate::check_new with every limit set; all pass
  RiskCheckNoLimits,   // RiskGate::check_new with no limits
  EngineAdd,           // passive add through Engine: gate + book + risk update
};

std::string_view scenario_name(Scenario scenario);

class MicroScenario {
 public:
  static constexpr Price kMid = 1'000'000;
  static constexpr int kBaseLevels = 50;
  static constexpr int kBaseOrdersPerLevel = 4;
  static constexpr Qty kBaseQty = 100;

  struct Config {
    Scenario scenario{};
    int batch{256};
    int param{1};  // makers per taker (MatchOneLevel) or levels per sweep (MatchMultiLevel)
  };

  explicit MicroScenario(Config config);

  void prepare();    // untimed: sets up what the batch consumes
  void run_batch();  // timed: `batch` operations
  void restore();    // untimed: undoes what the batch added

  int ops_per_batch() const { return config_.batch; }
  // Events of the last run_batch() (book and engine scenarios).
  const std::vector<Event>& batch_events() const { return events_; }
  // Rejected checks in the last run_batch() (risk scenarios).
  std::size_t batch_rejects() const { return rejects_; }

  // Price levels as (price, total qty, order count), each side best first.
  using Shape = std::vector<std::tuple<Side, Price, Qty, std::size_t>>;
  Shape shape() const;
  const OrderBook& book() const;

 private:
  void book_command(const OrderCommand& command);
  void add(Side side, Price price, Qty qty, AccountId account, TimeInForce tif = TimeInForce::GTC);
  void cancel(OrderId id);
  Price base_ask() const;

  Config config_;
  std::unique_ptr<OrderBook> book_;  // book scenarios and risk scenarios
  std::unique_ptr<Engine> engine_;   // EngineAdd
  RiskGate gate_;
  SeqNo seq_{};
  Timestamp now_{};
  OrderId next_id_{1};
  std::vector<OrderId> pending_;  // ids prepare() placed or run_batch() added
  std::vector<std::size_t> order_;  // cancel order, a fixed shuffle
  std::vector<Event> events_;
  std::vector<Event> scratch_;
  std::size_t rejects_{};
};

}  // namespace mf::bench
