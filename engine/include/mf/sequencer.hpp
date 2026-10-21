#pragma once

#include "mf/clock.hpp"
#include "mf/commands.hpp"
#include "mf/journal.hpp"
#include "mf/types.hpp"

namespace mf {

// Live-path ordering stage for one symbol: assigns the next seq_no (1, 2, 3, ... with no
// gaps), stamps the clock's time and appends the record to the journal before the
// caller hands the command to the engine (write-ahead). It never rejects: validation,
// including risk checks and session-wide order-id uniqueness, happens in the Engine
// (risk gate, then book), which also runs on replay, so every reject is reproduced.
// Admin commands (limits, kill switches, sessions) are sequenced the same way. Replay
// does not use the sequencer; seq_nos and timestamps come from the journal.
class Sequencer {
 public:
  Sequencer(Clock& clock, JournalWriter& journal) : clock_(clock), journal_(journal) {}

  // Overwrites the command's seq_no, journals it and returns the stamped record.
  JournalRecord sequence(Command command);

  SeqNo last_seq_no() const { return journal_.last_seq_no(); }

 private:
  Clock& clock_;
  JournalWriter& journal_;
};

}  // namespace mf
