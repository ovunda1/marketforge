#pragma once

#include <istream>
#include <optional>
#include <ostream>
#include <stdexcept>
#include <string>
#include <string_view>

#include "mf/commands.hpp"
#include "mf/order_book.hpp"

namespace mf {

// Canonical text formats for the differential test and golden files
// (docs/event-format.md). Events use to_string(const Event&) from events.hpp.

class ParseError : public std::runtime_error {
 public:
  using std::runtime_error::runtime_error;
};

// Parses one command line. Returns nullopt for a blank or comment-only line; text after
// '#' is ignored. Throws ParseError on malformed input.
std::optional<Command> parse_command(std::string_view line);

// "BOOK seq=<n> bid=<px>x<qty>|- ask=<px>x<qty>|-": top of book after a command.
std::string format_top_of_book(SeqNo seq_no, const OrderBook& book);

// One "LVL side=<B|S> px=<px> qty=<qty> orders=<id>:<leaves>,..." line per price level,
// bids best first then asks best first, orders in time priority. Each line ends in '\n'.
std::string format_depth(const OrderBook& book);

// Runs every command in `in` through a fresh OrderBook and writes the canonical output
// stream to `out`: each command's events, then its BOOK line, then the final depth.
// Throws ParseError (message prefixed with "line N: ") on malformed input; an
// InvariantViolation from the book propagates unchanged.
void run_commands(std::istream& in, std::ostream& out);

}  // namespace mf
