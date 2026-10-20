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

if(MF_BUILD_TESTS)
  enable_testing()
endif()

add_subdirectory(engine)

if(MF_BUILD_BINDINGS)
  add_subdirectory(bindings)
endif()
