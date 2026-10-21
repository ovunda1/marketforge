#include "mf/journal.hpp"

#include <fcntl.h>
#include <unistd.h>

#include <cerrno>
#include <cstring>
#include <stdexcept>
#include <type_traits>

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

// Wire values of cmd_type (docs/journal-format.md).
enum class CmdType : std::uint8_t { New = 1, Cancel = 2, Replace = 3 };

// Bytes covered by the length field: seq_no, timestamp, cmd_type and the payload.
constexpr std::uint32_t kBodyFixed = 8 + 8 + 1;
constexpr std::uint32_t kNewPayload = 8 + 8 + 5 + 8 + 8;
constexpr std::uint32_t kCancelPayload = 8;
constexpr std::uint32_t kReplacePayload = 8 + 8 + 8;
constexpr std::uint32_t kMinBody = kBodyFixed + kCancelPayload;
constexpr std::uint32_t kMaxBody = kBodyFixed + kNewPayload;

// Enum wire values are the enumerators' underlying values; pin them, since changing
// one would silently change the meaning of existing journals.
static_assert(static_cast<int>(Side::Sell) == 1 && static_cast<int>(OrderType::Market) == 1);
static_assert(static_cast<int>(TimeInForce::FOK) == 2 && static_cast<int>(StpMode::CancelBoth) == 2);

template <class E>
std::uint8_t wire(E e) {
  return static_cast<std::uint8_t>(e);
}

// Decodes an enum, or returns false if the value is out of range.
template <class E>
bool enum_from(std::uint8_t v, std::uint8_t max, E& out) {
  if (v > max) return false;
  out = static_cast<E>(v);
  return true;
}

std::string header_prefix() {
  std::string out(kJournalMagic);
  put_u16(out, kJournalVersion);
  put_u16(out, 0);  // reserved
  return out;
}

}  // namespace

SeqNo JournalRecord::seq_no() const {
  return std::visit([](const auto& c) { return c.seq_no; }, command);
}

std::string encode_journal_header() {
  std::string out = header_prefix();
  put_u32(out, crc32(out));
  return out;
}

std::string encode_record(const JournalRecord& record) {
  std::string payload;
  CmdType type{};
  std::visit(
      [&](const auto& c) {
        using T = std::decay_t<decltype(c)>;
        if constexpr (std::is_same_v<T, NewOrder>) {
          type = CmdType::New;
          put_u64(payload, c.order_id);
          put_u64(payload, c.account_id);
          put_u8(payload, wire(c.side));
          put_u8(payload, wire(c.type));
          put_u8(payload, wire(c.tif));
          put_u8(payload, c.post_only ? 1 : 0);
          put_u8(payload, wire(c.stp));
          put_i64(payload, c.price);
          put_i64(payload, c.qty);
        } else if constexpr (std::is_same_v<T, Cancel>) {
          type = CmdType::Cancel;
          put_u64(payload, c.order_id);
        } else {
          type = CmdType::Replace;
          put_u64(payload, c.order_id);
          put_i64(payload, c.price);
          put_i64(payload, c.qty);
        }
      },
      record.command);

  std::string out;
  put_u32(out, static_cast<std::uint32_t>(kBodyFixed + payload.size()));
  put_u64(out, record.seq_no());
  put_i64(out, record.timestamp);
  put_u8(out, static_cast<std::uint8_t>(type));
  out += payload;
  put_u32(out, crc32(out));
  return out;
}

// --- Writer -----------------------------------------------------------------------

JournalWriter::JournalWriter(const std::string& path) : path_(path) {
  // O_EXCL: never overwrite, so an existing journal (the source of truth) is safe.
  fd_ = ::open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0644);
  if (fd_ < 0) {
    throw JournalError(JournalError::Kind::Io,
                       path + ": cannot create journal: " + std::strerror(errno));
  }
  write_all(encode_journal_header());
}

JournalWriter::~JournalWriter() {
  if (fd_ >= 0) ::close(fd_);
}

