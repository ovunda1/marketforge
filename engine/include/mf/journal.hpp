#pragma once

#include <cstddef>
#include <cstdint>
#include <fstream>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>

#include "mf/commands.hpp"
#include "mf/types.hpp"

namespace mf {

// Binary command journal: the source of truth for replay. Layout, error handling and
// durability guarantees are specified in docs/journal-format.md.
//
// Every command that receives a seq_no is journaled, including ones the engine rejects
// and admin commands: a reject depends on book and risk state, so it is only known
// after the engine runs, and replay must reproduce every event the client saw.

inline constexpr std::string_view kJournalMagic{"MFJOURNL", 8};
inline constexpr std::uint16_t kJournalVersion = 2;  // reads 1 and 2
inline constexpr std::size_t kJournalHeaderSize = 16;
// length field + its covered bytes (seq_no, timestamp, cmd_type) + crc, without payload.
inline constexpr std::size_t kRecordOverhead = 4 + 8 + 8 + 1 + 4;

// One journaled command. The command's own seq_no is the record's seq_no.
struct JournalRecord {
  Timestamp timestamp{};
  Command command;

  SeqNo seq_no() const;
  bool operator==(const JournalRecord&) const = default;
};

class JournalError : public std::runtime_error {
 public:
  enum class Kind : std::uint8_t {
    Io,                  // cannot open, read or write the file
    BadHeader,           // wrong magic, unsupported version, or header CRC mismatch
    Truncated,           // file ends inside the header or a record
    Corrupt,             // record CRC mismatch, bad length, type or enum value
    SequenceGap,         // seq_no is not the previous seq_no + 1
    TimestampBackwards,  // timestamp is earlier than the previous record's
  };

  JournalError(Kind kind, const std::string& message)
      : std::runtime_error(message), kind_(kind) {}
  Kind kind() const { return kind_; }

 private:
  Kind kind_;
};

// Exact byte encodings, exposed so tests can pin the format and craft bad files.
std::string encode_journal_header(std::uint16_t version = kJournalVersion);
std::string encode_record(const JournalRecord& record);

// Creates a new journal file (never overwrites an existing one) and appends records.
// Each append is one write(2) of the whole record, so a record handed to the OS
// survives a process crash. Only sync() makes records survive an OS crash or power
// loss (docs/journal-format.md, "Durability"). A crash in the middle of an append
// leaves a torn final record, which the reader reports as Truncated.
class JournalWriter {
 public:
  // Throws JournalError(Io) if the file exists or cannot be created.
  explicit JournalWriter(const std::string& path);
  ~JournalWriter();
  JournalWriter(const JournalWriter&) = delete;
  JournalWriter& operator=(const JournalWriter&) = delete;

  // Opens an existing journal to continue it (a restarted live sequencer, M10). Every
  // record is read and validated first. A torn final record (Truncated: a crash during
  // its append) is cut off and the cut is synced: write-ahead means the engine never
  // processed it and nobody saw its events. Any other damage, a missing file or a
  // version-1 journal (which cannot hold admin commands) throws JournalError and leaves
  // the file unchanged. `trimmed_bytes`, if given, receives the size of the cut.
  static std::unique_ptr<JournalWriter> resume(const std::string& path,
                                               std::uint64_t* trimmed_bytes = nullptr);

  // Throws std::invalid_argument if the record would make an invalid journal
  // (seq_no not last + 1, or an earlier timestamp), JournalError(Io) on write failure.
  void append(const JournalRecord& record);

  // Flushes every appended record to stable storage: fdatasync(2), or fsync(2) on macOS,
  // where neither flushes the drive's own write cache (that needs F_FULLFSYNC). Throws
  // JournalError(Io) on failure.
  void sync();

  SeqNo last_seq_no() const { return last_seq_no_; }
  // The file's size: the offset the next record will be written at.
  std::uint64_t bytes_written() const { return bytes_written_; }

 private:
  JournalWriter() = default;
  void write_all(std::string_view bytes);

  std::string path_;
  int fd_{-1};
  SeqNo last_seq_no_{};
  std::optional<Timestamp> last_timestamp_;
  std::uint64_t bytes_written_{};
};

// Reads and validates a journal record by record. The header is checked on
// construction. next() returns nullopt only at a clean end of file (on a record
// boundary); any damage throws JournalError naming the file, the byte offset and the
// last good seq_no, so a bad record is never returned.
class JournalReader {
 public:
  explicit JournalReader(const std::string& path);

  std::optional<JournalRecord> next();

  // Continues reading at `offset` (a record boundary, e.g. from a snapshot), expecting
  // the next record to be seq_no last_seq_no + 1 with a timestamp >= last_timestamp.
  void seek(std::uint64_t offset, SeqNo last_seq_no, Timestamp last_timestamp);

  // Byte offset just after the last record returned (the header size initially).
  std::uint64_t offset() const { return offset_; }
  SeqNo last_seq_no() const { return last_seq_no_; }
  std::optional<Timestamp> last_timestamp() const { return last_timestamp_; }

 private:
  [[noreturn]] void fail(JournalError::Kind kind, std::uint64_t at,
                         const std::string& what) const;

  std::string path_;
  std::ifstream in_;
  std::uint64_t file_size_{};
  std::uint16_t version_{};
  std::uint64_t offset_{};
  SeqNo last_seq_no_{};
  std::optional<Timestamp> last_timestamp_;
};

}  // namespace mf
