#pragma once

#include <cstddef>
#include <type_traits>
#include <utility>

namespace mf {

// is_plain_value_v<T>: T is an aggregate whose every member is arithmetic or an enum, so
// a copy of it owns everything it holds (no pointer, reference or class member that
// could point back into the engine). Events are checked with it (events.hpp) because
// the Python bindings hand out copies of them that outlive the engine's buffers.
//
// C++20 has no reflection: the members are counted by aggregate initialization from a
// value convertible to anything, then named with a structured binding, whose
// declared types are checked. Array members are not supported.

namespace detail {

struct AnyField {
  template <class T>
  operator T() const noexcept;  // declaration only: used in unevaluated contexts
};

template <class T, std::size_t... I>
constexpr bool brace_constructible(std::index_sequence<I...>) {
  return requires { T{(void(I), AnyField{})...}; };
}

template <class T, std::size_t N = 0>
constexpr std::size_t count_fields() {
  if constexpr (N < 32 && brace_constructible<T>(std::make_index_sequence<N + 1>{})) {
    return count_fields<T, N + 1>();
  } else {
    return N;
  }
}

template <class... Ts>
struct TypeList {
  static constexpr bool all_scalar = ((std::is_arithmetic_v<Ts> || std::is_enum_v<Ts>) && ...);
};

template <class... Ts>
constexpr TypeList<Ts...> member_types_of(Ts&...) {
  return {};
}

// The type of each member, from a structured binding of `t`. Only the return type is
// used.
#define MF_PV_BIND(n, ...)                           \
  if constexpr (N == n) {                            \
    auto& [__VA_ARGS__] = t;                         \
    return mf::detail::member_types_of(__VA_ARGS__); \
  } else

template <std::size_t N, class T>
auto member_types(T& t) {
  MF_PV_BIND(1, a)
  MF_PV_BIND(2, a, b)
  MF_PV_BIND(3, a, b, c)
  MF_PV_BIND(4, a, b, c, d)
  MF_PV_BIND(5, a, b, c, d, e)
  MF_PV_BIND(6, a, b, c, d, e, f)
  MF_PV_BIND(7, a, b, c, d, e, f, g)
  MF_PV_BIND(8, a, b, c, d, e, f, g, h)
  MF_PV_BIND(9, a, b, c, d, e, f, g, h, i)
  MF_PV_BIND(10, a, b, c, d, e, f, g, h, i, j)
  MF_PV_BIND(11, a, b, c, d, e, f, g, h, i, j, k)
  MF_PV_BIND(12, a, b, c, d, e, f, g, h, i, j, k, l)
  { return TypeList<void>{}; }  // more members than supported: not plain
}
#undef MF_PV_BIND

template <class T>
constexpr bool plain_members() {
  constexpr std::size_t n = count_fields<T>();
  if constexpr (n == 0) {
    return std::is_empty_v<T>;
  } else {
    // Each binding is an lvalue of its member's type (a reference member shows as the
    // type it refers to, which is why is_plain_value rules references out first).
    return decltype(member_types<n>(std::declval<T&>()))::all_scalar;
  }
}

template <class T>
constexpr bool is_plain_value() {
  // Reference and const members make copy assignment deleted; owning members
  // (std::string, std::unique_ptr) make it non-trivial. Checked first so the member
  // walk only sees types it can bind.
  if constexpr (!std::is_class_v<T> || !std::is_aggregate_v<T> || std::is_polymorphic_v<T> ||
                !std::is_trivially_copyable_v<T> || !std::is_copy_assignable_v<T>) {
    return false;
  } else {
    return plain_members<T>();
  }
}

}  // namespace detail

template <class T>
inline constexpr bool is_plain_value_v = detail::is_plain_value<T>();

template <class T>
inline constexpr std::size_t field_count_v = detail::count_fields<T>();

}  // namespace mf
