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
// Smallest encoding of one order: what a count field may claim per remaining byte.
constexpr std::size_t kOrderSize = 8 + 8 + 8 + 8 + 8 + 8 + 1 + 1;
constexpr std::size_t kLevelMinSize = 8 + 8 + kOrderSize;

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

std::vector<BookState::Level> read_side(ByteReader& r) {
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
      if (post_only > 1 || stp > 2) throw SnapshotError("corrupt snapshot: bad order flags");
      o.post_only = post_only == 1;
      o.stp = static_cast<StpMode>(stp);
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
  if (version != kSnapshotVersion) {
    throw SnapshotError("unsupported snapshot format version " + std::to_string(version) +
                        " (this build reads version " + std::to_string(kSnapshotVersion) + ")");
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
    s.book.bids = read_side(r);
    s.book.asks = read_side(r);
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
