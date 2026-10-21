#include "mf/snapshot.hpp"

#include <cstdio>
#include <filesystem>
#include <fstream>
#include <iterator>

#include "byte_io.hpp"
#include "mf/checksum.hpp"

namespace mf {

namespace {

using detail::ByteReader;
using detail::put_i64;
using detail::put_u16;
using detail::put_u32;
using detail::put_u64;
using detail::put_u8;

constexpr std::size_t kHeaderSize = 8 + 2 + 2;
// Smallest encoding of one order (version 1; version 2 adds tif): what a count field
// may claim per remaining byte.
constexpr std::size_t kOrderSize = 8 + 8 + 8 + 8 + 8 + 8 + 1 + 1;
constexpr std::size_t kLevelMinSize = 8 + 8 + kOrderSize;
// Account limits: mask u8 + six i64 (max_notional, position_limit, credit,
// max_open_orders, rate per_sec, rate burst); unset fields are 0.
constexpr std::size_t kAccountLimitsSize = 1 + 6 * 8;

void put_optional(std::string& out, std::uint8_t& mask, int bit, std::optional<std::int64_t> v) {
  if (v) mask |= static_cast<std::uint8_t>(1u << bit);
  put_i64(out, v.value_or(0));
}

void put_account_limits(std::string& out, const AccountLimits& l) {
  std::uint8_t mask = 0;
  std::string fields;
  put_optional(fields, mask, 0, l.max_notional);
  put_optional(fields, mask, 1, l.position_limit);
  put_optional(fields, mask, 2, l.credit);
  put_optional(fields, mask, 3, l.max_open_orders);
  if (l.rate) mask |= 1u << 4;
  put_i64(fields, l.rate ? l.rate->per_sec : 0);
  put_i64(fields, l.rate ? l.rate->burst : 0);
  put_u8(out, mask);
  out += fields;
}

void put_risk(std::string& out, const RiskState& r) {
  put_u8(out, static_cast<std::uint8_t>(r.session));
  put_u8(out, r.global_kill ? 1 : 0);
  put_u8(out, r.last_trade_price ? 1 : 0);
  put_i64(out, r.last_trade_price.value_or(0));
  std::uint8_t mask = 0;
  std::string fields;
  put_optional(fields, mask, 0, r.instrument.max_qty);
  put_optional(fields, mask, 1, r.instrument.band_bps);
  put_optional(fields, mask, 2, r.instrument.reference_price);
  put_u8(out, mask);
  out += fields;
  put_u8(out, r.default_limits ? 1 : 0);
  put_account_limits(out, r.default_limits.value_or(AccountLimits{}));
  put_u64(out, r.account_limits.size());
  for (const auto& e : r.account_limits) {
    put_u64(out, e.account_id);
    put_account_limits(out, e.limits);
  }
  put_u64(out, r.killed_accounts.size());
  for (const AccountId id : r.killed_accounts) put_u64(out, id);
  put_u64(out, r.positions.size());
  for (const auto& p : r.positions) {
    put_u64(out, p.account_id);
    put_i64(out, p.qty);
  }
  put_u64(out, r.buckets.size());
  for (const auto& b : r.buckets) {
    put_u64(out, b.account_id);
    put_i64(out, b.tokens);
    put_i64(out, b.last_refill);
  }
}

void put_side(std::string& out, const std::vector<BookState::Level>& levels) {
  put_u64(out, levels.size());
  for (const auto& level : levels) {
    put_i64(out, level.price);
    put_u64(out, level.orders.size());
    for (const auto& o : level.orders) {
      put_u64(out, o.order_id);
      put_u64(out, o.account_id);
      put_i64(out, o.qty);
      put_i64(out, o.leaves_qty);
      put_i64(out, o.filled_qty);
      put_u64(out, o.priority_seq_no);
      put_u8(out, o.post_only ? 1 : 0);
      put_u8(out, static_cast<std::uint8_t>(o.stp));
      put_u8(out, static_cast<std::uint8_t>(o.tif));
    }
  }
}

// A count read from the file must fit in the bytes that are left, so a corrupt count
// can never trigger a huge allocation.
std::uint64_t count(ByteReader& r, std::size_t min_item_size, const char* what) {
  const std::uint64_t n = r.u64();
  if (n > r.remaining() / min_item_size) {
    throw SnapshotError(std::string("corrupt snapshot: ") + what + " count " +
                        std::to_string(n) + " exceeds the file size");
  }
  return n;
}

std::uint8_t flag(ByteReader& r, const char* what) {
  const std::uint8_t v = r.u8();
  if (v > 1) throw SnapshotError(std::string("corrupt snapshot: ") + what + " is not 0 or 1");
  return v;
}

// Reads an optional field written by put_optional.
std::optional<std::int64_t> optional_field(ByteReader& r, std::uint8_t mask, int bit) {
  const std::int64_t v = r.i64();
  if (mask & (1u << bit)) return v;
  if (v != 0) throw SnapshotError("corrupt snapshot: unset limit is not zero");
  return std::nullopt;
}

AccountLimits read_account_limits(ByteReader& r) {
  const std::uint8_t mask = r.u8();
  if (mask & ~0b11111u) throw SnapshotError("corrupt snapshot: unknown account limit bits");
  AccountLimits l;
  l.max_notional = optional_field(r, mask, 0);
  l.position_limit = optional_field(r, mask, 1);
  l.credit = optional_field(r, mask, 2);
  l.max_open_orders = optional_field(r, mask, 3);
  const auto per_sec = optional_field(r, mask, 4);
  const auto burst = optional_field(r, mask, 4);
  if (per_sec) l.rate = RateLimit{.per_sec = *per_sec, .burst = *burst};
  return l;
}

RiskState read_risk(ByteReader& r) {
  RiskState s;
  const std::uint8_t session = r.u8();
  if (session > 3) throw SnapshotError("corrupt snapshot: bad session state");
  s.session = static_cast<SessionState>(session);
  s.global_kill = flag(r, "global kill switch") == 1;
  const bool has_trade = flag(r, "last trade flag") == 1;
  const Price last_trade = r.i64();
  if (has_trade) {
    s.last_trade_price = last_trade;
  } else if (last_trade != 0) {
    throw SnapshotError("corrupt snapshot: unset last trade price is not zero");
  }
  const std::uint8_t mask = r.u8();
  if (mask & ~0b111u) throw SnapshotError("corrupt snapshot: unknown instrument limit bits");
  s.instrument.max_qty = optional_field(r, mask, 0);
  s.instrument.band_bps = optional_field(r, mask, 1);
  s.instrument.reference_price = optional_field(r, mask, 2);
  const bool has_defaults = flag(r, "default limits flag") == 1;
  const AccountLimits defaults = read_account_limits(r);
  if (has_defaults) {
    s.default_limits = defaults;
  } else if (defaults != AccountLimits{}) {
    throw SnapshotError("corrupt snapshot: unset default limits are not empty");
  }
  s.account_limits.resize(count(r, 8 + kAccountLimitsSize, "account limits"));
  for (auto& e : s.account_limits) {
    e.account_id = r.u64();
    e.limits = read_account_limits(r);
  }
  s.killed_accounts.resize(count(r, 8, "killed account"));
  for (auto& id : s.killed_accounts) id = r.u64();
  s.positions.resize(count(r, 16, "position"));
  for (auto& p : s.positions) {
    p.account_id = r.u64();
    p.qty = r.i64();
  }
  s.buckets.resize(count(r, 24, "bucket"));
  for (auto& b : s.buckets) {
    b.account_id = r.u64();
    b.tokens = r.i64();
    b.last_refill = r.i64();
  }
  return s;
}

std::vector<BookState::Level> read_side(ByteReader& r, std::uint16_t version) {
  std::vector<BookState::Level> levels(count(r, kLevelMinSize, "level"));
  for (auto& level : levels) {
    level.price = r.i64();
    level.orders.resize(count(r, kOrderSize, "order"));
    for (auto& o : level.orders) {
      o.order_id = r.u64();
      o.account_id = r.u64();
      o.qty = r.i64();
      o.leaves_qty = r.i64();
      o.filled_qty = r.i64();
      o.priority_seq_no = r.u64();
      const std::uint8_t post_only = r.u8();
      const std::uint8_t stp = r.u8();
      // Version 1 had only GTC resting orders.
      const std::uint8_t tif = version >= 2 ? r.u8() : static_cast<std::uint8_t>(TimeInForce::GTC);
      if (post_only > 1 || stp > 2 || tif > 3) throw SnapshotError("corrupt snapshot: bad order flags");
      o.post_only = post_only == 1;
      o.stp = static_cast<StpMode>(stp);
      o.tif = static_cast<TimeInForce>(tif);
    }
  }
  return levels;
}

}  // namespace

std::string encode_snapshot(const Snapshot& snapshot) {
  const BookState& b = snapshot.book;
  std::string out(kSnapshotMagic);
  put_u16(out, kSnapshotVersion);
  put_u16(out, 0);  // reserved
  put_u64(out, b.last_seq_no);
  put_i64(out, snapshot.last_timestamp);
  put_u64(out, snapshot.journal_offset);
  put_u64(out, b.next_trade_id);
  put_u64(out, b.used_ids.size());
  for (const OrderId id : b.used_ids) put_u64(out, id);
  put_side(out, b.bids);
  put_side(out, b.asks);
  put_risk(out, snapshot.risk);
  put_u32(out, crc32(out));
  return out;
}

Snapshot decode_snapshot(std::string_view bytes) {
  if (bytes.size() < kHeaderSize + 4) throw SnapshotError("truncated snapshot");
  if (bytes.substr(0, 8) != kSnapshotMagic) {
    throw SnapshotError("not a MarketForge snapshot (bad magic)");
  }
  ByteReader header(bytes.substr(8, 4));
  const std::uint16_t version = header.u16();
  if (version < 1 || version > kSnapshotVersion) {
    throw SnapshotError("unsupported snapshot format version " + std::to_string(version) +
                        " (this build reads versions 1 to " + std::to_string(kSnapshotVersion) +
                        ")");
  }
  const std::string_view covered = bytes.substr(0, bytes.size() - 4);
  if (ByteReader(bytes.substr(bytes.size() - 4)).u32() != crc32(covered)) {
    throw SnapshotError("corrupt snapshot: CRC mismatch (damaged or truncated)");
  }
  if (header.u16() != 0) throw SnapshotError("corrupt snapshot: reserved field is not zero");

  try {
    ByteReader r(covered.substr(kHeaderSize));
    Snapshot s;
    s.book.last_seq_no = r.u64();
    s.last_timestamp = r.i64();
    s.journal_offset = r.u64();
    s.book.next_trade_id = r.u64();
    s.book.used_ids.resize(count(r, 8, "used id"));
    for (auto& id : s.book.used_ids) id = r.u64();
    s.book.bids = read_side(r, version);
    s.book.asks = read_side(r, version);
    if (version >= 2) s.risk = read_risk(r);  // version 1: defaults
    if (r.remaining() != 0) throw SnapshotError("corrupt snapshot: trailing bytes");
    return s;
  } catch (const detail::ShortRead&) {
    throw SnapshotError("corrupt snapshot: ends in the middle of a field");
  }
}

void write_snapshot_file(const std::string& path, const Snapshot& snapshot) {
  const std::string bytes = encode_snapshot(snapshot);
  const std::string tmp = path + ".tmp";
  {
    std::ofstream out(tmp, std::ios::binary | std::ios::trunc);
    out.write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
    out.flush();
    if (!out) throw SnapshotError(tmp + ": cannot write snapshot");
  }
  std::error_code ec;
  std::filesystem::rename(tmp, path, ec);  // atomic on POSIX
  if (ec) throw SnapshotError(path + ": cannot rename snapshot into place: " + ec.message());
}

Snapshot read_snapshot_file(const std::string& path) {
  std::ifstream in(path, std::ios::binary);
  if (!in) throw SnapshotError(path + ": cannot open snapshot");
  const std::string bytes{std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>()};
  try {
    return decode_snapshot(bytes);
  } catch (const SnapshotError& e) {
    throw SnapshotError(path + ": " + e.what());
  }
}

}  // namespace mf
