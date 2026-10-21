#include <gtest/gtest.h>

#include <string>

#include "mf/checksum.hpp"

namespace mf {
namespace {

TEST(Crc32, MatchesStandardCheckValue) {
  EXPECT_EQ(crc32("123456789"), 0xCBF43926u);
  EXPECT_EQ(crc32(""), 0u);
  // zlib.crc32(b"The quick brown fox jumps over the lazy dog")
  EXPECT_EQ(crc32("The quick brown fox jumps over the lazy dog"), 0x414FA339u);
}

TEST(Crc32, UpdateContinuesACrc) {
  EXPECT_EQ(crc32_update(crc32("12345"), "6789"), crc32("123456789"));
  EXPECT_EQ(crc32_update(crc32(""), "123456789"), 0xCBF43926u);
}

// FIPS 180-4 / NIST CAVP example vectors.
TEST(Sha256, MatchesNistVectors) {
  EXPECT_EQ(sha256_hex(""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
  EXPECT_EQ(sha256_hex("abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  EXPECT_EQ(sha256_hex("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"),
            "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1");
  EXPECT_EQ(sha256_hex(std::string(1'000'000, 'a')),
            "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0");
}

// Lengths around the 55/56/64-byte padding boundaries.
TEST(Sha256, PaddingBoundaries) {
  EXPECT_EQ(sha256_hex(std::string(55, 'a')),
            "9f4390f8d30c2dd92ec9f095b65e2b9ae9b0a925a5258e241c9f1e910f734318");
  EXPECT_EQ(sha256_hex(std::string(56, 'a')),
            "b35439a4ac6f0948b6d6f9e3c6af0f5f590ce20f1bde7090ef7970686ec6738a");
  EXPECT_EQ(sha256_hex(std::string(64, 'a')),
            "ffe054fe7ae0cb6dc65c3af9b61d5209f439851db43d0ba5997337df154668eb");
}

TEST(Sha256, IncrementalUpdatesMatchOneShot) {
  std::string data;
  for (int i = 0; i < 5000; ++i) data += static_cast<char>((i * 31 + 7) % 256);
  const std::string expected = sha256_hex(data);
  for (const std::size_t chunk : {1u, 3u, 63u, 64u, 65u, 1000u}) {
    Sha256 h;
    for (std::size_t i = 0; i < data.size(); i += chunk) {
      h.update(std::string_view(data).substr(i, chunk));
    }
    EXPECT_EQ(Sha256::hex(h.finish()), expected) << "chunk " << chunk;
  }
}

}  // namespace
}  // namespace mf
