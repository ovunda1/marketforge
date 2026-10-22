# mfengine._core, the pybind11 extension (M7). Built by scikit-build-core (pip install,
# see pyproject.toml), or directly with -DMF_BUILD_BINDINGS=ON.
find_package(Python 3.12 REQUIRED COMPONENTS Interpreter Development.Module)

include(FetchContent)
FetchContent_Declare(
  pybind11
  URL https://github.com/pybind/pybind11/archive/refs/tags/v3.0.1.tar.gz
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE
)
FetchContent_MakeAvailable(pybind11)

pybind11_add_module(_core
  src/engine.cpp
  src/errors.cpp
  src/io.cpp
  src/module.cpp
  src/values.cpp
)
target_link_libraries(_core PRIVATE mf::config)  # brings mf::engine
target_compile_definitions(_core PRIVATE MF_BUILD_TYPE="${CMAKE_BUILD_TYPE}")
target_compile_options(_core PRIVATE
  $<$<CXX_COMPILER_ID:GNU,Clang,AppleClang>:-Wall -Wextra -Wpedantic>
)

# The package's Python files next to the extension, so a plain CMake build directory
# is importable too (PYTHONPATH=<build>/bindings/python), e.g. the sanitizer build.
set(MF_PY_PACKAGE ${CMAKE_CURRENT_BINARY_DIR}/python/mfengine)
set_target_properties(_core PROPERTIES LIBRARY_OUTPUT_DIRECTORY ${MF_PY_PACKAGE})
foreach(f __init__.py _core.pyi py.typed)
  configure_file(mfengine/${f} ${MF_PY_PACKAGE}/${f} COPYONLY)
endforeach()

install(TARGETS _core LIBRARY DESTINATION mfengine COMPONENT mfengine)
