include(FetchContent)

FetchContent_Declare(
  googletest
  URL https://github.com/google/googletest/archive/refs/tags/v1.15.2.tar.gz
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE
)
set(INSTALL_GTEST OFF CACHE BOOL "" FORCE)
set(gtest_force_shared_crt ON CACHE BOOL "" FORCE)
FetchContent_MakeAvailable(googletest)

add_executable(mf_engine_tests
  checksum_test.cpp
  clock_sequencer_test.cpp
  golden_test.cpp
  flat_hash_map_test.cpp
  journal_test.cpp
  object_pool_test.cpp
  order_book_test.cpp
  order_types_test.cpp
  price_levels_test.cpp
  replay_test.cpp
  risk_config_test.cpp
  risk_replay_test.cpp
  risk_test.cpp
  session_test.cpp
  snapshot_test.cpp
  test_placeholder.cpp
  text_format_test.cpp
)
target_link_libraries(mf_engine_tests PRIVATE mf::config GTest::gtest_main)  # brings mf::engine
target_compile_definitions(mf_engine_tests PRIVATE
  MF_GOLDEN_DIR="${CMAKE_CURRENT_SOURCE_DIR}/golden"
  MF_RISK_GOLDEN_DIR="${CMAKE_CURRENT_SOURCE_DIR}/golden/risk"
  MF_JOURNAL_FIXTURE_DIR="${CMAKE_CURRENT_SOURCE_DIR}/journal"
  MF_OPTION_CHECK_INVARIANTS=$<BOOL:${MF_CHECK_INVARIANTS}>
)

include(GoogleTest)
gtest_discover_tests(mf_engine_tests)
