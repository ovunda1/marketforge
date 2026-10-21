#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>

namespace mf {

// CRC-32/IEEE (the zlib and Ethernet polynomial, reflected 0xEDB88320), used to detect
// corrupt journal records and snapshots. crc32("123456789") == 0xCBF43926.
std::uint32_t crc32(std::string_view data);
// Continues a CRC: crc32_update(crc32(a), b) == crc32(a + b).
std::uint32_t crc32_update(std::uint32_t crc, std::string_view data);

// Incremental SHA-256 (FIPS 180-4) over the canonical event stream. Implemented here
// so the engine and its tools need no crypto library.
class Sha256 {
 public:
  using Digest = std::array<std::uint8_t, 32>;

  Sha256();
  void update(std::string_view data);
  // Finishes the hash; the object must not be updated afterwards.
  Digest finish();

  static std::string hex(const Digest& digest);

 private:
  void compress(const std::uint8_t* block);

  std::array<std::uint32_t, 8> state_;
  std::array<std::uint8_t, 64> buffer_{};
  std::size_t buffered_{};
  std::uint64_t total_bytes_{};
};

// One-shot convenience: lowercase hex SHA-256 of `data`.
std::string sha256_hex(std::string_view data);

}  // namespace mf