void JournalWriter::append(const JournalRecord& record) {
  if (record.seq_no() != last_seq_no_ + 1) {
    throw std::invalid_argument("journal append: expected seq_no " +
                                std::to_string(last_seq_no_ + 1) + ", got " +
                                std::to_string(record.seq_no()));
  }
  if (last_timestamp_ && record.timestamp < *last_timestamp_) {
    throw std::invalid_argument("journal append: timestamp " +
                                std::to_string(record.timestamp) + " is before " +
                                std::to_string(*last_timestamp_));
  }
  write_all(encode_record(record));
  last_seq_no_ = record.seq_no();
  last_timestamp_ = record.timestamp;
}

// One write(2) per record in the normal case: once it returns, the record is in the
// OS page cache and survives a crash of this process. No fsync (see the header).
void JournalWriter::write_all(std::string_view bytes) {
  while (!bytes.empty()) {
    const ssize_t n = ::write(fd_, bytes.data(), bytes.size());
    if (n < 0) {
      if (errno == EINTR) continue;
      throw JournalError(JournalError::Kind::Io,
                         path_ + ": write failed: " + std::strerror(errno));
    }
    bytes.remove_prefix(static_cast<std::size_t>(n));
    bytes_written_ += static_cast<std::uint64_t>(n);
  }
}

// --- Reader -----------------------------------------------------------------------

JournalReader::JournalReader(const std::string& path) : path_(path) {
  in_.open(path, std::ios::binary);
  if (!in_) throw JournalError(JournalError::Kind::Io, path + ": cannot open journal");
  in_.seekg(0, std::ios::end);
  file_size_ = static_cast<std::uint64_t>(in_.tellg());
  in_.seekg(0);

  if (file_size_ < kJournalHeaderSize) {
    throw JournalError(JournalError::Kind::Truncated,
                       path + ": truncated journal header: file is " +
                           std::to_string(file_size_) + " bytes, the header needs " +
                           std::to_string(kJournalHeaderSize));
  }
  std::string header(kJournalHeaderSize, '\0');
  in_.read(header.data(), static_cast<std::streamsize>(header.size()));
  if (!in_) throw JournalError(JournalError::Kind::Io, path + ": cannot read journal header");

  const auto bad = [&](const std::string& what) {
    throw JournalError(JournalError::Kind::BadHeader, path + ": " + what);
  };
  if (std::string_view(header).substr(0, 8) != kJournalMagic) {
    bad("not a MarketForge journal (bad magic)");
  }
  ByteReader r(std::string_view(header).substr(8));
  const std::uint16_t version = r.u16();
  const std::uint16_t reserved = r.u16();
  const std::uint32_t stored_crc = r.u32();
  if (stored_crc != crc32(std::string_view(header).substr(0, 12))) bad("journal header CRC mismatch");
  if (version != kJournalVersion) {
    bad("unsupported journal format version " + std::to_string(version) +
        " (this build reads version " + std::to_string(kJournalVersion) + ")");
  }
  if (reserved != 0) bad("journal header reserved field is not zero");
  offset_ = kJournalHeaderSize;
}

void JournalReader::fail(JournalError::Kind kind, std::uint64_t at,
                         const std::string& what) const {
  throw JournalError(kind, path_ + ": " + what + " at byte offset " + std::to_string(at) +
                               " (last good seq_no " + std::to_string(last_seq_no_) + ")");
}

void JournalReader::seek(std::uint64_t offset, SeqNo last_seq_no, Timestamp last_timestamp) {
  if (offset < kJournalHeaderSize) {
    fail(JournalError::Kind::Corrupt, offset, "resume offset is inside the journal header");
  }
  if (offset > file_size_) {
    fail(JournalError::Kind::Truncated, offset,
         "resume offset is beyond the end of the journal (" + std::to_string(file_size_) +
             " bytes)");
  }
  in_.clear();
  in_.seekg(static_cast<std::streamoff>(offset));
  offset_ = offset;
  last_seq_no_ = last_seq_no;
  // Nothing processed yet (a snapshot at seq_no 0): no earlier timestamp to respect.
  last_timestamp_ = last_seq_no == 0 ? std::nullopt : std::optional<Timestamp>{last_timestamp};
}

