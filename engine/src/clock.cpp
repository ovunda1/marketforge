#include "mf/clock.hpp"

#include <stdexcept>
#include <string>

namespace mf {

SimulatedClock::SimulatedClock(Timestamp start, Timestamp step) : now_(start), step_(step) {
  if (step < 0) throw std::invalid_argument("SimulatedClock step must not be negative");
}

Timestamp SimulatedClock::now() {
  const Timestamp t = now_;
  now_ += step_;
  return t;
}

void SimulatedClock::set(Timestamp t) {
  if (t < now_) {
    throw std::invalid_argument("SimulatedClock cannot go back from " + std::to_string(now_) +
                                " to " + std::to_string(t));
  }
  now_ = t;
}

void SimulatedClock::advance(Timestamp dt) {
  if (dt < 0) throw std::invalid_argument("SimulatedClock cannot advance by a negative amount");
  now_ += dt;
}

}  // namespace mf
