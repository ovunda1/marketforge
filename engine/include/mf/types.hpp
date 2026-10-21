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

}  // namespace mf
