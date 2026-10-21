add_library(mf_engine
  src/checksum.cpp
  src/clock.cpp
  src/events.cpp
  src/journal.cpp
  src/order_book.cpp
  src/engine.cpp
  src/replay.cpp
  src/risk.cpp
  src/sequencer.cpp
  src/snapshot.cpp
  src/text_format.cpp
  src/version.cpp
)
add_library(mf::engine ALIAS mf_engine)

target_include_directories(mf_engine PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_compile_features(mf_engine PUBLIC cxx_std_20)
# PUBLIC so tests see the same kInvariantChecksEnabled value the library was built with.
if(MF_CHECK_INVARIANTS)
  target_compile_definitions(mf_engine PUBLIC MF_CHECK_INVARIANTS)
endif()
target_compile_options(mf_engine PRIVATE
  $<$<CXX_COMPILER_ID:GNU,Clang,AppleClang>:-Wall -Wextra -Wpedantic>
)
# Position-independent so the library can be linked into the Python extension.
set_target_properties(mf_engine PROPERTIES POSITION_INDEPENDENT_CODE ON)

# Risk limits from YAML (M5). A separate library so the engine core has no YAML
# dependency: the config becomes admin commands before it reaches the engine.
include(FetchContent)
FetchContent_Declare(
  yaml-cpp
  URL https://github.com/jbeder/yaml-cpp/archive/refs/tags/yaml-cpp-0.9.0.tar.gz
  URL_HASH SHA256=25cb043240f828a8c51beb830569634bc7ac603978e0f69d6b63558dadefd49a
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE
)
set(YAML_CPP_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(YAML_CPP_BUILD_TOOLS OFF CACHE BOOL "" FORCE)
set(YAML_CPP_BUILD_CONTRIB OFF CACHE BOOL "" FORCE)
set(YAML_CPP_INSTALL OFF CACHE BOOL "" FORCE)
set(YAML_CPP_FORMAT_SOURCE OFF CACHE BOOL "" FORCE)
set(YAML_BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
FetchContent_MakeAvailable(yaml-cpp)

add_library(mf_config src/risk_config.cpp)
add_library(mf::config ALIAS mf_config)
target_link_libraries(mf_config PUBLIC mf::engine PRIVATE yaml-cpp::yaml-cpp)
target_compile_options(mf_config PRIVATE
  $<$<CXX_COMPILER_ID:GNU,Clang,AppleClang>:-Wall -Wextra -Wpedantic>
)

# Differential-test driver; always built (it is small and the Python tests use it).
add_subdirectory(tools)

if(MF_BUILD_TESTS)
  add_subdirectory(tests)
endif()
