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
  if (t == "DAY") return TimeInForce::Day;
  bad_field("tif", t);
}

SessionState parse_state(std::string_view t) {
  if (t == "PRE_OPEN") return SessionState::PreOpen;
  if (t == "CONTINUOUS") return SessionState::Continuous;
  if (t == "HALTED") return SessionState::Halted;
  if (t == "CLOSED") return SessionState::Closed;
  bad_field("state", t);
}

// "-" for an unset limit.
template <class T>
std::optional<T> optional_number(std::string_view token, const char* field) {
  if (token == "-") return std::nullopt;
  return number<T>(token, field);
}

// "*" for the defaults (account limits) or the global switch.
std::optional<AccountId> parse_scope(std::string_view t) {
  if (t == "*") return std::nullopt;
  return number<AccountId>(t, "acct");
}

bool parse_choice(std::string_view t, const char* yes, const char* no, const char* field) {
  if (t == yes) return true;
  if (t == no) return false;
  bad_field(field, t);
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
  const auto parsed = parse_line(line);
  if (!parsed) return std::nullopt;
  if (std::holds_alternative<ClockSet>(*parsed)) throw ParseError("a clock line is not a command");
  return std::get<Command>(*parsed);
}

std::optional<TextLine> parse_line(std::string_view line) {
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
  if (t[0] == "S") {
    expect_fields(t, 3);
    return SetSession{.seq_no = number<SeqNo>(t[1], "seq"), .state = parse_state(t[2])};
  }
  if (t[0] == "L") {
    expect_fields(t, 5);
    return SetInstrumentLimits{
        .seq_no = number<SeqNo>(t[1], "seq"),
        .limits = {.max_qty = optional_number<Qty>(t[2], "max_qty"),
                   .band_bps = optional_number<std::int64_t>(t[3], "band_bps"),
                   .reference_price = optional_number<Price>(t[4], "ref_px")}};
  }
  if (t[0] == "A") {
    expect_fields(t, 9);
    const auto per_sec = optional_number<std::int64_t>(t[7], "rate");
    const auto burst = optional_number<std::int64_t>(t[8], "burst");
    if (per_sec.has_value() != burst.has_value()) {
      throw ParseError("rate and burst must both be set or both be '-'");
    }
    return SetAccountLimits{
        .seq_no = number<SeqNo>(t[1], "seq"),
        .account_id = parse_scope(t[2]),
        .limits = {.max_notional = optional_number<std::int64_t>(t[3], "max_notional"),
                   .position_limit = optional_number<Qty>(t[4], "position"),
                   .credit = optional_number<std::int64_t>(t[5], "credit"),
                   .max_open_orders = optional_number<std::int64_t>(t[6], "max_open"),
                   .rate = per_sec ? std::optional<RateLimit>{{*per_sec, *burst}} : std::nullopt}};
  }
  if (t[0] == "K") {
    expect_fields(t, 5);
    return KillSwitch{.seq_no = number<SeqNo>(t[1], "seq"),
                      .account_id = parse_scope(t[2]),
                      .engaged = parse_choice(t[3], "ON", "OFF", "kill switch state"),
                      .cancel_open = parse_choice(t[4], "CANCEL", "KEEP", "kill switch action")};
  }
  if (t[0] == "T") {
    expect_fields(t, 2);
    const auto ts = number<Timestamp>(t[1], "time");
    if (ts < 0) bad_field("time", t[1]);
    return ClockSet{.timestamp = ts};
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

void append_command_output(const std::vector<Event>& events, SeqNo seq_no,
                           const OrderBook& book, std::string& out) {
  for (const auto& e : events) (out += to_string(e)) += '\n';
  (out += format_top_of_book(seq_no, book)) += '\n';
}

void run_commands(std::istream& in, std::ostream& out, const std::vector<Command>& prefix) {
  Engine engine;
  std::vector<Event> events;
  std::string text;
  Timestamp now = 0;
  const auto run = [&](const Command& command) {
    events.clear();
    engine.process(command, now, events);
    text.clear();
    append_command_output(events, seq_no_of(command), engine.book(), text);
    out << text;
  };
  SeqNo seq = 0;
  for (Command command : prefix) {
    std::visit([&](auto& c) { c.seq_no = ++seq; }, command);
    run(command);
  }
  std::string line;
  for (std::size_t line_no = 1; std::getline(in, line); ++line_no) {
    std::optional<TextLine> parsed;
    try {
      parsed = parse_line(line);
      if (const auto* set = parsed ? std::get_if<ClockSet>(&*parsed) : nullptr) {
        if (set->timestamp < now) {
          throw ParseError("time " + std::to_string(set->timestamp) + " is before " +
                           std::to_string(now));
        }
        now = set->timestamp;
        continue;
      }
    } catch (const ParseError& e) {
      throw ParseError("line " + std::to_string(line_no) + ": " + e.what());
    }
    if (parsed) run(std::get<Command>(*parsed));
  }
  out << format_depth(engine.book());
}

}  // namespace mf
