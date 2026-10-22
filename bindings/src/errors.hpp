#pragma once

// Python exception classes and the translation of engine exceptions into them.
//
//   MarketForgeError
//   ├── InvariantViolation       mf::InvariantViolation (the engine may have changed)
//   │   └── SequenceError        mf::SequenceError (found before any change: usable)
//   ├── EngineFailed             any call on an Engine after a failure
//   ├── JournalError (.kind)     mf::JournalError
//   ├── SnapshotError            mf::SnapshotError
//   ├── ConfigError              mf::ConfigError
//   └── ParseError               mf::ParseError

#include <pybind11/pybind11.h>

#include <string>

namespace mfpy {

namespace py = pybind11;

struct Errors {
  py::handle base;
  py::handle invariant;
  py::handle sequence;
  py::handle failed;
  py::handle journal;
  py::handle snapshot;
  py::handle config;
  py::handle parse;
};

// Valid after bind_errors(); the classes live as long as the interpreter.
const Errors& errors();

void bind_errors(py::module_& m);

// Raises `type(message)` with extra attributes set on the instance.
[[noreturn]] void raise_with(py::handle type, const std::string& message, const py::dict& attrs);

}  // namespace mfpy
