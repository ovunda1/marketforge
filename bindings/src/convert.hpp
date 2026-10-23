#pragma once

// Conversions between Python values and engine values, and the builder for the
// immutable value classes (commands, events, limits). Glue only: no engine logic.
//
// Integers are checked, never wrapped: an int outside the C++ type's range raises
// OverflowError naming the field; a float, bool or str raises TypeError. pybind11's own
// int conversion would only say "incompatible function arguments".

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <concepts>
#include <cstdint>
#include <functional>
#include <limits>
#include <memory>
#include <optional>
#include <string>
#include <type_traits>
#include <utility>
#include <variant>
#include <vector>

#include "mf/commands.hpp"
#include "mf/types.hpp"

namespace mfpy {

namespace py = pybind11;

[[noreturn]] inline void raise(PyObject* type, const std::string& message) {
  PyErr_SetString(type, message.c_str());
  throw py::error_already_set();
}

inline std::string type_name(py::handle value) { return Py_TYPE(value.ptr())->tp_name; }

template <class T>
constexpr const char* int_type_name() {
  if constexpr (std::is_same_v<T, std::int64_t>) return "int64";
  else if constexpr (std::is_same_v<T, std::uint64_t>) return "uint64";
  else return std::is_signed_v<T> ? "a signed integer" : "an unsigned integer";
}

template <class T>
[[noreturn]] void raise_out_of_range(py::handle value, const char* field) {
  raise(PyExc_OverflowError, std::string(field) + "=" + py::repr(value).cast<std::string>() +
                                 " is out of range for " + int_type_name<T>());
}

template <class T>
T to_int(py::handle value, const char* field) {
  static_assert(std::is_integral_v<T> && !std::is_same_v<T, bool>);
  PyObject* o = value.ptr();
  if (PyBool_Check(o) || !PyLong_Check(o)) {
    raise(PyExc_TypeError, std::string(field) + " must be int, not " + type_name(value));
  }
  int overflow = 0;
  const long long v = PyLong_AsLongLongAndOverflow(o, &overflow);
  if (v == -1 && PyErr_Occurred()) throw py::error_already_set();
  if (overflow == 0) {
    if (!std::in_range<T>(v)) raise_out_of_range<T>(value, field);
    return static_cast<T>(v);
  }
  if (overflow > 0 && std::is_unsigned_v<T>) {
    const unsigned long long u = PyLong_AsUnsignedLongLong(o);
    if (PyErr_Occurred()) {
      PyErr_Clear();
      raise_out_of_range<T>(value, field);
    }
    if (!std::in_range<T>(u)) raise_out_of_range<T>(value, field);
    return static_cast<T>(u);
  }
  raise_out_of_range<T>(value, field);
}

inline bool to_bool(py::handle value, const char* field) {
  if (!PyBool_Check(value.ptr())) {
    raise(PyExc_TypeError, std::string(field) + " must be bool, not " + type_name(value));
  }
  return value.ptr() == Py_True;
}

// The Python class of a bound C++ type (class or native enum), looked up once.
template <class T>
py::handle python_type() {
  static const py::handle type = [] {
    // Kept for the interpreter's lifetime: one reference is never released.
    if constexpr (std::is_enum_v<T>) return py::type::of(py::cast(T{})).release();
    else return py::type::of<T>().release();
  }();
  return type;
}

template <class T>
T to_instance(py::handle value, const char* field) {
  const py::handle type = python_type<T>();
  if (!py::isinstance(value, type)) {
    raise(PyExc_TypeError, std::string(field) + " must be " +
                               type.attr("__name__").cast<std::string>() + ", not " +
                               type_name(value));
  }
  return value.cast<T>();
}

inline mf::Command to_command(py::handle value, const char* field) {
  try {
    return value.cast<mf::Command>();
  } catch (const py::cast_error&) {
    raise(PyExc_TypeError, std::string(field) + " must be a command, not " + type_name(value));
  }
}

template <class T>
struct is_optional : std::false_type {};
template <class T>
struct is_optional<std::optional<T>> : std::true_type {};

template <class M>
M convert(py::handle value, const char* field) {
  if constexpr (is_optional<M>::value) {
    if (value.is_none()) return std::nullopt;
    return convert<typename M::value_type>(value, field);
  } else if constexpr (std::is_same_v<M, bool>) {
    return to_bool(value, field);
  } else if constexpr (std::is_integral_v<M>) {
    return to_int<M>(value, field);
  } else if constexpr (std::is_same_v<M, mf::Command>) {
    return to_command(value, field);
  } else {
    return to_instance<M>(value, field);  // enums and bound classes
  }
}

// int128 (risk notionals) to a Python int, exactly.
inline py::object to_python(mf::Notional v) {
  const bool negative = v < 0;
  unsigned __int128 u = negative ? -static_cast<unsigned __int128>(v) : static_cast<unsigned __int128>(v);
  std::string digits;
  do {
    digits.insert(digits.begin(), static_cast<char>('0' + static_cast<int>(u % 10)));
    u /= 10;
  } while (u != 0);
  if (negative) digits.insert(digits.begin(), '-');
  return py::reinterpret_steal<py::object>(PyLong_FromString(digits.c_str(), nullptr, 10));
}

// --- Value classes ------------------------------------------------------------------
//
// An immutable Python class around a C++ value type: keyword-only constructor with
// checked conversions, read-only attributes that return copies, ==, hash and a
// dataclass-style repr. The fields are listed once and drive all of it.

enum class Arg : std::uint8_t { Required, Optional };

template <class T>
class ValueClass {
 public:
  ValueClass(py::handle scope, const char* name, const char* doc) : cls_(scope, name, doc) {}

