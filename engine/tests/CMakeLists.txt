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
  test_placeholder.cpp
)
target_link_libraries(mf_engine_tests PRIVATE mf::engine GTest::gtest_main)

include(GoogleTest)
gtest_discover_tests(mf_engine_tests)
