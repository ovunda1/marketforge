#include "fixtures.hpp"

#include "workload.hpp"

namespace mf::bench {

namespace {

constexpr AccountId kTaker = 999;  // never a maker: no self-trade prevention in matches
constexpr Qty kMakerQty = 10;

// The limits the workloads use: every check runs and passes.
InstrumentLimits instrument_limits() {
  return {.max_qty = 1'000'000, .band_bps = 5'000, .reference_price = MicroScenario::kMid};
}
AccountLimits account_limits() {
  return {.max_notional = 1'000'000'000'000'000, .position_limit = 1'000'000'000'000,
          .credit = 1'000'000'000'000'000'000, .max_open_orders = 1'000'000,
          .rate = RateLimit{.per_sec = 1'000'000, .burst = 1'000'000}};
}

}  // namespace

std::string_view scenario_name(Scenario scenario) {
  switch (scenario) {
    case Scenario::AddExistingLevel: return "AddExistingLevel";
    case Scenario::AddNewLevel: return "AddNewLevel";
    case Scenario::CancelKeepsLevel: return "CancelKeepsLevel";
    case Scenario::CancelRemovesLevel: return "CancelRemovesLevel";
    case Scenario::ReplaceQtyDown: return "ReplaceQtyDown";
    case Scenario::ReplaceReprice: return "ReplaceReprice";
    case Scenario::MatchOneLevel: return "MatchOneLevel";
    case Scenario::MatchMultiLevel: return "MatchMultiLevel";
    case Scenario::RiskCheck: return "RiskCheck";
    case Scenario::RiskCheckNoLimits: return "RiskCheckNoLimits";
    case Scenario::EngineAdd: return "EngineAdd";
  }
  return "?";
}

MicroScenario::MicroScenario(Config config) : config_(config) {
  if (config_.scenario == Scenario::EngineAdd) {
    engine_ = std::make_unique<Engine>();
    scratch_.clear();
    engine_->process(SetInstrumentLimits{.seq_no = ++seq_, .limits = instrument_limits()}, now_,
                     scratch_);
    engine_->process(SetAccountLimits{.seq_no = ++seq_, .limits = account_limits()}, now_,
                     scratch_);
  } else {
    book_ = std::make_unique<OrderBook>();
  }
  if (config_.scenario == Scenario::RiskCheck) {
    gate_.set_instrument_limits(instrument_limits());
    gate_.set_account_limits(std::nullopt, account_limits());
  }

  // Base book. Match scenarios leave room above kMid for their makers.
  for (int level = 0; level < kBaseLevels; ++level) {
    for (int i = 0; i < kBaseOrdersPerLevel; ++i) {
      const AccountId account = 1 + static_cast<AccountId>(i);
      add(Side::Buy, kMid - 1 - level, kBaseQty, account);
      add(Side::Sell, base_ask() + level, kBaseQty, account);
    }
  }
  events_.clear();

  // A fixed shuffle of the cancel order, so cancels do not walk the book in order.
  order_.resize(static_cast<std::size_t>(config_.batch));
  for (std::size_t i = 0; i < order_.size(); ++i) order_[i] = i;
  Rng rng(7);
  for (std::size_t i = order_.size(); i > 1; --i) std::swap(order_[i - 1], order_[rng.below(i)]);
}

Price MicroScenario::base_ask() const {
  const bool match = config_.scenario == Scenario::MatchOneLevel ||
                     config_.scenario == Scenario::MatchMultiLevel;
  return kMid + 1 + (match ? static_cast<Price>(config_.batch) * config_.param : 0);
}

const OrderBook& MicroScenario::book() const { return engine_ ? engine_->book() : *book_; }

void MicroScenario::book_command(const OrderCommand& command) {
  if (engine_) {
    std::visit([&](const auto& c) { engine_->process(c, ++now_, events_); }, command);
  } else {
    book_->process(command, events_);
  }
}

void MicroScenario::add(Side side, Price price, Qty qty, AccountId account, TimeInForce tif) {
  book_command(NewOrder{.seq_no = ++seq_, .order_id = next_id_++, .account_id = account,
                        .side = side, .tif = tif, .price = price, .qty = qty});
}

void MicroScenario::cancel(OrderId id) { book_command(Cancel{.seq_no = ++seq_, .order_id = id}); }

void MicroScenario::prepare() {
  const int k = config_.batch;
  pending_.clear();
  const auto place = [&](Side side, Price price, Qty qty, AccountId account) {
    pending_.push_back(next_id_);
    add(side, price, qty, account);
  };
  switch (config_.scenario) {
    case Scenario::CancelKeepsLevel:
    case Scenario::ReplaceQtyDown:
    case Scenario::ReplaceReprice:
      for (int i = 0; i < k; ++i) place(Side::Buy, kMid - 1 - i % kBaseLevels, kBaseQty, 100);
      break;
    case Scenario::CancelRemovesLevel:
      for (int i = 0; i < k; ++i) place(Side::Buy, kMid - 1 - kBaseLevels - i, kBaseQty, 100);
      break;
    case Scenario::MatchOneLevel:
      for (int i = 0; i < k * config_.param; ++i) {
        place(Side::Sell, kMid + 1, kMakerQty, 1 + static_cast<AccountId>(i % 4));
      }
      break;
    case Scenario::MatchMultiLevel:
      for (int i = 0; i < k * config_.param; ++i) {
        place(Side::Sell, kMid + 1 + i, kMakerQty, 1 + static_cast<AccountId>(i % 4));
      }
      break;
    default:
      break;
  }
  events_.clear();
}

void MicroScenario::run_batch() {
  const int k = config_.batch;
  const int p = config_.param;
  events_.clear();
  rejects_ = 0;
  switch (config_.scenario) {
    case Scenario::AddExistingLevel:
    case Scenario::EngineAdd:
      pending_.clear();
      for (int i = 0; i < k; ++i) {
        pending_.push_back(next_id_);
        add(Side::Buy, kMid - 1 - i % kBaseLevels, kBaseQty, 100);
      }
      break;
    case Scenario::AddNewLevel:
      pending_.clear();
      for (int i = 0; i < k; ++i) {
        pending_.push_back(next_id_);
        add(Side::Buy, kMid - 1 - kBaseLevels - i, kBaseQty, 100);
      }
      break;
    case Scenario::CancelKeepsLevel:
    case Scenario::CancelRemovesLevel:
      for (const std::size_t i : order_) cancel(pending_[i]);
      pending_.clear();
      break;
    case Scenario::ReplaceQtyDown:
      for (int i = 0; i < k; ++i) {
        book_command(Replace{.seq_no = ++seq_, .order_id = pending_[static_cast<std::size_t>(i)],
                             .price = kMid - 1 - i % kBaseLevels, .qty = kBaseQty / 2});
      }
      break;
    case Scenario::ReplaceReprice:
      for (int i = 0; i < k; ++i) {
        book_command(Replace{.seq_no = ++seq_, .order_id = pending_[static_cast<std::size_t>(i)],
                             .price = kMid - 1 - (i + 1) % kBaseLevels, .qty = kBaseQty});
      }
      break;
    case Scenario::MatchOneLevel:
      for (int i = 0; i < k; ++i) add(Side::Buy, kMid + 1, kMakerQty * p, kTaker, TimeInForce::IOC);
      pending_.clear();
      break;
    case Scenario::MatchMultiLevel:
      for (int i = 0; i < k; ++i) {
        add(Side::Buy, kMid + static_cast<Price>(i + 1) * p, kMakerQty * p, kTaker,
            TimeInForce::IOC);
      }
      pending_.clear();
      break;
    case Scenario::RiskCheck:
    case Scenario::RiskCheckNoLimits:
      for (int i = 0; i < k; ++i) {
        const NewOrder order{.seq_no = ++seq_, .order_id = next_id_++, .account_id = 100,
                             .side = Side::Buy, .price = kMid - 1 - i % kBaseLevels,
                             .qty = kBaseQty};
        rejects_ += gate_.check_new(order, now_ += 1'000, *book_).reject.has_value();
      }
      break;
  }
}

void MicroScenario::restore() {
  for (const OrderId id : pending_) cancel(id);
  pending_.clear();
  events_.clear();
}

MicroScenario::Shape MicroScenario::shape() const {
  Shape s;
  const OrderBook& b = book();
  for (const Side side : {Side::Buy, Side::Sell}) {
    for (const Price price : b.level_prices(side)) {
      s.emplace_back(side, price, b.level_qty(side, price), b.level_order_count(side, price));
    }
  }
  return s;
}

}  // namespace mf::bench
