#include "mf/text_format.hpp"

#include <charconv>
#include <string>
#include <vector>

namespace mf {

namespace {

std::vector<std::string_view> split(std::string_view line) {
  std::vector<std::string_view> tokens;
  std::size_t i = 0;
  while (i < line.size()) {
    while (i < line.size() && (line[i] == ' ' || line[i] == '\t' || line[i] == '\r')) ++i;
    const std::size_t start = i;
    while (i < line.size() && line[i] != ' ' && line[i] != '\t' && line[i] != '\r') ++i;
    if (i > start) tokens.push_back(line.substr(start, i - start));
  }
  return tokens;
}

// Parses the whole token as an integer of type T; rejects signs on unsigned types,
// trailing characters and overflow.
template <class T>
T number(std::string_view token, const char* field) {
  T value{};
  const auto [end, ec] = std::from_chars(token.data(), token.data() + token.size(), value);
  if (ec != std::errc{} || end != token.data() + token.size()) {
    throw ParseError("bad " + std::string(field) + " '" + std::string(token) + "'");
  }
  return value;
}

[[noreturn]] void bad_field(const char* field, std::string_view token) {
  throw ParseError("bad " + std::string(field) + " '" + std::string(token) + "'");
}

Side parse_side(std::string_view t) {
  if (t == "B") return Side::Buy;
  if (t == "S") return Side::Sell;
  bad_field("side", t);
}

OrderType parse_type(std::string_view t) {
  if (t == "LMT") return OrderType::Limit;
  if (t == "MKT") return OrderType::Market;
  bad_field("type", t);
}

TimeInForce parse_tif(std::string_view t) {
  if (t == "GTC") return TimeInForce::GTC;
  if (t == "IOC") return TimeInForce::IOC;
  if (t == "FOK") return TimeInForce::FOK;
  bad_field("tif", t);
}

bool parse_flag(std::string_view t) {
  if (t == "0") return false;
  if (t == "1") return true;
  bad_field("post_only", t);
}

StpMode parse_stp(std::string_view t) {
  if (t == "CN") return StpMode::CancelNewest;
  if (t == "CO") return StpMode::CancelOldest;
  if (t == "CB") return StpMode::CancelBoth;
  bad_field("stp", t);
}

void expect_fields(const std::vector<std::string_view>& tokens, std::size_t n) {
  if (tokens.size() != n) {
    throw ParseError("'" + std::string(tokens[0]) + "' expects " + std::to_string(n - 1) +
                     " fields, got " + std::to_string(tokens.size() - 1));
  }
}

void append_side_top(std::string& out, const OrderBook& book, Side side) {
  const auto best = side == Side::Buy ? book.best_bid() : book.best_ask();
  if (!best) {
    out += '-';
    return;
  }
  out += std::to_string(*best);
  out += 'x';
  out += std::to_string(book.level_qty(side, *best));
}

}  // namespace

std::optional<Command> parse_command(std::string_view line) {
  if (const auto hash = line.find('#'); hash != std::string_view::npos) {
    line = line.substr(0, hash);
  }
  const auto t = split(line);
  if (t.empty()) return std::nullopt;

  if (t[0] == "N") {
    expect_fields(t, 11);
    return NewOrder{.seq_no = number<SeqNo>(t[1], "seq"),
                    .order_id = number<OrderId>(t[2], "id"),
                    .account_id = number<AccountId>(t[3], "acct"),
                    .side = parse_side(t[4]),
                    .type = parse_type(t[5]),
                    .tif = parse_tif(t[6]),
                    .post_only = parse_flag(t[7]),
                    .stp = parse_stp(t[8]),
                    .price = number<Price>(t[9], "px"),
                    .qty = number<Qty>(t[10], "qty")};
  }
  if (t[0] == "C") {
    expect_fields(t, 3);
    return Cancel{.seq_no = number<SeqNo>(t[1], "seq"), .order_id = number<OrderId>(t[2], "id")};
  }
  if (t[0] == "R") {
    expect_fields(t, 5);
    return Replace{.seq_no = number<SeqNo>(t[1], "seq"),
                   .order_id = number<OrderId>(t[2], "id"),
                   .price = number<Price>(t[3], "px"),
                   .qty = number<Qty>(t[4], "qty")};
  }
  throw ParseError("unknown command '" + std::string(t[0]) + "'");
}

std::string format_top_of_book(SeqNo seq_no, const OrderBook& book) {
  std::string out = "BOOK seq=" + std::to_string(seq_no) + " bid=";
  append_side_top(out, book, Side::Buy);
  out += " ask=";
  append_side_top(out, book, Side::Sell);
  return out;
}

std::string format_depth(const OrderBook& book) {
  std::string out;
  for (const Side side : {Side::Buy, Side::Sell}) {
    for (const Price price : book.level_prices(side)) {
      out += side == Side::Buy ? "LVL side=B px=" : "LVL side=S px=";
      out += std::to_string(price);
      out += " qty=";
      out += std::to_string(book.level_qty(side, price));
      out += " orders=";
      bool first = true;
      for (const OrderId id : book.level_orders(side, price)) {
        if (!first) out += ',';
        first = false;
        out += std::to_string(id);
        out += ':';
        out += std::to_string(book.leaves_qty(id).value_or(0));
      }
      out += '\n';
    }
  }
  return out;
}

void run_commands(std::istream& in, std::ostream& out) {
  OrderBook book;
  std::vector<Event> events;
  std::string line;
  std::string text;
  for (std::size_t line_no = 1; std::getline(in, line); ++line_no) {
    std::optional<Command> command;
    try {
      command = parse_command(line);
    } catch (const ParseError& e) {
      throw ParseError("line " + std::to_string(line_no) + ": " + e.what());
    }
    if (!command) continue;
    events.clear();
    book.process(*command, events);
    text.clear();
    for (const auto& e : events) (text += to_string(e)) += '\n';
    const SeqNo seq_no = std::visit([](const auto& c) { return c.seq_no; }, *command);
    (text += format_top_of_book(seq_no, book)) += '\n';
    out << text;
  }
  out << format_depth(book);
}

}  // namespace mf
