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
# Benchmark builds (M6) also compile for the host CPU (-mcpu=native / -march=native) and
# link with LTO, unless told otherwise. Never on by default elsewhere: a native binary
# may not run on another machine, and CI builds must be portable.
if(NOT DEFINED MF_NATIVE)
  set(MF_NATIVE ${MF_BUILD_BENCH})
endif()
if(NOT DEFINED MF_LTO)
  set(MF_LTO ${MF_BUILD_BENCH})
endif()
option(MF_NATIVE "Compile for the host CPU" ${MF_NATIVE})
option(MF_LTO "Link-time optimization" ${MF_LTO})
if(MF_NATIVE)
  include(CheckCXXCompilerFlag)
  check_cxx_compiler_flag(-mcpu=native MF_HAS_MCPU_NATIVE)
  check_cxx_compiler_flag(-march=native MF_HAS_MARCH_NATIVE)
  if(MF_HAS_MCPU_NATIVE AND CMAKE_SYSTEM_PROCESSOR MATCHES "arm64|aarch64")
    add_compile_options(-mcpu=native)
    set(MF_NATIVE_FLAG -mcpu=native)
  elseif(MF_HAS_MARCH_NATIVE)
    add_compile_options(-march=native)
    set(MF_NATIVE_FLAG -march=native)
  endif()
endif()
if(MF_LTO)
  include(CheckIPOSupported)
  check_ipo_supported(RESULT MF_IPO_OK OUTPUT MF_IPO_ERROR LANGUAGES CXX)
  if(MF_IPO_OK)
    set(CMAKE_INTERPROCEDURAL_OPTIMIZATION ON)
  else()
    message(WARNING "LTO not supported: ${MF_IPO_ERROR}")
  endif()
endif()
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

# The Python extension (M7) links the static engine, config and yaml-cpp libraries into
# a shared object, so everything must be position-independent (the default on macOS,
# not on Linux).
if(MF_BUILD_BINDINGS)
  set(CMAKE_POSITION_INDEPENDENT_CODE ON)
endif()

add_subdirectory(engine)

if(MF_BUILD_BENCH)
  add_subdirectory(bench)
endif()

if(MF_BUILD_BINDINGS)
  add_subdirectory(bindings)
endif()
