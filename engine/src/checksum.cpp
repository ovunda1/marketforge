#include "mf/checksum.hpp"

#include <algorithm>
#include <cstring>

namespace mf {

namespace {

constexpr std::array<std::uint32_t, 256> make_crc_table() {
  std::array<std::uint32_t, 256> table{};
  for (std::uint32_t i = 0; i < 256; ++i) {
    std::uint32_t c = i;
    for (int k = 0; k < 8; ++k) c = (c & 1) ? 0xEDB88320u ^ (c >> 1) : c >> 1;
    table[i] = c;
  }
  return table;
}

constexpr auto kCrcTable = make_crc_table();

constexpr std::array<std::uint32_t, 64> kRoundConstants = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4,
    0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe,
    0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f,
    0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
    0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc,
    0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
    0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116,
    0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
    0xc67178f2};

constexpr std::uint32_t rotr(std::uint32_t x, int n) { return (x >> n) | (x << (32 - n)); }

}  // namespace

std::uint32_t crc32_update(std::uint32_t crc, std::string_view data) {
  crc = ~crc;
  for (const char ch : data) {
    crc = kCrcTable[(crc ^ static_cast<std::uint8_t>(ch)) & 0xFF] ^ (crc >> 8);
  }
  return ~crc;
}

std::uint32_t crc32(std::string_view data) { return crc32_update(0, data); }

Sha256::Sha256()
    : state_{0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c,
             0x1f83d9ab, 0x5be0cd19} {}

void Sha256::compress(const std::uint8_t* block) {
  std::array<std::uint32_t, 64> w{};
  for (int i = 0; i < 16; ++i) {
    w[i] = (std::uint32_t{block[4 * i]} << 24) | (std::uint32_t{block[4 * i + 1]} << 16) |
           (std::uint32_t{block[4 * i + 2]} << 8) | std::uint32_t{block[4 * i + 3]};
  }
  for (int i = 16; i < 64; ++i) {
    const std::uint32_t s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >> 3);
    const std::uint32_t s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >> 10);
    w[i] = w[i - 16] + s0 + w[i - 7] + s1;
  }
  auto [a, b, c, d, e, f, g, h] = state_;
  for (int i = 0; i < 64; ++i) {
    const std::uint32_t s1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
    const std::uint32_t ch = (e & f) ^ (~e & g);
    const std::uint32_t t1 = h + s1 + ch + kRoundConstants[i] + w[i];
    const std::uint32_t s0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
    const std::uint32_t maj = (a & b) ^ (a & c) ^ (b & c);
    const std::uint32_t t2 = s0 + maj;
    h = g;
    g = f;
    f = e;
    e = d + t1;
    d = c;
    c = b;
    b = a;
    a = t1 + t2;
  }
  state_[0] += a;
  state_[1] += b;
  state_[2] += c;
  state_[3] += d;
  state_[4] += e;
  state_[5] += f;
  state_[6] += g;
  state_[7] += h;
}

void Sha256::update(std::string_view data) {
  total_bytes_ += data.size();
  const auto* p = reinterpret_cast<const std::uint8_t*>(data.data());
  std::size_t n = data.size();
  if (buffered_ > 0) {
    const std::size_t take = std::min(n, buffer_.size() - buffered_);
    std::memcpy(buffer_.data() + buffered_, p, take);
    buffered_ += take;
    p += take;
    n -= take;
    if (buffered_ < buffer_.size()) return;
    compress(buffer_.data());
    buffered_ = 0;
  }
  for (; n >= 64; p += 64, n -= 64) compress(p);
  std::memcpy(buffer_.data(), p, n);
  buffered_ = n;
}

Sha256::Digest Sha256::finish() {
  const std::uint64_t bit_length = total_bytes_ * 8;
  // 0x80, zeros up to 56 mod 64, then the 64-bit big-endian message length.
  std::string pad(1, static_cast<char>(0x80));
  pad.append((buffered_ < 56 ? 55 - buffered_ : 119 - buffered_), '\0');
  for (int i = 7; i >= 0; --i) pad += static_cast<char>((bit_length >> (8 * i)) & 0xFF);
  update(pad);

  Digest digest{};
  for (int i = 0; i < 8; ++i) {
    for (int k = 0; k < 4; ++k) {
      digest[4 * i + k] = static_cast<std::uint8_t>(state_[i] >> (24 - 8 * k));
    }
  }
  return digest;
}

std::string Sha256::hex(const Digest& digest) {
  static constexpr char kHex[] = "0123456789abcdef";
  std::string out;
  out.reserve(64);
  for (const std::uint8_t byte : digest) {
    out += kHex[byte >> 4];
    out += kHex[byte & 0xF];
  }
  return out;
}

std::string sha256_hex(std::string_view data) {
  Sha256 h;
  h.update(data);
  return Sha256::hex(h.finish());
}

}  // namespace mf
