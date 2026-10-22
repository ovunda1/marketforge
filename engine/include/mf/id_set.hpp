#pragma once

#include <bit>
#include <cstddef>
#include <cstdint>

#include "mf/flat_hash_map.hpp"
#include "mf/object_pool.hpp"
#include "mf/types.hpp"

namespace mf {

// Set of order ids as a paged bitmap: one 64-byte page of bits per 512 consecutive ids,
// found through a small page table. Ids are mostly sequential, so a new id's bit is in
// the same cache line as the previous id's, and the page table (one entry per 512 ids)
// stays in cache: a lookup is a probe of a small, hot table and one bit test, where a
// hash set of millions of ids misses the cache on almost every new id. Pages come from a
// pool; nothing is allocated per id.
//
// Memory: about one bit per id when ids are dense; a page per id at worst, when ids are
// at least 512 apart. Iteration order is unspecified: never iterate one to produce output.
class IdSet {
 public:
  IdSet() = default;
  IdSet(const IdSet&) = delete;
  IdSet& operator=(const IdSet&) = delete;

  bool contains(OrderId id) const {
    const Page* const* page = pages_.find(id >> kShift);
    return page != nullptr && ((*page)->words[word(id)] & bit(id)) != 0;
  }

  // Returns true if the id was not in the set.
  bool insert(OrderId id) {
    Page* const* found = pages_.find(id >> kShift);
    Page* page = found != nullptr ? *found : *pages_.try_emplace(id >> kShift, pool_.create()).first;
    std::uint64_t& w = page->words[word(id)];
    if ((w & bit(id)) != 0) return false;
    w |= bit(id);
    ++size_;
    return true;
  }

  void clear() {
    pages_.for_each([&](std::uint64_t, Page* page) { pool_.destroy(page); });
    pages_.clear();
    size_ = 0;
  }

  std::size_t size() const { return size_; }

  // Calls f(id) for every id, in unspecified order.
  template <class F>
  void for_each(F&& f) const {
    pages_.for_each([&](std::uint64_t page_no, const Page* page) {
      for (std::size_t i = 0; i < kWords; ++i) {
        for (std::uint64_t m = page->words[i]; m != 0; m &= m - 1) {
          f((page_no << kShift) | (i * 64) | static_cast<OrderId>(std::countr_zero(m)));
        }
      }
    });
  }

 private:
  static constexpr unsigned kShift = 9;  // 512 ids per page
  static constexpr std::size_t kWords = (std::size_t{1} << kShift) / 64;

  struct alignas(64) Page {
    std::uint64_t words[kWords]{};
  };

  static std::size_t word(OrderId id) { return (id >> 6) & (kWords - 1); }
  static std::uint64_t bit(OrderId id) { return std::uint64_t{1} << (id & 63); }

  FlatHashMap<std::uint64_t, Page*> pages_;
  ObjectPool<Page, 256> pool_;
  std::size_t size_{};
};

}  // namespace mf