  template <class M>
  ValueClass& field(const char* name, M T::*member, Arg arg = Arg::Required) {
    fields_->push_back({name, arg == Arg::Required,
                        [member](const T& t) { return py::cast(t.*member); },
                        [member, name](T& t, py::handle v) { t.*member = convert<M>(v, name); }});
    cls_.def_property_readonly(name, [member](const T& t) { return py::cast(t.*member); });
    return *this;
  }

  // A read-only attribute that is not a constructor argument.
  template <class F>
  ValueClass& computed(const char* name, F&& get) {
    getters_->push_back({name, false, std::function<py::object(const T&)>(get), {}});
    cls_.def_property_readonly(name, std::forward<F>(get));
    return *this;
  }

  template <class... Args>
  ValueClass& def(Args&&... args) {
    cls_.def(std::forward<Args>(args)...);
    return *this;
  }

  // Adds __init__ (unless result_only), __eq__, __hash__ and __repr__.
  py::class_<T>& finish(bool result_only = false) {
    auto fields = fields_;
    if (!result_only) {
      cls_.def(py::init([fields](const py::args& args, const py::kwargs& kwargs) {
        if (!args.empty()) raise(PyExc_TypeError, "arguments are keyword-only");
        T t{};
        std::vector<bool> seen(fields->size());
        for (const auto& [key, value] : kwargs) {
          const auto k = py::cast<std::string>(key);  // not key.cast<>: gcc parses a structured binding in a template as dependent
          std::size_t i = 0;
          while (i < fields->size() && k != (*fields)[i].name) ++i;
          if (i == fields->size()) raise(PyExc_TypeError, "unexpected keyword argument '" + k + "'");
          (*fields)[i].set(t, value);
          seen[i] = true;
        }
        for (std::size_t i = 0; i < fields->size(); ++i) {
          if ((*fields)[i].required && !seen[i]) {
            raise(PyExc_TypeError, std::string("missing required keyword argument '") +
                                       (*fields)[i].name + "'");
          }
        }
        return t;
      }));
    }
    auto shown = std::make_shared<std::vector<Field>>(*fields_);
    shown->insert(shown->end(), getters_->begin(), getters_->end());
    const auto values = [shown](const T& t) {
      py::tuple out(shown->size());
      for (std::size_t i = 0; i < shown->size(); ++i) out[i] = (*shown)[i].get(t);
      return out;
    };
    // A type without operator== (OrderView) compares by its attribute values.
    const auto equal = [values](const T& a, const T& b) -> bool {
      if constexpr (std::equality_comparable<T>) {
        return a == b;
      } else {
        return values(a).equal(values(b));
      }
    };
    cls_.def("__eq__", [equal](const T& a, py::handle b) -> py::object {
      if (!py::isinstance<T>(b)) return py::reinterpret_borrow<py::object>(Py_NotImplemented);
      return py::bool_(equal(a, b.cast<const T&>()));
    });
    cls_.def("__ne__", [equal](const T& a, py::handle b) -> py::object {
      if (!py::isinstance<T>(b)) return py::reinterpret_borrow<py::object>(Py_NotImplemented);
      return py::bool_(!equal(a, b.cast<const T&>()));
    });
    cls_.def("__hash__", [values](const T& t) { return py::hash(values(t)); });
    cls_.def("__repr__", [shown](py::handle self) {
      static const py::object enum_type = py::module_::import("enum").attr("Enum");
      std::string out = py::type::of(self).attr("__name__").cast<std::string>() + "(";
      for (std::size_t i = 0; i < shown->size(); ++i) {
        const py::object v = self.attr((*shown)[i].name);
        if (i != 0) out += ", ";
        out += (*shown)[i].name;
        out += "=";
        out += py::isinstance(v, enum_type) ? py::str(v).cast<std::string>()
                                            : py::repr(v).cast<std::string>();
      }
      return out + ")";
    });
    return cls_;
  }

  py::class_<T>& cls() { return cls_; }

 private:
  struct Field {
    const char* name;
    bool required;
    std::function<py::object(const T&)> get;
    std::function<void(T&, py::handle)> set;
  };

  py::class_<T> cls_;
  std::shared_ptr<std::vector<Field>> fields_ = std::make_shared<std::vector<Field>>();
  std::shared_ptr<std::vector<Field>> getters_ = std::make_shared<std::vector<Field>>();
};

}  // namespace mfpy
