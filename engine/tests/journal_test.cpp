#include <gtest/gtest.h>

#include <algorithm>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

#include "mf/checksum.hpp"
#include "mf/journal.hpp"
#include "temp_dir.hpp"

// Binary journal format and validation (docs/journal-format.md).

namespace mf {
namespace {

using test::read_bytes;
using test::TempDir;
using test::write_bytes;
using Kind = JournalError::Kind;

std::string unhex(std::string_view hex) {
  std::string out;
  for (std::size_t i = 0; i + 1 < hex.size(); i += 2) {
    out += static_cast<char>(std::stoi(std::string(hex.substr(i, 2)), nullptr, 16));
  }
  return out;
}

JournalRecord new_order(SeqNo seq, Timestamp ts) {
  return {.timestamp = ts,
          .command = NewOrder{.seq_no = seq, .order_id = 42, .account_id = 7,
                              .side = Side::Sell, .type = OrderType::Limit,
                              .tif = TimeInForce::IOC, .post_only = false,
                              .stp = StpMode::CancelOldest, .price = 100, .qty = -3}};
}

JournalRecord cancel(SeqNo seq, Timestamp ts) {
  return {.timestamp = ts, .command = Cancel{.seq_no = seq, .order_id = 42}};
}

JournalRecord replace(SeqNo seq, Timestamp ts) {
  return {.timestamp = ts,
          .command = Replace{.seq_no = seq, .order_id = 42, .price = 101, .qty = 12}};
}

std::vector<JournalRecord> sample_records() {
  return {new_order(1, 1'000'000'000), cancel(2, 1'000'000'000), replace(3, 1'000'000'500)};
}

std::string write_journal(const TempDir& dir, const std::vector<JournalRecord>& records,
                          const std::string& name = "j.mfj") {
  JournalWriter writer(dir.file(name));
  for (const auto& r : records) writer.append(r);
  return dir.file(name);
}

std::vector<JournalRecord> read_all(const std::string& path) {
  JournalReader reader(path);
  std::vector<JournalRecord> out;
  while (auto r = reader.next()) out.push_back(*r);
  return out;
}

// Reads until the reader throws; returns the records read before that and the error.
struct Partial {
  std::vector<JournalRecord> records;
  std::optional<Kind> error;
  std::string message;
};

Partial read_until_error(const std::string& path) {
  Partial p;
  try {
    JournalReader reader(path);
    while (auto r = reader.next()) p.records.push_back(*r);
  } catch (const JournalError& e) {
    p.error = e.kind();
    p.message = e.what();
  }
  return p;
}

// Re-seals a hand-edited record with a correct CRC, so only the edit is wrong.
std::string reseal(std::string record) {
  const std::string body = record.substr(0, record.size() - 4);
  const std::uint32_t crc = crc32(body);
  for (int i = 0; i < 4; ++i) record[body.size() + i] = static_cast<char>((crc >> (8 * i)) & 0xFF);
  return record;
}

// --- Exact bytes ------------------------------------------------------------------

// Expected bytes worked out independently with Python struct + zlib.crc32. Pins the
// field order, widths, little-endian encoding, enum values and CRC coverage.
TEST(JournalFormat, HeaderAndRecordsHaveExactBytes) {
  EXPECT_EQ(encode_journal_header(), unhex("4d464a4f55524e4c0200000081eb83c8"));
  EXPECT_EQ(encode_journal_header(1), unhex("4d464a4f55524e4c010000006f4436da"));
  EXPECT_EQ(encode_record(new_order(1, 1'000'000'000)),
            unhex("36000000" "0100000000000000" "00ca9a3b00000000" "01"
                  "2a00000000000000" "0700000000000000" "01" "00" "01" "00" "01"
                  "6400000000000000" "fdffffffffffffff" "a3e6d1b6"));
  EXPECT_EQ(encode_record(cancel(2, 1'000'000'000)),
            unhex("19000000" "0200000000000000" "00ca9a3b00000000" "02"
                  "2a00000000000000" "79e85599"));
  EXPECT_EQ(encode_record(replace(3, 1'000'000'500)),
            unhex("29000000" "0300000000000000" "f4cb9a3b00000000" "03"
                  "2a00000000000000" "6500000000000000" "0c00000000000000" "5547a73c"));
}

TEST(JournalFormat, WriterProducesHeaderThenRecords) {
  TempDir dir;
  const auto path = write_journal(dir, sample_records());
  std::string expected = encode_journal_header();
  for (const auto& r : sample_records()) expected += encode_record(r);
  EXPECT_EQ(read_bytes(path), expected);
}

// --- Round trip -------------------------------------------------------------------

TEST(Journal, RoundTripsEveryCommandTypeAndExtremeValues) {
  TempDir dir;
  constexpr auto kMaxU = std::numeric_limits<std::uint64_t>::max();
  constexpr auto kMinI = std::numeric_limits<std::int64_t>::min();
  constexpr auto kMaxI = std::numeric_limits<std::int64_t>::max();
  const std::vector<JournalRecord> records = {
      {.timestamp = kMinI,
       .command = NewOrder{.seq_no = 1, .order_id = kMaxU, .account_id = kMaxU,
                           .side = Side::Buy, .type = OrderType::Market,
                           .tif = TimeInForce::FOK, .post_only = true,
                           .stp = StpMode::CancelBoth, .price = kMinI, .qty = kMaxI}},
      {.timestamp = 0, .command = Cancel{.seq_no = 2, .order_id = 0}},
      {.timestamp = kMaxI,
       .command = Replace{.seq_no = 3, .order_id = kMaxU, .price = kMaxI, .qty = kMinI}},
  };
  EXPECT_EQ(read_all(write_journal(dir, records)), records);
}

TEST(Journal, HeaderOnlyJournalHasNoRecords) {
  TempDir dir;
  const auto path = write_journal(dir, {});
  JournalReader reader(path);
  EXPECT_EQ(reader.next(), std::nullopt);
  EXPECT_EQ(reader.offset(), kJournalHeaderSize);
  EXPECT_EQ(reader.last_seq_no(), 0u);
}

TEST(Journal, OffsetTracksRecordBoundaries) {
  TempDir dir;
  const auto path = write_journal(dir, sample_records());
  JournalReader reader(path);
  std::uint64_t expected = kJournalHeaderSize;
  for (const auto& r : sample_records()) {
    ASSERT_EQ(reader.next(), std::optional<JournalRecord>{r});
    expected += encode_record(r).size();
    EXPECT_EQ(reader.offset(), expected);
    EXPECT_EQ(reader.last_seq_no(), r.seq_no());
  }
}

// Non-decreasing, not strictly increasing: two commands can share a timestamp.
TEST(Journal, EqualTimestampsAreAllowed) {
  TempDir dir;
  const std::vector<JournalRecord> records = {cancel(1, 500), cancel(2, 500), cancel(3, 500)};
  EXPECT_EQ(read_all(write_journal(dir, records)), records);
}

TEST(Journal, SeekResumesAtARecordBoundary) {
  TempDir dir;
  const auto path = write_journal(dir, sample_records());
  JournalReader reader(path);
  reader.seek(kJournalHeaderSize + encode_record(sample_records()[0]).size(), 1,
              1'000'000'000);
  EXPECT_EQ(reader.next(), std::optional<JournalRecord>{sample_records()[1]});

  JournalReader wrong(path);
  wrong.seek(kJournalHeaderSize, 5, 0);  // expects seq_no 6, finds 1
  EXPECT_THROW(wrong.next(), JournalError);
  JournalReader beyond(path);
  EXPECT_THROW(beyond.seek(read_bytes(path).size() + 1, 3, 0), JournalError);
}

// --- Writer -----------------------------------------------------------------------

TEST(JournalWriter, RefusesToOverwriteAnExistingFile) {
  TempDir dir;
  write_bytes(dir.file("existing.mfj"), "precious");
  try {
    JournalWriter writer(dir.file("existing.mfj"));
    FAIL() << "expected JournalError";
  } catch (const JournalError& e) {
    EXPECT_EQ(e.kind(), Kind::Io);
  }
  EXPECT_EQ(read_bytes(dir.file("existing.mfj")), "precious");
}

TEST(JournalWriter, RefusesRecordsThatWouldMakeAnInvalidJournal) {
  TempDir dir;
  JournalWriter writer(dir.file("j.mfj"));
  EXPECT_THROW(writer.append(cancel(2, 0)), std::invalid_argument);  // must start at 1
  writer.append(cancel(1, 100));
  EXPECT_THROW(writer.append(cancel(3, 100)), std::invalid_argument);  // gap
  EXPECT_THROW(writer.append(cancel(2, 99)), std::invalid_argument);   // time backwards
  writer.append(cancel(2, 100));                                      // equal time is fine
  EXPECT_EQ(writer.last_seq_no(), 2u);
}

// --- Damaged files ----------------------------------------------------------------

TEST(JournalReader, RejectsBadHeaders) {
  TempDir dir;
  const std::string good = encode_journal_header();

  std::string magic = good;
  magic[0] = 'X';
  write_bytes(dir.file("magic.mfj"), magic);
  auto p = read_until_error(dir.file("magic.mfj"));
  EXPECT_EQ(p.error, Kind::BadHeader);
  EXPECT_NE(p.message.find("not a MarketForge journal"), std::string::npos) << p.message;

  // Version 3 with a correct header CRC, so the version itself is what is reported.
  write_bytes(dir.file("v3.mfj"), encode_journal_header(3));
  p = read_until_error(dir.file("v3.mfj"));
  EXPECT_EQ(p.error, Kind::BadHeader);
  EXPECT_NE(p.message.find("version 3"), std::string::npos) << p.message;
  write_bytes(dir.file("v0.mfj"), encode_journal_header(0));
  EXPECT_EQ(read_until_error(dir.file("v0.mfj")).error, Kind::BadHeader);

  std::string bad_crc = good;
  bad_crc[15] ^= 0x01;
  write_bytes(dir.file("crc.mfj"), bad_crc);
  EXPECT_EQ(read_until_error(dir.file("crc.mfj")).error, Kind::BadHeader);

  EXPECT_EQ(read_until_error(dir.file("missing.mfj")).error, Kind::Io);
}

// Every possible cut: inside the header, inside each record, and on each boundary.
// A partial record is never returned; everything before it is.
TEST(JournalReader, DetectsTruncationAtEveryByte) {
  TempDir dir;
  const std::string full = read_bytes(write_journal(dir, sample_records()));
  std::vector<std::size_t> boundaries = {kJournalHeaderSize};
  for (const auto& r : sample_records()) {
    boundaries.push_back(boundaries.back() + encode_record(r).size());
  }

  for (std::size_t cut = 0; cut < full.size(); ++cut) {
    write_bytes(dir.file("cut.mfj"), full.substr(0, cut));
    const auto p = read_until_error(dir.file("cut.mfj"));
    std::size_t complete = 0;  // records entirely before the cut
    while (complete + 1 < boundaries.size() && boundaries[complete + 1] <= cut) ++complete;
    const bool on_boundary =
        std::find(boundaries.begin(), boundaries.end(), cut) != boundaries.end();

    ASSERT_EQ(p.records.size(), complete) << "cut " << cut;
    if (on_boundary) {
      EXPECT_FALSE(p.error.has_value()) << "cut " << cut << ": " << p.message;
    } else {
      ASSERT_EQ(p.error, Kind::Truncated) << "cut " << cut << ": " << p.message;
      if (cut >= kJournalHeaderSize) {
        const std::string at = "byte offset " + std::to_string(boundaries[complete]);
        EXPECT_NE(p.message.find(at), std::string::npos) << p.message;
        const std::string last = "last good seq_no " + std::to_string(complete);
        EXPECT_NE(p.message.find(last), std::string::npos) << p.message;
      }
    }
  }
}

// Flipping any single bit of any record is caught before that record is returned.
// A flipped length can instead run past the end of the file (Truncated); everything
// else is a CRC mismatch.
TEST(JournalReader, DetectsCorruptionOfEveryRecordByte) {
  TempDir dir;
  const std::string full = read_bytes(write_journal(dir, sample_records()));
  std::size_t start = kJournalHeaderSize;
  for (std::size_t k = 0; k < sample_records().size(); ++k) {
    const std::size_t size = encode_record(sample_records()[k]).size();
    for (std::size_t i = start; i < start + size; ++i) {
      std::string damaged = full;
      damaged[i] ^= 0x10;
      write_bytes(dir.file("bad.mfj"), damaged);
      const auto p = read_until_error(dir.file("bad.mfj"));
      ASSERT_EQ(p.records.size(), k) << "byte " << i;
      ASSERT_TRUE(p.error.has_value()) << "byte " << i;
      if (i < start + 4) {
        EXPECT_TRUE(*p.error == Kind::Corrupt || *p.error == Kind::Truncated) << "byte " << i;
      } else {
        EXPECT_EQ(*p.error, Kind::Corrupt) << "byte " << i << ": " << p.message;
      }
    }
    start += size;
  }
}

TEST(JournalReader, RejectsAnOutOfRangeLengthWithoutAllocating) {
  TempDir dir;
  for (const std::string length : {std::string("\xff\xff\xff\xff", 4), std::string(4, '\0')}) {
    write_bytes(dir.file("len.mfj"), encode_journal_header() + length + std::string(64, 'x'));
    const auto p = read_until_error(dir.file("len.mfj"));
    EXPECT_EQ(p.error, Kind::Corrupt) << p.message;
  }
}

// A valid CRC does not make a record valid: type, length and enum values are checked.
TEST(JournalReader, RejectsBadFieldsUnderAValidCrc) {
  TempDir dir;
  const std::string good = encode_record(new_order(1, 0));
  const auto check = [&](std::string record, const char* what) {
    write_bytes(dir.file("f.mfj"), encode_journal_header() + reseal(std::move(record)));
    const auto p = read_until_error(dir.file("f.mfj"));
    EXPECT_EQ(p.error, Kind::Corrupt) << what << ": " << p.message;
    EXPECT_TRUE(p.records.empty()) << what;
  };
  constexpr std::size_t kType = 20, kSide = 37, kOrderType = 38, kTif = 39, kPostOnly = 40,
                        kStp = 41;
  std::string r = good;
  r[kType] = 9;
  check(r, "unknown command type");
  r = good;
  r[kType] = 2;  // a Cancel needs 8 payload bytes, not 37
  check(r, "length does not match type");
  for (const std::size_t field : {kSide, kOrderType, kTif, kPostOnly, kStp}) {
    r = good;
    r[field] = 7;
    check(r, "enum value out of range");
  }
}

TEST(JournalReader, RejectsSequenceGaps) {
  TempDir dir;
  const std::string header = encode_journal_header();
  write_bytes(dir.file("gap.mfj"), header + encode_record(cancel(1, 0)) +
                                       encode_record(cancel(2, 0)) +
                                       encode_record(cancel(4, 0)));
  auto p = read_until_error(dir.file("gap.mfj"));
  EXPECT_EQ(p.records.size(), 2u);
  EXPECT_EQ(p.error, Kind::SequenceGap);
  EXPECT_NE(p.message.find("seq_no 4"), std::string::npos) << p.message;

  write_bytes(dir.file("start.mfj"), header + encode_record(cancel(2, 0)));
  p = read_until_error(dir.file("start.mfj"));
  EXPECT_TRUE(p.records.empty());
  EXPECT_EQ(p.error, Kind::SequenceGap);
}

TEST(JournalReader, RejectsTimestampsGoingBackwards) {
  TempDir dir;
  write_bytes(dir.file("ts.mfj"), encode_journal_header() + encode_record(cancel(1, 100)) +
                                      encode_record(cancel(2, 100)) +
                                      encode_record(cancel(3, 99)));
  const auto p = read_until_error(dir.file("ts.mfj"));
  EXPECT_EQ(p.records.size(), 2u);  // the equal timestamp is fine
  EXPECT_EQ(p.error, Kind::TimestampBackwards);
}

// --- Version 2: admin commands and DAY (M5) -----------------------------------------

constexpr Timestamp kT = 1'000'000'000;

std::vector<JournalRecord> admin_records() {
  return {
      {.timestamp = kT, .command = SetSession{.seq_no = 4, .state = SessionState::Closed}},
      {.timestamp = kT,
       .command = SetInstrumentLimits{.seq_no = 5,
                                      .limits = {.max_qty = 1000, .reference_price = 10000}}},
      {.timestamp = kT,
       .command = SetAccountLimits{.seq_no = 6, .account_id = 7,
                                   .limits = {.max_notional = 1'000'000,
                                              .rate = RateLimit{.per_sec = 100, .burst = 20}}}},
      {.timestamp = kT,
       .command = KillSwitch{.seq_no = 7, .account_id = std::nullopt, .engaged = true,
                             .cancel_open = true}},
  };
}

// Expected bytes worked out independently with Python struct + zlib.crc32.
TEST(JournalFormatV2, AdminRecordsHaveExactBytes) {
  const auto r = admin_records();
  EXPECT_EQ(encode_record(r[0]),
            unhex("12000000" "0400000000000000" "00ca9a3b00000000" "04" "03" "dea18ee0"));
  EXPECT_EQ(encode_record(r[1]),
            unhex("2a000000" "0500000000000000" "00ca9a3b00000000" "05" "05"
                  "e803000000000000" "0000000000000000" "1027000000000000" "027204d8"));
  EXPECT_EQ(encode_record(r[2]),
            unhex("4b000000" "0600000000000000" "00ca9a3b00000000" "06" "01"
                  "0700000000000000" "11" "40420f0000000000" "0000000000000000"
                  "0000000000000000" "0000000000000000" "6400000000000000"
                  "1400000000000000" "30f9b74a"));
  EXPECT_EQ(encode_record(r[3]),
            unhex("1c000000" "0700000000000000" "00ca9a3b00000000" "07" "00"
                  "0000000000000000" "01" "01" "b6e1793c"));
}

TEST(JournalV2, RoundTripsAdminCommandsAndDayOrders) {
  TempDir dir;
  constexpr auto kMaxI = std::numeric_limits<std::int64_t>::max();
  constexpr Timestamp kLater = 2 * kT;  // after the sample records
  std::vector<JournalRecord> records = sample_records();
  auto day = new_order(4, kLater);
  std::get<NewOrder>(day.command).tif = TimeInForce::Day;
  records.push_back(day);
  SeqNo seq = 5;
  for (auto r : admin_records()) {
    std::visit([&](auto& c) { c.seq_no = seq++; }, r.command);
    r.timestamp = kLater;
    records.push_back(r);
  }
  const std::vector<Command> more = {
      SetSession{.state = SessionState::PreOpen},
      SetSession{.state = SessionState::Continuous},
      SetSession{.state = SessionState::Halted},
      SetInstrumentLimits{.limits = {}},
      SetInstrumentLimits{.limits = {.max_qty = kMaxI, .band_bps = -5, .reference_price = 0}},
      SetAccountLimits{.account_id = std::nullopt, .limits = {}},
      SetAccountLimits{.account_id = 0,
                       .limits = {.max_notional = -1, .position_limit = kMaxI, .credit = 0,
                                  .max_open_orders = 3, .rate = RateLimit{}}},
      KillSwitch{.account_id = 9, .engaged = false, .cancel_open = false},
      KillSwitch{.account_id = 0, .engaged = true, .cancel_open = false},
  };
  for (auto c : more) {
    std::visit([&](auto& x) { x.seq_no = seq++; }, c);
    records.push_back({.timestamp = kLater, .command = c});
  }
  EXPECT_EQ(read_all(write_journal(dir, records)), records);
}

TEST(JournalV2, ReadsVersion1Journals) {
  TempDir dir;
  std::string bytes = encode_journal_header(1);
  for (const auto& r : sample_records()) bytes += encode_record(r);
  write_bytes(dir.file("v1.mfj"), bytes);
  EXPECT_EQ(read_all(dir.file("v1.mfj")), sample_records());
}

// Version 1 had no admin commands and no DAY: a v1 file containing them is damaged.
TEST(JournalV2, Version1JournalsCannotHoldVersion2Commands) {
  TempDir dir;
  write_bytes(dir.file("admin.mfj"), encode_journal_header(1) + encode_record(cancel(1, 0)) +
                                         encode_record({.timestamp = 0,
                                                        .command = SetSession{.seq_no = 2}}));
  auto p = read_until_error(dir.file("admin.mfj"));
  EXPECT_EQ(p.error, Kind::Corrupt) << p.message;
  EXPECT_EQ(p.records.size(), 1u);

  auto day = new_order(1, 0);
  std::get<NewOrder>(day.command).tif = TimeInForce::Day;
  write_bytes(dir.file("day.mfj"), encode_journal_header(1) + encode_record(day));
  p = read_until_error(dir.file("day.mfj"));
  EXPECT_EQ(p.error, Kind::Corrupt) << p.message;
}

// Each command has exactly one encoding: unset fields must be zero, flags 0 or 1.
TEST(JournalV2, RejectsBadAdminFieldsUnderAValidCrc) {
  TempDir dir;
  const auto check = [&](std::string record, const std::string& what) {
    write_bytes(dir.file("f.mfj"), encode_journal_header() + reseal(std::move(record)));
    const auto p = read_until_error(dir.file("f.mfj"));
    EXPECT_EQ(p.error, Kind::Corrupt) << what << ": " << p.message;
  };
  const auto with_seq1 = [](JournalRecord r) {
    std::visit([](auto& c) { c.seq_no = 1; }, r.command);
    return encode_record(r);
  };
  constexpr std::size_t kPayload = 21;
  const auto rs = admin_records();

  std::string r = with_seq1(rs[0]);
  r[kPayload] = 4;
  check(r, "session state out of range");

  const std::string ins = with_seq1(rs[1]);
  r = ins;
  r[kPayload] = 8;
  check(r, "unknown instrument mask bit");
  r = ins;
  r[kPayload + 1 + 8] = 1;  // band_bps is unset but non-zero
  check(r, "unset instrument field not zero");

  const std::string acc = with_seq1(rs[2]);
  r = acc;
  r[kPayload] = 2;
  check(r, "account scope out of range");
  r = acc;
  r[kPayload] = 0;  // defaults scope with account 7
  check(r, "defaults scope with an account id");
  r = acc;
  r[kPayload + 9] = 0x20;
  check(r, "unknown account mask bit");
  r = acc;
  r[kPayload + 10 + 8] = 1;  // position_limit unset but non-zero
  check(r, "unset account field not zero");

  const std::string kill = with_seq1(rs[3]);
  r = kill;
  r[kPayload] = 2;
  check(r, "kill scope out of range");
  r = kill;
  r[kPayload + 1] = 1;  // global scope with an account id
  check(r, "global scope with an account id");
  r = kill;
  r[kPayload + 9] = 2;
  check(r, "engaged not 0 or 1");
  r = kill;
  r[kPayload + 10] = 2;
  check(r, "cancel_open not 0 or 1");
}

}  // namespace
}  // namespace mf
