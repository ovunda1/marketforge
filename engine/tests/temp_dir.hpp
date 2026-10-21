#pragma once

#include <gtest/gtest.h>

#include <filesystem>
#include <fstream>
#include <iterator>
#include <string>

namespace mf::test {

// A fresh directory per test, removed afterwards. Named after the running test so
// parallel ctest processes never share one.
class TempDir {
 public:
  TempDir() {
    const auto* info = ::testing::UnitTest::GetInstance()->current_test_info();
    std::string name = std::string("mf-") + info->test_suite_name() + "-" + info->name();
    for (char& c : name) {
      if (c == '/') c = '_';
    }
    path_ = std::filesystem::temp_directory_path() / name;
    std::filesystem::remove_all(path_);
    std::filesystem::create_directories(path_);
  }
  ~TempDir() {
    std::error_code ignored;
    std::filesystem::remove_all(path_, ignored);
  }
  TempDir(const TempDir&) = delete;
  TempDir& operator=(const TempDir&) = delete;

  std::string file(const std::string& name) const { return (path_ / name).string(); }
  const std::filesystem::path& path() const { return path_; }

 private:
  std::filesystem::path path_;
};

inline std::string read_bytes(const std::string& path) {
  std::ifstream in(path, std::ios::binary);
  return {std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>()};
}

inline void write_bytes(const std::string& path, const std::string& bytes) {
  std::ofstream out(path, std::ios::binary | std::ios::trunc);
  out.write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
}

}  // namespace mf::test
