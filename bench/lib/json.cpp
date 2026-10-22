#include "json.hpp"

#include <charconv>
#include <cmath>
#include <cstdio>
#include <stdexcept>
#include <string_view>

namespace mf::bench {

namespace {

void append_string(std::string& out, std::string_view s) {
  out += '"';
  for (const char c : s) {
    switch (c) {
      case '"': out += "\\\""; break;
      case '\\': out += "\\\\"; break;
      case '\n': out += "\\n"; break;
      case '\t': out += "\\t"; break;
      case '\r': out += "\\r"; break;
      default:
        if (static_cast<unsigned char>(c) < 0x20) {
          char buf[8];
          std::snprintf(buf, sizeof buf, "\\u%04x", static_cast<unsigned char>(c));
          out += buf;
        } else {
          out += c;
        }
    }
  }
  out += '"';
}

void append_double(std::string& out, double v) {
  if (!std::isfinite(v)) {
    out += "null";
    return;
  }
  char buf[32];
  const auto [end, ec] = std::to_chars(buf, buf + sizeof buf, v);
  std::string_view text(buf, static_cast<std::size_t>(end - buf));
  out += text;
  // Keep a double recognisably a double: 2.0, not 2.
  if (text.find_first_of(".e") == std::string_view::npos) out += ".0";
}

}  // namespace

Json& Json::push(Json value) {
  auto* a = std::get_if<Array>(&v_);
  if (a == nullptr) throw std::logic_error("Json::push on a non-array");
  a->push_back(std::move(value));
  return *this;
}

Json& Json::set(const std::string& key, Json value) {
  auto* o = std::get_if<Object>(&v_);
  if (o == nullptr) throw std::logic_error("Json::set on a non-object");
  for (auto& [k, v] : *o) {
    if (k == key) {
      v = std::move(value);
      return *this;
    }
  }
  o->emplace_back(key, std::move(value));
  return *this;
}

std::string Json::dump() const {
  std::string out;
  dump_to(out, 0);
  out += '\n';
  return out;
}

void Json::dump_to(std::string& out, int indent) const {
  const auto newline = [&](int level) {
    out += '\n';
    out.append(static_cast<std::size_t>(level) * 2, ' ');
  };
  std::visit(
      [&](const auto& v) {
        using T = std::decay_t<decltype(v)>;
        if constexpr (std::is_same_v<T, std::monostate>) {
          out += "null";
        } else if constexpr (std::is_same_v<T, bool>) {
          out += v ? "true" : "false";
        } else if constexpr (std::is_same_v<T, std::int64_t>) {
          out += std::to_string(v);
        } else if constexpr (std::is_same_v<T, double>) {
          append_double(out, v);
        } else if constexpr (std::is_same_v<T, std::string>) {
          append_string(out, v);
        } else if constexpr (std::is_same_v<T, Array>) {
          if (v.empty()) {
            out += "[]";
            return;
          }
          out += '[';
          for (std::size_t i = 0; i < v.size(); ++i) {
            if (i > 0) out += ',';
            newline(indent + 1);
            v[i].dump_to(out, indent + 1);
          }
          newline(indent);
          out += ']';
        } else {
          if (v.empty()) {
            out += "{}";
            return;
          }
          out += '{';
          for (std::size_t i = 0; i < v.size(); ++i) {
            if (i > 0) out += ',';
            newline(indent + 1);
            append_string(out, v[i].first);
            out += ": ";
            v[i].second.dump_to(out, indent + 1);
          }
          newline(indent);
          out += '}';
        }
      },
      v_);
}

}  // namespace mf::bench
