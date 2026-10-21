#pragma once

// Little-endian fixed-width encoding shared by the journal and snapshot formats. Values
// are written byte by byte, never by copying structs, so neither host endianness nor
// struct padding can reach a file.

#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>

namespace mf::detail {

inline void put_u8(std::string& out, std::uint8_t v) { out += static_cast<char>(v); }

inline void put_u16(std::string& out, std::uint16_t v) {
  for (int i = 0; i < 2; ++i) out += static_cast<char>((v >> (8 * i)) & 0xFF);
}

inline void put_u32(std::string& out, std::uint32_t v) {
  for (int i = 0; i < 4; ++i) out += static_cast<char>((v >> (8 * i)) & 0xFF);
}

inline void put_u64(std::string& out, std::uint64_t v) {
  for (int i = 0; i < 8; ++i) out += static_cast<char>((v >> (8 * i)) & 0xFF);
}

// Two's complement, as the format specifies.
inline void put_i64(std::string& out, std::int64_t v) { put_u64(out, static_cast<std::uint64_t>(v)); }

// Thrown by ByteReader when a read would run past the end; callers translate it into
// their own error type.
struct ShortRead {};

class ByteReader {
 public:
  explicit ByteReader(std::string_view data) : data_(data) {}

  std::uint8_t u8() { return static_cast<std::uint8_t>(take(1)); }
  std::uint16_t u16() { return static_cast<std::uint16_t>(take(2)); }
  std::uint32_t u32() { return static_cast<std::uint32_t>(take(4)); }
  std::uint64_t u64() { return take(8); }
  std::int64_t i64() { return static_cast<std::int64_t>(take(8)); }

  std::size_t position() const { return pos_; }
  std::size_t remaining() const { return data_.size() - pos_; }

 private:
  std::uint64_t take(std::size_t n) {
    if (remaining() < n) throw ShortRead{};
    std::uint64_t v = 0;
    for (std::size_t i = 0; i < n; ++i) {
      v |= std::uint64_t{static_cast<std::uint8_t>(data_[pos_ + i])} << (8 * i);
    }
    pos_ += n;
    return v;
  }

  std::string_view data_;
  std::size_t pos_{};
};

}  // namespace mf::detail
