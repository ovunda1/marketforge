// Counting replacements for the global allocation functions, linked into mf-bench-replay
// only. They report allocations per command, which the hot-path rule says should be
// zero once the book has warmed up.

#include "alloc_counter.hpp"

#include <algorithm>
#include <cstdlib>
#include <new>

namespace {

std::uint64_t g_allocations = 0;

void* allocate(std::size_t size) {
  ++g_allocations;
  if (void* p = std::malloc(size == 0 ? 1 : size)) return p;
  throw std::bad_alloc();
}

void* allocate_aligned(std::size_t size, std::align_val_t align) {
  ++g_allocations;
  void* p = nullptr;
  const std::size_t a = std::max(static_cast<std::size_t>(align), sizeof(void*));
  if (posix_memalign(&p, a, size == 0 ? 1 : size) != 0) throw std::bad_alloc();
  return p;
}

}  // namespace

namespace mf::bench {
std::uint64_t allocation_count() { return g_allocations; }
}  // namespace mf::bench

void* operator new(std::size_t size) { return allocate(size); }
void* operator new[](std::size_t size) { return allocate(size); }
void* operator new(std::size_t size, std::align_val_t a) { return allocate_aligned(size, a); }
void* operator new[](std::size_t size, std::align_val_t a) { return allocate_aligned(size, a); }
void* operator new(std::size_t size, const std::nothrow_t&) noexcept {
  ++g_allocations;
  return std::malloc(size == 0 ? 1 : size);
}
void* operator new[](std::size_t size, const std::nothrow_t&) noexcept {
  ++g_allocations;
  return std::malloc(size == 0 ? 1 : size);
}
void operator delete(void* p) noexcept { std::free(p); }
void operator delete[](void* p) noexcept { std::free(p); }
void operator delete(void* p, std::size_t) noexcept { std::free(p); }
void operator delete[](void* p, std::size_t) noexcept { std::free(p); }
void operator delete(void* p, std::align_val_t) noexcept { std::free(p); }
void operator delete[](void* p, std::align_val_t) noexcept { std::free(p); }
void operator delete(void* p, std::size_t, std::align_val_t) noexcept { std::free(p); }
void operator delete[](void* p, std::size_t, std::align_val_t) noexcept { std::free(p); }
