#include "mf/sequencer.hpp"

namespace mf {

JournalRecord Sequencer::sequence(Command command) {
  const SeqNo seq_no = journal_.last_seq_no() + 1;
  std::visit([&](auto& c) { c.seq_no = seq_no; }, command);
  JournalRecord record{.timestamp = clock_.now(), .command = std::move(command)};
  journal_.append(record);  // write-ahead: durable (to the OS) before the book sees it
  return record;
}

}  // namespace mf
