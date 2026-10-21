#pragma once

#include <istream>
#include <optional>
#include <ostream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include "mf/commands.hpp"
#include "mf/engine.hpp"
#include "mf/order_book.hpp"

namespace mf {

// Canonical text formats for the differential test and golden files
// (docs/event-format.md). Events use to_string(const Event&) from events.hpp.

class ParseError : public std::runtime_error {
 public:
  using std::runtime_error::runtime_error;
};

// Parses one command line. Returns nullopt for a blank or comment-only line; text after
// '#' is ignored. Throws ParseError on malformed input, including a clock line.
std::optional<Command> parse_command(std::string_view line);

// "T <ns>": sets the simulated time for the commands that follow (M5). Not a command:
// it has no seq_no. Time starts at 0 and must never go backwards.
struct ClockSet {
  Timestamp timestamp{};

  bool operator==(const ClockSet&) const = default;
};
using TextLine = std::variant<Command, ClockSet>;

// Like parse_command, but also accepts clock lines.
std::optional<TextLine> parse_line(std::string_view line);

// "BOOK seq=<n> bid=<px>x<qty>|- ask=<px>x<qty>|-": top of book after a command.
std::string format_top_of_book(SeqNo seq_no, const OrderBook& book);

// One "LVL side=<B|S> px=<px> qty=<qty> orders=<id>:<leaves>,..." line per price level,
// bids best first then asks best first, orders in time priority. Each line ends in '\n'.
std::string format_depth(const OrderBook& book);

// Appends one command's canonical output to `out`: each event on its own line, then
// the BOOK line. Shared by mf_driver and replay so the two streams cannot drift apart.
void append_command_output(const std::vector<Event>& events, SeqNo seq_no,
                           const OrderBook& book, std::string& out);

// Runs `prefix` (e.g. commands from a risk config, numbered 1..k at time 0) and then
// every line of `in` through a fresh Engine, and writes the canonical output stream to
// `out`: each command's events, then its BOOK line, then the final depth. Clock lines
// set the timestamp of the commands after them. Throws ParseError (message prefixed
// with "line N: ") on malformed input or time going backwards; an InvariantViolation
// from the engine propagates unchanged.
void run_commands(std::istream& in, std::ostream& out, const std::vector<Command>& prefix = {});

}  // namespace mf
