#!/bin/sh
# Development build of kicad-cli + the eeschema kiface from a simee/<version> checkout on macOS,
# against Homebrew's libraries. For testing patches only: release bundles are built differently.
#
#   dev/build-macos-homebrew.sh <kicad checkout> <build dir>
#   KICAD_CLI=<build dir>/kicad/KiCad.app/Contents/MacOS/kicad-cli uv run pytest tests/test_sch_import.py
#
# Needs: brew install wxwidgets glm nng unixodbc ninja swig boost protobuf opencascade libngspice \
#        libgit2 cairo pixman harfbuzz fontconfig freetype zstd curl python@3.13 icu4c@78
# Uses Xcode's clang directly: the /usr/bin/c++ shim puts /usr/local/include ahead of every
# -isystem dir, so on a Mac with an old x86_64 Homebrew there, its protobuf/boost headers win.
set -eu
src=$1 build=$2
tc=$(xcode-select -p)/Toolchains/XcodeDefault.xctoolchain/usr/bin
brew=$(brew --prefix)
py=$brew/opt/python@3.13/Frameworks/Python.framework
cmake -S "$src" -B "$build" -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER="$tc/clang" -DCMAKE_CXX_COMPILER="$tc/clang++" -DCMAKE_OBJCXX_COMPILER="$tc/clang++" \
  -DCMAKE_PREFIX_PATH="$brew;$brew/opt/icu4c@78" \
  -DKICAD_BUILD_QA_TESTS=OFF -DKICAD_SCRIPTING_WXPYTHON=OFF -DKICAD_BUILD_I18N=OFF -DKICAD_USE_SENTRY=OFF \
  -DKICAD_UPDATE_CHECK=OFF -DKICAD_USE_PCH=OFF -DwxWidgets_CONFIG_EXECUTABLE="$brew/bin/wx-config" \
  -DOCC_INCLUDE_DIR="$brew/opt/opencascade/include/opencascade" -DOCC_LIBRARY_DIR="$brew/opt/opencascade/lib" \
  -DNGSPICE_INCLUDE_DIR="$brew/opt/libngspice/include" -DNGSPICE_LIBRARY="$brew/opt/libngspice/lib/libngspice.dylib" \
  -DPYTHON_EXECUTABLE="$py/Versions/3.13/bin/python3.13" -DPYTHON_LIBRARY="$py/Versions/3.13/lib/libpython3.13.dylib" \
  -DPYTHON_INCLUDE_DIR="$py/Versions/3.13/include/python3.13" -DPYTHON_FRAMEWORK="$py"
ninja -C "$build" kicad-cli eeschema_kiface
