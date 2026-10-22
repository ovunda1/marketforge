#pragma once

#include <cstddef>
#include <cstdint>
#include <type_traits>
#include <utility>
#include <vector>

namespace mf {

// Open-addressing hash map for unsigned integer keys (order ids): one flat array of
// (key, value) slots, linear probing, power-of-two capacity, at most half full, and
// backward-shift deletion (no tombstones, so probe runs never degrade). A lookup is a
// hash and a short scan of adjacent slots, usually within one cache line; nothing is
// allocated except when the table doubles.
//
// The all-ones key marks an empty slot; it is still a valid key, kept outside the table.
//
// Pointers to values are invalidated by any insert (the table may grow) and any erase
// (later entries shift back). Iteration order is unspecified: never iterate one to
// produce output.
template <class Key, class Value>
class FlatHashMap {
  static_assert(std::is_unsigned_v<Key>);

 public:
  std::size_t size() const { return size_ + (has_max_ ? 1 : 0); }
  bool empty() const { return size() == 0; }
  std::size_t capacity() const { return slots_.size(); }

  Value* find(Key key) {
    return const_cast<Value*>(static_cast<const FlatHashMap*>(this)->find(key));
  }
  const Value* find(Key key) const {
    if (key == kEmpty) return has_max_ ? &max_value_ : nullptr;
    if (slots_.empty()) return nullptr;
    for (std::size_t i = index_of(key);; i = (i + 1) & mask_) {
      const Slot& s = slots_[i];
      if (s.key == key) return &s.value;
      if (s.key == kEmpty) return nullptr;
    }
  }
  bool contains(Key key) const { return find(key) != nullptr; }

  // Inserts `value` if `key` is absent. Returns the key's value and whether it was inserted.
  std::pair<Value*, bool> try_emplace(Key key, Value value) {
    if (key == kEmpty) {
      if (has_max_) return {&max_value_, false};
      has_max_ = true;
      max_value_ = std::move(value);
      return {&max_value_, true};
    }
    if ((size_ + 1) * 2 > slots_.size()) rehash(slots_.empty() ? 16 : slots_.size() * 2);
    std::size_t i = index_of(key);
    for (; slots_[i].key != kEmpty; i = (i + 1) & mask_) {
      if (slots_[i].key == key) return {&slots_[i].value, false};
    }
    slots_[i].key = key;
    slots_[i].value = std::move(value);
    ++size_;
    return {&slots_[i].value, true};
  }

  bool erase(Key key) {
    if (key == kEmpty) {
      const bool had = has_max_;
      has_max_ = false;
      max_value_ = Value{};
      return had;
    }
    if (slots_.empty()) return false;
    std::size_t hole = index_of(key);
    for (;; hole = (hole + 1) & mask_) {
      if (slots_[hole].key == key) break;
      if (slots_[hole].key == kEmpty) return false;
    }
    // Backward shift: move each later entry of the run into the hole if the hole lies
    // on its probe path (between its home slot and where it sits).
    for (std::size_t j = (hole + 1) & mask_; slots_[j].key != kEmpty; j = (j + 1) & mask_) {
      const std::size_t home = index_of(slots_[j].key);
      if (((j - home) & mask_) >= ((j - hole) & mask_)) {
        slots_[hole] = std::move(slots_[j]);
        hole = j;
      }
    }
    slots_[hole] = Slot{};
    --size_;
    return true;
  }

  void clear() {
    for (Slot& s : slots_) s = Slot{};
    size_ = 0;
    has_max_ = false;
    max_value_ = Value{};
  }

  // Makes room for n entries without growing.
  void reserve(std::size_t n) {
    std::size_t capacity = 16;
    while (capacity < n * 2) capacity *= 2;
    if (capacity > slots_.size()) rehash(capacity);
  }

  // Calls f(key, value) for every entry, in unspecified order.
  template <class F>
  void for_each(F&& f) const {
    for (const Slot& s : slots_) {
      if (s.key != kEmpty) f(s.key, s.value);
    }
    if (has_max_) f(kEmpty, max_value_);
  }

 private:
  static constexpr Key kEmpty = static_cast<Key>(~Key{0});

  struct Slot {
    Key key{kEmpty};
    [[no_unique_address]] Value value{};
  };

  // Order ids are mostly sequential: mix every bit into the low ones (murmur3 fmix64).
  std::size_t index_of(Key key) const {
    std::uint64_t h = key;
    h ^= h >> 33;
    h *= 0xff51afd7ed558ccdULL;
    h ^= h >> 33;
    h *= 0xc4ceb9fe1a85ec53ULL;
    h ^= h >> 33;
    return static_cast<std::size_t>(h) & mask_;
  }

  void rehash(std::size_t capacity) {
    std::vector<Slot> old = std::move(slots_);
    slots_.assign(capacity, Slot{});
    mask_ = capacity - 1;
    for (Slot& s : old) {
      if (s.key == kEmpty) continue;
      std::size_t i = index_of(s.key);
      while (slots_[i].key != kEmpty) i = (i + 1) & mask_;
      slots_[i] = std::move(s);
    }
  }

  std::vector<Slot> slots_;
  std::size_t mask_{};
  std::size_t size_{};  // entries in slots_
  bool has_max_{};
  Value max_value_{};
};

}  // namespace mf
