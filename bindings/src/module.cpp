// mfengine._core: Python bindings for the MarketForge engine (M7). The binding layer is
// thin: every call forwards to the engine library, and no matching or risk logic lives
// here. Values cross by copy; see docs/learning/m7.md.

#include <pybind11/pybind11.h>

#include <string>

#include "bindings.hpp"
#include "errors.hpp"
#include "mf/order_book.hpp"
#include "mf/version.hpp"

#ifndef MF_BUILD_TYPE
#define MF_BUILD_TYPE "unknown"
#endif

PYBIND11_MODULE(_core, m) {
  m.doc() = "MarketForge matching engine bindings";
  mfpy::bind_errors(m);
  mfpy::bind_values(m);  // enums first: later signatures use them as defaults
  mfpy::bind_engine(m);
  mfpy::bind_io(m);
  m.def("version", [] { return std::string(mf::version()); }, "Engine version string.");
  m.attr("INVARIANT_CHECKS") = mf::kInvariantChecksEnabled;
  m.attr("BUILD_TYPE") = MF_BUILD_TYPE;
}
