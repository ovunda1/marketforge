#include "json.hpp"

#include <gtest/gtest.h>

#include <limits>

namespace mf::bench {
namespace {

TEST(Json, ObjectsKeepInsertionOrderAndIndent) {
  Json j = Json::object();
  j.set("b", 1);
  j.set("a", Json::array().push("x").push(true).push(Json()));
  j.set("c", Json::object());
  EXPECT_EQ(j.dump(),
            "{\n"
            "  \"b\": 1,\n"
            "  \"a\": [\n"
            "    \"x\",\n"
            "    true,\n"
            "    null\n"
            "  ],\n"
            "  \"c\": {}\n"
            "}\n");
}

TEST(Json, SetReplacesAnExistingKey) {
  Json j = Json::object();
  j.set("k", 1);
  j.set("k", 2);
  EXPECT_EQ(j.dump(), "{\n  \"k\": 2\n}\n");
}

TEST(Json, Scalars) {
  EXPECT_EQ(Json(0.1).dump(), "0.1\n");
  EXPECT_EQ(Json(2.0).dump(), "2.0\n");
  EXPECT_EQ(Json(std::int64_t{-9'000'000'000}).dump(), "-9000000000\n");
  EXPECT_EQ(Json(std::numeric_limits<double>::quiet_NaN()).dump(), "null\n");
  EXPECT_EQ(Json(std::numeric_limits<double>::infinity()).dump(), "null\n");
  EXPECT_EQ(Json(false).dump(), "false\n");
}

TEST(Json, EscapesStrings) {
  EXPECT_EQ(Json("a\"b\\c\nd\te\x01").dump(), "\"a\\\"b\\\\c\\nd\\te\\u0001\"\n");
}

}  // namespace
}  // namespace mf::bench
