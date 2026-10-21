cmake_minimum_required(VERSION 3.24)

project(marketforge VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE RelWithDebInfo CACHE STRING "Build type" FORCE)
endif()

option(MF_BUILD_TESTS "Build GoogleTest unit tests" ON)
option(MF_BUILD_BINDINGS "Build the pybind11 mfengine module" OFF)
# O(N) book consistency walk after every command. On by default for correctness;
# benchmark builds (M6) turn it off. The seq_no check is always on.
option(MF_CHECK_INVARIANTS "Check order book invariants after every command" ON)
# Benchmarks (M6): Google Benchmark, HdrHistogram and the bench/ harness. Off by default;
# `make bench` builds them in build-bench/ (Release, invariant walk off).
option(MF_BUILD_BENCH "Build the benchmark harness in bench/" OFF)
# AddressSanitizer + UndefinedBehaviorSanitizer for everything built here, including
# third-party code, so a bad access anywhere is reported. Any finding aborts.
option(MF_SANITIZE "Build with ASan and UBSan" OFF)
if(MF_SANITIZE)
  add_compile_options(-fsanitize=address,undefined -fno-sanitize-recover=all
                      -fno-omit-frame-pointer)
  add_link_options(-fsanitize=address,undefined)
endif()

if(MF_BUILD_TESTS)
  enable_testing()
endif()

add_subdirectory(engine)

if(MF_BUILD_BENCH)
  add_subdirectory(bench)
endif()

if(MF_BUILD_BINDINGS)
  add_subdirectory(bindings)
endif()
