// is_plain_value (M7): an event is copied out to Python by value, which is only safe if
// it owns everything it holds. The trait accepts aggregates whose members are all
// arithmetic or enum; events.hpp static_asserts it for every event type. Array members
// are not supported (aggregate initialization cannot count them).

#include <gtest/gtest.h>

#include <memory>
#include <optional>
#include <string>
#include <variant>

#include "mf/events.hpp"
#include "mf/plain_value.hpp"

namespace mf::test {
namespace {

enum class Color : std::uint8_t { Red };

struct Scalars {
  std::int64_t a{};
  bool b{};
  Color c{};
  double d{};
};
struct Empty {};
struct WithPointer {
  std::int64_t a{};
  const char* p{};
};
struct WithReference {
  std::int64_t& r;
};
struct WithConstReference {
  const std::int64_t& r;
};
struct WithString {
  std::string s;
};
struct WithOptional {
  std::optional<std::int64_t> o;
};
struct WithUniquePtr {
  std::unique_ptr<int> p;
};
struct Inner {
  std::int64_t a{};
};
struct Nested {
  Inner inner;
};
class NotAggregate {
 public:
  explicit NotAggregate(int v) : v_(v) {}

 private:
  int v_;
};

TEST(PlainValue, AcceptsAggregatesOfScalars) {
  static_assert(is_plain_value_v<Scalars>);
  static_assert(is_plain_value_v<Empty>);
}

TEST(PlainValue, RejectsPointersAndReferences) {
  static_assert(!is_plain_value_v<WithPointer>);
  static_assert(!is_plain_value_v<WithReference>);
  static_assert(!is_plain_value_v<WithConstReference>);
  static_assert(!is_plain_value_v<WithUniquePtr>);
}

TEST(PlainValue, RejectsMembersThatAreNotScalars) {
  // Strict on purpose: a nested class could hide a pointer.
  static_assert(!is_plain_value_v<WithString>);
  static_assert(!is_plain_value_v<WithOptional>);
  static_assert(!is_plain_value_v<Nested>);
  static_assert(!is_plain_value_v<NotAggregate>);
  static_assert(!is_plain_value_v<int>);
}

template <class... Ts>
constexpr bool all_plain(std::variant<Ts...>*) {
  return (is_plain_value_v<Ts> && ...);
}

TEST(PlainValue, EveryEventTypeIsAPlainValue) {
  static_assert(all_plain(static_cast<Event*>(nullptr)));
  static_assert(field_count_v<Fill> == 10);
  static_assert(field_count_v<Cancelled> == 4);
}

}  // namespace
}  // namespace mf::test
