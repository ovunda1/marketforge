# Benchmark harness (M6). Timing code lives only here: the engine never reads a clock.
include(FetchContent)

FetchContent_Declare(
  googlebenchmark
  URL https://github.com/google/benchmark/archive/refs/tags/v1.9.4.tar.gz
  URL_HASH SHA256=b334658edd35efcf06a99d9be21e4e93e092bd5f95074c1673d5c8705d95c104
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE
)
set(BENCHMARK_ENABLE_TESTING OFF CACHE BOOL "" FORCE)
set(BENCHMARK_ENABLE_GTEST_TESTS OFF CACHE BOOL "" FORCE)
set(BENCHMARK_ENABLE_INSTALL OFF CACHE BOOL "" FORCE)
set(BENCHMARK_INSTALL_DOCS OFF CACHE BOOL "" FORCE)
set(BENCHMARK_ENABLE_WERROR OFF CACHE BOOL "" FORCE)

# HdrHistogram_c: only the histogram itself (hdr_histogram.c) is compiled here. Its own
# CMake project wants zlib for its log format and installs unconditionally, so its
# CMakeLists is skipped (SOURCE_SUBDIR names a directory that does not exist).
FetchContent_Declare(
  hdrhistogram
  URL https://github.com/HdrHistogram/HdrHistogram_c/archive/refs/tags/0.11.8.tar.gz
  URL_HASH SHA256=bb95351a6a8b242dc9be1f28562761a84d4cf0a874ffc90a9b630770a6468e94
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE
  SOURCE_SUBDIR no-cmake
)
FetchContent_MakeAvailable(googlebenchmark hdrhistogram)
enable_language(C)
add_library(mf_hdr_histogram STATIC ${hdrhistogram_SOURCE_DIR}/src/hdr_histogram.c)
target_include_directories(mf_hdr_histogram PUBLIC ${hdrhistogram_SOURCE_DIR}/include
                                            PRIVATE ${hdrhistogram_SOURCE_DIR}/src)

# Compiler and flags of this build, written into every results file.
string(TOUPPER "${CMAKE_BUILD_TYPE}" MF_BUILD_TYPE_UPPER)
set(MF_BENCH_CXX_FLAGS "${CMAKE_CXX_FLAGS} ${CMAKE_CXX_FLAGS_${MF_BUILD_TYPE_UPPER}} ${MF_NATIVE_FLAG}")
string(STRIP "${MF_BENCH_CXX_FLAGS}" MF_BENCH_CXX_FLAGS)
configure_file(build_info.hpp.in ${CMAKE_CURRENT_BINARY_DIR}/generated/mf_bench/build_info.hpp
               @ONLY)

set(MF_BENCH_WARNINGS $<$<CXX_COMPILER_ID:GNU,Clang,AppleClang>:-Wall -Wextra -Wpedantic>)

add_library(mf_bench
  lib/fixtures.cpp
  lib/histogram.cpp
  lib/json.cpp
  lib/platform.cpp
  lib/runner.cpp
  lib/stats.cpp
  lib/workload.cpp
)
target_include_directories(mf_bench PUBLIC
  ${CMAKE_CURRENT_SOURCE_DIR}/lib
  ${CMAKE_CURRENT_BINARY_DIR}/generated
)
target_link_libraries(mf_bench PUBLIC mf::engine PRIVATE mf_hdr_histogram)
target_compile_options(mf_bench PRIVATE ${MF_BENCH_WARNINGS})

# Microbenchmarks: add, cancel, replace, match (1 level, multi-level), risk check.
add_executable(mf-bench-micro micro.cpp)
target_link_libraries(mf-bench-micro PRIVATE mf_bench benchmark::benchmark)
target_compile_options(mf-bench-micro PRIVATE ${MF_BENCH_WARNINGS})

# End-to-end replay throughput and the per-command latency histogram.
add_executable(mf-bench-replay replay.cpp alloc_counter.cpp)
target_link_libraries(mf-bench-replay PRIVATE mf_bench)
target_compile_options(mf-bench-replay PRIVATE ${MF_BENCH_WARNINGS})

if(MF_BUILD_TESTS)
  add_executable(mf_bench_tests
    tests/fixtures_test.cpp
    tests/histogram_test.cpp
    tests/json_test.cpp
    tests/runner_test.cpp
    tests/stats_test.cpp
    tests/workload_test.cpp
  )
  target_link_libraries(mf_bench_tests PRIVATE mf_bench GTest::gtest_main)
  target_compile_options(mf_bench_tests PRIVATE ${MF_BENCH_WARNINGS})
  include(GoogleTest)
  gtest_discover_tests(mf_bench_tests DISCOVERY_MODE PRE_TEST)
endif()
