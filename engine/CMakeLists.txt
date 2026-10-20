add_library(mf_engine
  src/version.cpp
)
add_library(mf::engine ALIAS mf_engine)

target_include_directories(mf_engine PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_compile_features(mf_engine PUBLIC cxx_std_20)
target_compile_options(mf_engine PRIVATE
  $<$<CXX_COMPILER_ID:GNU,Clang,AppleClang>:-Wall -Wextra -Wpedantic>
)
# Position-independent so the library can be linked into the Python extension.
set_target_properties(mf_engine PROPERTIES POSITION_INDEPENDENT_CODE ON)

if(MF_BUILD_TESTS)
  add_subdirectory(tests)
endif()