std::optional<JournalRecord> JournalReader::next() {
  using Kind = JournalError::Kind;
  const std::uint64_t at = offset_;
  if (at == file_size_) return std::nullopt;
  const std::uint64_t remaining = file_size_ - at;

  const auto truncated = [&](std::uint64_t needed) {
    fail(Kind::Truncated, at,
         "truncated record: the file ends " + std::to_string(remaining) + " bytes into a " +
             std::to_string(needed) + "-byte record");
  };
  if (remaining < 4) truncated(4);

  std::string bytes(4, '\0');
  in_.read(bytes.data(), 4);
  const std::uint32_t length = ByteReader(bytes).u32();
  // Checked before reading or allocating anything sized by it.
  if (length < kMinBody || length > kMaxBody) {
    fail(Kind::Corrupt, at, "corrupt record: length " + std::to_string(length) + " out of range");
  }
  const std::uint64_t record_size = 4 + std::uint64_t{length} + 4;
  if (remaining < record_size) truncated(record_size);

  bytes.resize(record_size);
  in_.read(bytes.data() + 4, static_cast<std::streamsize>(record_size - 4));
  if (!in_) fail(Kind::Io, at, "read failed");

  const std::string_view covered = std::string_view(bytes).substr(0, 4 + length);
  ByteReader crc_reader(std::string_view(bytes).substr(4 + length));
  if (crc_reader.u32() != crc32(covered)) fail(Kind::Corrupt, at, "corrupt record: CRC mismatch");

  ByteReader r(covered.substr(4));
  const SeqNo seq = r.u64();
  const Timestamp ts = r.i64();
  const std::uint8_t type = r.u8();
  const std::uint32_t payload = length - kBodyFixed;
  const auto corrupt = [&](const std::string& what) { fail(Kind::Corrupt, at, "corrupt record: " + what); };

  JournalRecord record{.timestamp = ts, .command = Cancel{}};
  if (type == static_cast<std::uint8_t>(CmdType::New)) {
    if (payload != kNewPayload) corrupt("length does not match a new-order record");
    NewOrder o{.seq_no = seq};
    o.order_id = r.u64();
    o.account_id = r.u64();
    const std::uint8_t side = r.u8();
    const std::uint8_t order_type = r.u8();
    const std::uint8_t tif = r.u8();
    const std::uint8_t post_only = r.u8();
    const std::uint8_t stp = r.u8();
    if (!enum_from(side, 1, o.side)) corrupt("side " + std::to_string(side) + " out of range");
    if (!enum_from(order_type, 1, o.type)) {
      corrupt("order type " + std::to_string(order_type) + " out of range");
    }
    if (!enum_from(tif, 2, o.tif)) corrupt("time in force " + std::to_string(tif) + " out of range");
    if (post_only > 1) corrupt("post_only " + std::to_string(post_only) + " is not 0 or 1");
    o.post_only = post_only == 1;
    if (!enum_from(stp, 2, o.stp)) corrupt("stp mode " + std::to_string(stp) + " out of range");
    o.price = r.i64();
    o.qty = r.i64();
    record.command = o;
  } else if (type == static_cast<std::uint8_t>(CmdType::Cancel)) {
    if (payload != kCancelPayload) corrupt("length does not match a cancel record");
    record.command = Cancel{.seq_no = seq, .order_id = r.u64()};
  } else if (type == static_cast<std::uint8_t>(CmdType::Replace)) {
    if (payload != kReplacePayload) corrupt("length does not match a replace record");
    Replace c{.seq_no = seq};
    c.order_id = r.u64();
    c.price = r.i64();
    c.qty = r.i64();
    record.command = c;
  } else {
    corrupt("unknown command type " + std::to_string(type));
  }

  if (seq != last_seq_no_ + 1) {
    fail(Kind::SequenceGap, at,
         "sequence gap: expected seq_no " + std::to_string(last_seq_no_ + 1) + ", found seq_no " +
             std::to_string(seq));
  }
  if (last_timestamp_ && ts < *last_timestamp_) {
    fail(Kind::TimestampBackwards, at,
         "timestamp " + std::to_string(ts) + " of seq_no " + std::to_string(seq) +
             " is earlier than the previous " + std::to_string(*last_timestamp_));
  }
  offset_ = at + record_size;
  last_seq_no_ = seq;
  last_timestamp_ = ts;
  return record;
}

}  // namespace mf
