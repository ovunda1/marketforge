#pragma once

#include <cstdint>
#include <memory>
#include <string>
#include <utility>
#include <variant>
#include <vector>

namespace mf::bench {

// Just enough JSON to write results files. Objects keep insertion order, so output is
// stable and diffs of committed results stay readable.
class Json {
 public:
  using Array = std::vector<Json>;
  using Object = std::vector<std::pair<std::string, Json>>;

  Json() = default;  // null
  Json(bool v) : v_(v) {}
  Json(int v) : v_(std::int64_t{v}) {}
  Json(std::int64_t v) : v_(v) {}
  Json(std::uint64_t v) : v_(static_cast<std::int64_t>(v)) {}
  Json(double v) : v_(v) {}
  Json(const char* v) : v_(std::string(v)) {}
  Json(std::string v) : v_(std::move(v)) {}
  Json(Array v) : v_(std::move(v)) {}
  Json(Object v) : v_(std::move(v)) {}

  static Json array() { return Json(Array{}); }
  static Json object() { return Json(Object{}); }

  // Appends to an array, or sets a key of an object (replacing an existing one).
  Json& push(Json value);
  Json& set(const std::string& key, Json value);

  // Two-space indented; doubles in shortest round-trip form; non-finite doubles as null.
  std::string dump() const;

 private:
  void dump_to(std::string& out, int indent) const;

  std::variant<std::monostate, bool, std::int64_t, double, std::string, Array, Object> v_;
};

}  // namespace mf::bench
