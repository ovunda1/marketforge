#pragma once

#include <cstddef>
#include <memory>
#include <new>
#include <type_traits>
#include <utility>
#include <vector>

#if defined(__SANITIZE_ADDRESS__)
#define MF_POOL_POISONING 1
#elif defined(__has_feature)
#if __has_feature(address_sanitizer)
#define MF_POOL_POISONING 1
#endif
#endif
#ifdef MF_POOL_POISONING
#include <sanitizer/asan_interface.h>
#endif

namespace mf {

// True when freed pool slots are poisoned (AddressSanitizer builds): touching a destroyed
// object is reported as a use-after-poison, as heap memory would be after delete.
#ifdef MF_POOL_POISONING
inline constexpr bool kPoolPoisoning = true;
#else
inline constexpr bool kPoolPoisoning = false;
#endif

// Fixed-size object pool: objects live in chunks of kChunk slots that are never freed or
// moved, so a pointer stays valid until its object is destroyed. create() reuses the most
// recently freed slot and allocates only when every slot is in use; with reserve(), or
// once the pool has reached its peak, creating and destroying allocate nothing.
//
// Single-threaded, like the book that owns it. T must be trivially destructible: the
// pool does not track which slots are live, so it cannot run destructors at the end.
template <class T, std::size_t kChunk = 4096>
class ObjectPool {
  static_assert(std::is_trivially_destructible_v<T>);
  static_assert(kChunk > 0);

 public:
  ObjectPool() = default;
  ObjectPool(const ObjectPool&) = delete;
  ObjectPool& operator=(const ObjectPool&) = delete;

  template <class... Args>
  T* create(Args&&... args) {
    if (free_.empty()) grow();
    void* slot = free_.back();
    free_.pop_back();
#ifdef MF_POOL_POISONING
    ASAN_UNPOISON_MEMORY_REGION(slot, sizeof(T));
#endif
    ++live_;
    return ::new (slot) T(std::forward<Args>(args)...);
  }

  void destroy(T* object) {
    std::destroy_at(object);
#ifdef MF_POOL_POISONING
    ASAN_POISON_MEMORY_REGION(object, sizeof(T));
#endif
    free_.push_back(object);  // never reallocates: capacity covers every slot
    --live_;
  }

  // Makes room for at least n live objects.
  void reserve(std::size_t n) {
    while (capacity() < n) grow();
  }

  std::size_t live() const { return live_; }
  std::size_t capacity() const { return chunks_.size() * kChunk; }

 private:
  struct alignas(T) Slot {
    std::byte bytes[sizeof(T)];
  };

  void grow() {
    auto chunk = std::make_unique<Slot[]>(kChunk);
#ifdef MF_POOL_POISONING
    ASAN_POISON_MEMORY_REGION(chunk.get(), sizeof(Slot) * kChunk);
#endif
    free_.reserve(capacity() + kChunk);
    // Lowest address on top, so a fresh pool hands out slots in address order.
    for (std::size_t i = kChunk; i > 0; --i) free_.push_back(&chunk[i - 1]);
    chunks_.push_back(std::move(chunk));
  }

  std::vector<std::unique_ptr<Slot[]>> chunks_;
  std::vector<void*> free_;
  std::size_t live_{};
};

}  // namespace mf
