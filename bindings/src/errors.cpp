#include "errors.hpp"

#include <exception>

#include "mf/journal.hpp"
#include "mf/order_book.hpp"
#include "mf/risk_config.hpp"
#include "mf/snapshot.hpp"
#include "mf/text_format.hpp"

namespace mfpy {
namespace {

Errors g_errors;

py::handle make(py::module_& m, const char* name, const char* doc, py::handle base) {
  const std::string qualified = std::string("mfengine.") + name;
  PyObject* type = PyErr_NewExceptionWithDoc(qualified.c_str(), doc, base.ptr(), nullptr);
  if (type == nullptr) throw py::error_already_set();
  m.attr(name) = py::handle(type);  // the module holds a reference; so does g_errors
  return type;
}

}  // namespace

const Errors& errors() { return g_errors; }

[[noreturn]] void raise_with(py::handle type, const std::string& message, const py::dict& attrs) {
  py::object exc = type(message);
  for (const auto& [key, value] : attrs) exc.attr(key) = value;
  PyErr_SetObject(type.ptr(), exc.ptr());
  throw py::error_already_set();
}

void bind_errors(py::module_& m) {
  Errors& e = g_errors;
  e.base = make(m, "MarketForgeError", "Base class of every mfengine error.", PyExc_Exception);
  e.invariant = make(m, "InvariantViolation",
                     "A broken engine invariant or caller contract. Unless it is a "
                     "SequenceError, the engine may have changed state, and the Engine that "
                     "raised it is failed: every later call raises EngineFailed.",
                     e.base);
  e.sequence = make(m, "SequenceError",
                    "A seq_no that does not exceed the previous one, or a timestamp earlier "
                    "than the previous command's. Detected before any state changed: the "
                    "engine is unchanged and still usable.",
                    e.invariant);
  e.failed = make(m, "EngineFailed",
                  "The Engine failed earlier (an error after it began changing state) and "
                  "can no longer be used.",
                  e.base);
  e.journal = make(m, "JournalError",
                   "A journal cannot be opened, read or written, or is damaged; .kind says "
                   "how (JournalErrorKind).",
                   e.base);
  e.snapshot = make(m, "SnapshotError", "A snapshot cannot be read, written or decoded.", e.base);
  e.config = make(m, "ConfigError", "A risk config that does not parse or validate.", e.base);
  e.parse = make(m, "ParseError", "A malformed canonical command line.", e.base);

  py::register_exception_translator([](std::exception_ptr p) {
    if (!p) return;
    const Errors& e = g_errors;
    try {
      std::rethrow_exception(p);
    } catch (const mf::SequenceError& x) {
      PyErr_SetString(e.sequence.ptr(), x.what());
    } catch (const mf::InvariantViolation& x) {
      PyErr_SetString(e.invariant.ptr(), x.what());
    } catch (const mf::JournalError& x) {
      py::object exc = e.journal(x.what());
      exc.attr("kind") = py::cast(x.kind());
      PyErr_SetObject(e.journal.ptr(), exc.ptr());
    } catch (const mf::SnapshotError& x) {
      PyErr_SetString(e.snapshot.ptr(), x.what());
    } catch (const mf::ConfigError& x) {
      PyErr_SetString(e.config.ptr(), x.what());
    } catch (const mf::ParseError& x) {
      PyErr_SetString(e.parse.ptr(), x.what());
    }
  });
}

}  // namespace mfpy
