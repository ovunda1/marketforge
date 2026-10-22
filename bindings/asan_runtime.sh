#!/bin/sh
# Prints the ASan runtime shared library of the C++ compiler $1, for preloading into an
# uninstrumented Python that imports a sanitized mfengine (make sanitize, CI). The name
# differs by toolchain: Apple clang, clang's per-target and older layouts, and gcc.
set -eu
cxx=${1-c++}
# An empty argument is a failed lookup (make sanitize reads it from the CMake cache), not
# a request for the default compiler.
if [ -z "$cxx" ]; then
  echo "asan_runtime.sh: empty compiler argument" >&2
  exit 1
fi
for name in libclang_rt.asan_osx_dynamic.dylib libclang_rt.asan.so \
            "libclang_rt.asan-$(uname -m).so" libasan.so; do
  path=$("$cxx" -print-file-name="$name")
  if [ "$path" != "$name" ] && [ -e "$path" ]; then
    echo "$path"
    exit 0
  fi
done
echo "asan_runtime.sh: no ASan runtime found for $cxx" >&2
exit 1
