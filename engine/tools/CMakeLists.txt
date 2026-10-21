add_executable(mf_driver mf_driver.cpp)
target_link_libraries(mf_driver PRIVATE mf::engine)
target_compile_options(mf_driver PRIVATE
  $<$<CXX_COMPILER_ID:GNU,Clang,AppleClang>:-Wall -Wextra -Wpedantic>
)
