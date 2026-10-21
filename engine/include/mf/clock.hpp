#pragma once

#include "mf/types.hpp"

namespace mf {

// The only source of time for the engine. Nothing in engine/ reads the host's clock:
// the sequencer stamps each command with now(), and replay takes timestamps from the
// journal instead of asking a clock, so a replay never depends on when it runs.
class Clock {
 public:
  virtual ~Clock() = default;
  virtual Timestamp now() = 0;
};

// Deterministic clock for tests, journal conversion and replay. now() returns the
// current time and then moves it forward by `step` (0: time only moves on set/advance).
// Time never goes backwards; set() to an earlier time or a negative advance() throws
// std::invalid_argument.
class SimulatedClock final : public Clock {
 public:
  explicit SimulatedClock(Timestamp start = 0, Timestamp step = 0);

  Timestamp now() override;
  // The time now() would return, without stepping.
  Timestamp peek() const { return now_; }
  void set(Timestamp t);
  void advance(Timestamp dt);

 private:
  Timestamp now_;
  Timestamp step_;
};

}  // namespace mf
