#pragma once

#include <cstdint>

namespace mf {

// Prices are integer ticks and quantities are integer units. Never use floating point
// for either: equality and ordering must be exact and identical on every platform.
using Price = std::int64_t;
using Qty = std::int64_t;

using OrderId = std::uint64_t;
using AccountId = std::uint64_t;
using SeqNo = std::uint64_t;
using TradeId = std::uint64_t;

enum class Side : std::uint8_t { Buy, Sell };

// A market order has no price and sweeps the opposite side; it must be IOC.
enum class OrderType : std::uint8_t { Limit, Market };

// GTC rests until cancelled; IOC cancels what it cannot fill immediately; FOK fills
// completely or is rejected. DAY arrives with session states in M4.
enum class TimeInForce : std::uint8_t { GTC, IOC, FOK };

// Self-trade prevention: what happens when an incoming order reaches a resting order of
// the same account. The incoming order's mode decides. The gateway fills it in from the
// account's configuration, so the command journal fully determines behavior.
enum class StpMode : std::uint8_t {
  CancelNewest,  // cancel the incoming order's remainder; the resting order is untouched
  CancelOldest,  // cancel the resting order and keep matching
  CancelBoth,    // cancel both
};

}  // namespace mf
