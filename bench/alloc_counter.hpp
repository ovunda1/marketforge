#pragma once

#include <cstdint>

namespace mf::bench {

// Heap allocations made by this process so far: mf-bench-replay replaces the global
// operator new to count them (alloc_counter.cpp). Single-threaded, like the engine.
std::uint64_t allocation_count();

}  // namespace mf::bench
