#pragma once

#include <ostream>
#include <vector>

#include "mf/engine.hpp"
#include "mf/order_book.hpp"

namespace mf {

// Lets GoogleTest print events readably when an expectation fails. GoogleTest prints a
// std::variant by its alternative, so each alternative needs its own overload.
inline void PrintTo(const Event& event, std::ostream* os) { *os << to_string(event); }
inline void PrintTo(const Ack& e, std::ostream* os) { PrintTo(Event{e}, os); }
inline void PrintTo(const Reject& e, std::ostream* os) { PrintTo(Event{e}, os); }
inline void PrintTo(const Fill& e, std::ostream* os) { PrintTo(Event{e}, os); }
inline void PrintTo(const Cancelled& e, std::ostream* os) { PrintTo(Event{e}, os); }
inline void PrintTo(const Trade& e, std::ostream* os) { PrintTo(Event{e}, os); }
inline void PrintTo(const Replaced& e, std::ostream* os) { PrintTo(Event{e}, os); }
inline void PrintTo(const SessionChanged& e, std::ostream* os) { PrintTo(Event{e}, os); }
inline void PrintTo(const AdminAck& e, std::ostream* os) { PrintTo(Event{e}, os); }
inline void PrintTo(const AdminReject& e, std::ostream* os) { PrintTo(Event{e}, os); }

namespace test {

// Processes one command and returns only the events it produced.
inline std::vector<Event> run(OrderBook& book, const OrderCommand& command) {
  std::vector<Event> out;
  book.process(command, out);
  return out;
}

inline std::vector<Event> run(Engine& engine, const Command& command, Timestamp ts = 0) {
  std::vector<Event> out;
  engine.process(command, ts, out);
  return out;
}

template <class T>
std::vector<T> events_of(const std::vector<Event>& events) {
  std::vector<T> result;
  for (const auto& e : events) {
    if (const auto* p = std::get_if<T>(&e)) result.push_back(*p);
  }
  return result;
}

}  // namespace test
}  // namespace mf
