# Built only with -DMF_BUILD_BINDINGS=ON. Packaging via scikit-build-core is Milestone 7.
find_package(Python 3.12 REQUIRED COMPONENTS Interpreter Development.Module)

include(FetchContent)
FetchContent_Declare(
  pybind11
  URL https://github.com/pybind/pybind11/archive/refs/tags/v3.0.1.tar.gz
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE
)
FetchContent_MakeAvailable(pybind11)

pybind11_add_module(mfengine mfengine.cpp)
target_link_libraries(mfengine PRIVATE mf::engine)
