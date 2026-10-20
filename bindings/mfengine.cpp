#include <pybind11/pybind11.h>

#include <string>

#include "mf/version.hpp"

namespace py = pybind11;

PYBIND11_MODULE(mfengine, m) {
  m.doc() = "MarketForge matching engine bindings (stub)";
  m.def("version", [] { return std::string(mf::version()); }, "Engine version string");
}
