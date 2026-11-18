import io
import os
import tarfile
import zipfile
from pathlib import Path

import pytest
import wasmtime

import tiny_macho
from tiny_elf import make_elf
from wasm_toolchain import check, cli, components, package, publish
from wasm_toolchain.components import BINARIES, COMPONENTS, VERSION

TOP = "wasi-sdk-34.0-x86_64-linux"
EH = "share/wasi-sysroot/lib/wasm32-wasip1/eh"
# A few files of each kind in wasi-sdk's release, and whether a model build keeps them whatever the programs load.
SDK_FILES = {
    "VERSION": True,
    "bin/clang-23": True, "bin/clang": True, "bin/clang++": True, "bin/lld": True, "bin/wasm-ld": True,
    "bin/clang.cfg": True, "bin/clang++.cfg": True,
    "bin/clang.exe": True, "bin/clang++.exe": True, "bin/lld.exe": True, "bin/wasm-ld.exe": True,
    "bin/clang-cl": False, "bin/clang-cpp": False, "bin/ld.lld": False, "bin/lld-link": False, "bin/lldb": False,
    "bin/clang-tidy": False, "bin/clang-format": False, "bin/llvm-ar": False, "bin/wasm32-wasip1-clang++": False,
    "bin/wasm-component-ld": False, "bin/clang-scan-deps": False, "bin/xml2-config": False,
    "lib/clang/23/include/stddef.h": True, "lib/clang/23/include/wasm_simd128.h": True,
    "lib/clang/23/lib/wasm32-unknown-wasip1/libclang_rt.builtins.a": True,
    "lib/clang/23/lib/wasm32-unknown-wasip2/libclang_rt.builtins.a": False,
    "lib/clang/23/lib/wasm32-unknown-wasi/libclang_rt.builtins.a": False,
    "lib/clang/23/lib/wasm32-unknown-wasip1-threads/libclang_rt.builtins.a": False,
    "lib/cmake/llvm/LLVMConfig.cmake": False, "lib/pkgconfig/libedit.pc": False, "lib/libedit.la": False,
    "share/wasi-sysroot/include/wasm32-wasip1/stdio.h": True,
    "share/wasi-sysroot/include/wasm32-wasip1/eh/c++/v1/vector": True,
    "share/wasi-sysroot/include/wasm32-wasip1/noeh/c++/v1/vector": False,
    "share/wasi-sysroot/include/wasm32-wasip2/stdio.h": False,
    "share/wasi-sysroot/lib/wasm32-wasip1/libc.a": True, "share/wasi-sysroot/lib/wasm32-wasip1/crt1-reactor.o": True,
    f"{EH}/libc++.a": True, f"{EH}/libc++abi.a": True, f"{EH}/libunwind.a": True,
    f"{EH}/llvm-lto/23.1.0/libc++.a": False,
    "share/wasi-sysroot/lib/wasm32-wasip1/llvm-lto/23.1.0/libc.a": False,
    "share/wasi-sysroot/lib/wasm32-wasip1/noeh/libc++.a": False,
    "share/wasi-sysroot/lib/wasm32-wasip1-threads/libc.a": False,
    "share/wasi-sysroot/experimental-coop-threads/lib/libc.a": False,
    "share/wasi-sysroot/share/libc++/v1/std.cppm": False,
    "share/cmake/wasi-sdk.cmake": False, "share/man/man1/clang.1": False, "include/llvm/IR/Module.h": False,
}


def test_every_host_is_wasi_sdks_own_release_pinned_by_sha256():
    """The same release and checksums simee-core pins in cmake/SimeeWasm.cmake."""
    assert set(BINARIES) == {"macos-arm64", "macos-x86_64", "linux-x86_64", "linux-arm64", "windows-x86_64"}
    for b in BINARIES.values():
        assert len(b.sha256) == 64 and b.url == (
            f"https://github.com/WebAssembly/wasi-sdk/releases/download/wasi-sdk-34/{b.archive}")
    assert BINARIES["macos-arm64"].archive == "wasi-sdk-34.0-arm64-macos.tar.gz"  # wasi-sdk's <arch>-<os>
    assert BINARIES["macos-arm64"].sha256 == "9c59398106b417f8f14913380fdf0097a8cc0ff4af9eb3ce0065a859e88d49e9"
    assert BINARIES["windows-x86_64"].archive == "wasi-sdk-34.0-x86_64-windows.tar.gz"


def test_assets_are_named_after_the_release_and_the_host_and_linux_arm64_is_for_the_build_only():
    assert components.name("linux-arm64") == f"wasi-sdk-{VERSION}-linux-arm64"
    assert "linux-arm64" not in components.SHIPPED_IN_APP and set(components.SHIPPED_IN_APP) < set(BINARIES)


def test_every_licence_is_pinned_at_the_commit_the_release_is_built_from():
    for c in COMPONENTS:
        for licence in c.licences:
            assert len(licence.source.sha256) == 64
    urls = [l.source.url for c in COMPONENTS for l in c.licences]
    assert any(components.LLVM_COMMIT in u and u.endswith("/libcxx/LICENSE.TXT") for u in urls)
    assert any(components.WASI_LIBC_COMMIT in u and u.endswith("/libc-top-half/musl/COPYRIGHT") for u in urls)
    assert any(components.WASI_SDK_COMMIT in u for u in urls)


@pytest.mark.parametrize("rel,wanted", SDK_FILES.items())
def test_only_what_a_model_build_runs_and_links_is_kept(rel, wanted):
    assert package.kept(rel) == wanted


def _add(tar: tarfile.TarFile, name: str, data: bytes = b"", link: str | None = None, mode: int = 0o644) -> None:
    info = tarfile.TarInfo(f"{TOP}/{name}")
    info.mode = mode
    if link is not None:
        info.type, info.linkname = tarfile.SYMTYPE, link
        tar.addfile(info)
    else:
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))


def _elf(tmp_path: Path, name: str, *needed: str) -> bytes:
    return make_elf(tmp_path / "elves" / name, needed).read_bytes()


def _linux_sdk(tmp_path: Path, llvm_needs: tuple[str, ...] = ("libedit.so.0", "libc.so.6")) -> Path:
    """Like wasi-sdk's Linux archive: the programs load libLLVM (and clang libclang-cpp), which loads libedit, and
    the libraries the system has; lldb's libraries and the unversioned links are there too."""
    sdk = tmp_path / "sdk.tar.gz"
    with tarfile.open(sdk, "w:gz") as tar:
        for rel in SDK_FILES:
            if not rel.startswith(("bin/", "lib/lib")):
                _add(tar, rel, rel.encode())
        _add(tar, "bin/clang-23", _elf(tmp_path, "clang", "libclang-cpp.so.23.1-wasi-sdk", "libLLVM.so.23.1-wasi-sdk",
                                       "libstdc++.so.6", "libgcc_s.so.1", "libc.so.6"), mode=0o755)
        _add(tar, "bin/clang", link="clang-23")
        _add(tar, "bin/clang++", link="clang")
        _add(tar, "bin/lld", _elf(tmp_path, "lld", "libLLVM.so.23.1-wasi-sdk", "libm.so.6"), mode=0o755)
        _add(tar, "bin/wasm-ld", link="lld")
        _add(tar, "bin/lldb", _elf(tmp_path, "lldb", "liblldb.so.23.1-wasi-sdk"), mode=0o755)
        _add(tar, "bin/clang.cfg", b"--sysroot=<CFGDIR>/../share/wasi-sysroot\n")
        _add(tar, "lib/libLLVM.so.23.1-wasi-sdk", _elf(tmp_path, "llvm", *llvm_needs))
        _add(tar, "lib/libLLVM.so", link="libLLVM.so.23.1-wasi-sdk")
        _add(tar, "lib/libclang-cpp.so.23.1-wasi-sdk", _elf(tmp_path, "clang-cpp", "libLLVM.so.23.1-wasi-sdk"))
        _add(tar, "lib/libedit.so.0.0.76", _elf(tmp_path, "edit", "libtinfo.so.6"))
        _add(tar, "lib/libedit.so.0", link="libedit.so.0.0.76")
        _add(tar, "lib/libedit.so", link="libedit.so.0.0.76")
        _add(tar, "lib/liblldb.so.23.1-wasi-sdk", _elf(tmp_path, "lldb-lib", "libxml2.so.16"))
        _add(tar, "lib/libxml2.so.16", _elf(tmp_path, "xml2"))
    return sdk


def _files(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() or p.is_symlink())


def test_extract_keeps_the_kept_files_and_only_the_libraries_the_programs_load(tmp_path):
    root = package.extract(_linux_sdk(tmp_path), tmp_path / "work", "wasi-sdk-x")
    assert root == tmp_path / "work" / "wasi-sdk-x"
    libs = [rel for rel in _files(root) if rel.startswith("lib/lib")]
    assert libs == ["lib/libLLVM.so.23.1-wasi-sdk", "lib/libclang-cpp.so.23.1-wasi-sdk", "lib/libedit.so.0",
                    "lib/libedit.so.0.0.76"]
    assert (root / "lib/libedit.so.0").readlink() == Path("libedit.so.0.0.76")
    assert sorted(p.name for p in (root / "bin").iterdir()) == [
        "clang", "clang++", "clang-23", "clang.cfg", "lld", "wasm-ld"]
    assert (root / "bin/clang++").resolve() == (root / "bin/clang-23").resolve()
    rest = [rel for rel in _files(root) if not rel.startswith(("bin/", "lib/lib"))]
    assert rest == sorted(rel for rel, wanted in SDK_FILES.items() if wanted and not rel.startswith("bin/"))


def test_extract_makes_the_empty_cxx_header_folder_clang_needs(tmp_path):
    root = package.extract(_linux_sdk(tmp_path), tmp_path / "work", "wasi-sdk-x")
    v1 = root / "share/wasi-sysroot/include/c++/v1"
    assert v1.is_dir() and not any(v1.iterdir())


def test_a_library_the_programs_load_that_is_neither_shipped_nor_the_systems_fails(tmp_path):
    with pytest.raises(RuntimeError, match=r"libLLVM.so.23.1-wasi-sdk loads libz.so.1"):
        package.extract(_linux_sdk(tmp_path, ("libz.so.1",)), tmp_path / "work", "wasi-sdk-x")


def test_macos_install_names_resolve_in_lib_and_the_systems_are_left_out(tmp_path):
    root = tmp_path / "root"
    (root / "lib").mkdir(parents=True)
    (root / "bin").mkdir()
    uuid = "11111111-1111-1111-1111-111111111111"
    (root / "bin/clang-23").write_bytes(tiny_macho.thin("arm64", uuid, deps=(
        "@rpath/libLLVM.23.1-wasi-sdk.dylib", "/usr/lib/libc++.1.dylib", "/usr/lib/libSystem.B.dylib")))
    (root / "bin/lld").write_bytes(tiny_macho.thin("arm64", uuid, deps=("@rpath/libLLVM.23.1-wasi-sdk.dylib",)))
    (root / "lib/libLLVM.23.1-wasi-sdk.dylib").write_bytes(tiny_macho.thin(
        "arm64", uuid, deps=("@rpath/libedit.0.dylib", "/System/Library/Frameworks/CoreServices.framework/CoreServices")))
    (root / "lib/libedit.0.dylib").write_bytes(tiny_macho.thin("arm64", uuid))
    (root / "lib/liblldb.dylib").write_bytes(tiny_macho.thin("arm64", uuid))
    assert package.libraries(root) == {root / "lib/libLLVM.23.1-wasi-sdk.dylib", root / "lib/libedit.0.dylib"}


def test_windows_copies_of_a_program_become_hard_links(tmp_path):
    bin_ = tmp_path / "root/bin"
    bin_.mkdir(parents=True)
    for name, data in (("clang.exe", b"clang"), ("clang++.exe", b"clang"), ("lld.exe", b"lld"),
                       ("wasm-ld.exe", b"lld"), ("clang.cfg", b"cfg"), ("clang++.cfg", b"cfg")):
        (bin_ / name).write_bytes(data)
    package.link_copies(tmp_path / "root")
    assert (bin_ / "clang++.exe").samefile(bin_ / "clang.exe") and (bin_ / "wasm-ld.exe").samefile(bin_ / "lld.exe")
    assert not (bin_ / "clang.exe").samefile(bin_ / "lld.exe")
    assert not (bin_ / "clang.cfg").samefile(bin_ / "clang++.cfg")  # only the programs: each .cfg is its own file
    assert (bin_ / "clang++.exe").read_bytes() == b"clang"


def _runtime(tmp_path: Path, names=("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll", "msvcp140_atomic_wait.dll")):
    """Like kicad-cli's Windows bundle: Microsoft's C++ runtime in bin/."""
    path = tmp_path / "kicad-cli-windows.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("kicad-cli-10.0.8-windows-x86_64/bin/kicad-cli.exe", b"kicad")
        for name in names:
            z.writestr(f"kicad-cli-10.0.8-windows-x86_64/bin/{name}", b"MZ" + name.encode())
    return path


def _windows(monkeypatch, tmp_path: Path, versions: dict[str, dict[str, str]] | None = None) -> Path:
    """A Windows root whose programs load Microsoft's C++ runtime and were linked by MSVC 14.44."""
    root = tmp_path / "root"
    (root / "bin").mkdir(parents=True)
    for name in ("clang.exe", "clang++.exe", "lld.exe", "wasm-ld.exe"):
        (root / "bin" / name).write_bytes(b"MZ" + name.encode())
    loads = ["KERNEL32.dll", "MSVCP140.dll", "VCRUNTIME140.dll", "VCRUNTIME140_1.dll", "api-ms-win-crt-heap-l1-1-0.dll"]
    monkeypatch.setattr(package.pe, "deps", lambda path: [] if path.suffix == ".dll" else loads)
    monkeypatch.setattr(package, "_linker_version", lambda path: (14, 44))
    microsoft = {"CompanyName": "Microsoft Corporation", "FileVersion": "14.44.35211.0"}
    monkeypatch.setattr(package.pe, "version_info", lambda path: (versions or {}).get(path.name, microsoft))
    return root


def test_windows_gets_the_microsoft_runtime_its_programs_load_and_nothing_else(monkeypatch, tmp_path):
    root = _windows(monkeypatch, tmp_path)
    assert package.add_msvc_runtime(root, _runtime(tmp_path)) == ["msvcp140.dll", "vcruntime140.dll",
                                                                  "vcruntime140_1.dll"]
    assert (root / "bin/vcruntime140_1.dll").read_bytes() == b"MZvcruntime140_1.dll"
    assert not (root / "bin/msvcp140_atomic_wait.dll").exists()
    assert package.libraries(root) == {root / "bin" / n for n in ("msvcp140.dll", "vcruntime140.dll",
                                                                  "vcruntime140_1.dll")}
    assert [c.name for c in package.shipped(root)] == ["llvm-project", "wasi-libc", "wasi-sdk", "microsoft-vc-runtime"]


def test_a_microsoft_runtime_older_than_the_programs_toolset_or_not_microsofts_fails(monkeypatch, tmp_path):
    old = {"CompanyName": "Microsoft Corporation", "FileVersion": "14.29.30133.0"}
    root = _windows(monkeypatch, tmp_path, {"msvcp140.dll": old})
    with pytest.raises(RuntimeError, match=r"msvcp140.dll .* for MSVC 14.44 or later"):
        package.add_msvc_runtime(root, _runtime(tmp_path))
    root = _windows(monkeypatch, tmp_path / "2", {"vcruntime140.dll": {"CompanyName": "Someone"}})
    with pytest.raises(RuntimeError, match="vcruntime140.dll"):
        package.add_msvc_runtime(root, _runtime(tmp_path))


def test_windows_without_the_microsoft_runtime_fails(monkeypatch, tmp_path):
    root = _windows(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError, match=r"MSVCP140.dll"):
        package.libraries(root)
    with pytest.raises(RuntimeError, match=r"has no vcruntime140_1.dll"):
        package.add_msvc_runtime(root, _runtime(tmp_path, ("msvcp140.dll", "vcruntime140.dll")))


def test_libedits_licence_is_shipped_only_where_the_programs_load_it(tmp_path):
    root = package.extract(_linux_sdk(tmp_path), tmp_path / "work", "wasi-sdk-x")
    assert [c.name for c in package.shipped(root)] == ["llvm-project", "wasi-libc", "wasi-sdk", "libedit"]
    for edit in (root / "lib").glob("libedit*"):
        edit.unlink()
    assert [c.name for c in package.shipped(root)] == ["llvm-project", "wasi-libc", "wasi-sdk"]


def test_a_library_no_component_is_the_source_of_fails(tmp_path):
    root = package.extract(_linux_sdk(tmp_path), tmp_path / "work", "wasi-sdk-x")
    (root / "lib/libzstd.so.1").write_bytes(b"")
    with pytest.raises(RuntimeError, match="libzstd.so.1"):
        package.shipped(root)


def _fake_fetch(monkeypatch) -> list[str]:
    """components.fetch swapped for one that 'downloads' each file as its URL's bytes, and libedit's release as a
    tarball holding COPYING."""
    fetched = []

    def fetch(download, cache, get=None):
        fetched.append(download.url)
        path = cache / Path(download.url).name
        path.parent.mkdir(parents=True, exist_ok=True)
        if download.url.endswith(".tar.gz"):
            with tarfile.open(path, "w:gz") as tar:
                info = tarfile.TarInfo("libedit-20251016-3.1/COPYING")
                info.size = 6
                tar.addfile(info, io.BytesIO(b"libedt"))
        else:
            path.write_bytes(download.url.encode())
        return path

    monkeypatch.setattr(package.components, "fetch", fetch)
    return fetched


def test_licences_of_every_shipped_component_come_from_upstream_at_the_pinned_commits(monkeypatch, tmp_path):
    _fake_fetch(monkeypatch)
    package.write_licences(list(COMPONENTS), tmp_path / "root", tmp_path / "cache")
    doc = tmp_path / "root/share/doc"
    assert sorted(p.name for p in doc.iterdir()) == ["libedit", "llvm-project", "wasi-libc", "wasi-sdk"]
    assert (doc / "libedit/COPYING").read_bytes() == b"libedt"
    assert (doc / "llvm-project/libcxxabi/LICENSE.TXT").read_text().endswith(
        f"{components.LLVM_COMMIT}/libcxxabi/LICENSE.TXT")
    assert (doc / "wasi-libc/libc-bottom-half/cloudlibc/LICENSE").exists()


def test_notice_names_wasi_sdks_download_and_each_components_licence():
    found = [c for c in COMPONENTS if c.always]
    text = package.notice("linux-x86_64", found)
    b = BINARIES["linux-x86_64"]
    assert b.url in text and b.sha256 in text and VERSION in text and "lld (as wasm-ld)" in text
    assert "clang (as clang and clang++)" in text
    assert all(c.licence in text for c in found) and "Apache-2.0 WITH LLVM-exception" in text
    assert "libstdc++" in text and "libstdc++" not in package.notice("macos-arm64", found)
    assert "Microsoft" not in text
    windows = package.notice("windows-x86_64", found, ["msvcp140.dll", "vcruntime140.dll"])
    assert "msvcp140.dll, vcruntime140.dll" in windows and "Distributable Code" in windows
    assert components.MSVC_RUNTIME.url in windows


def test_package_archives_the_root_with_its_empty_folder_hard_links_and_notice(monkeypatch, tmp_path):
    _fake_fetch(monkeypatch)
    root = package.extract(_linux_sdk(tmp_path), tmp_path / "work", components.name("linux-x86_64"))
    (root / "bin/wasm-ld").unlink()
    (root / "bin/wasm-ld").write_bytes((root / "bin/lld").read_bytes())  # as on Windows: a copy
    package.link_copies(root)
    out = package.package(root, "linux-x86_64", tmp_path / "cache", tmp_path / "dist")
    assert out.name == f"{components.name('linux-x86_64')}.tar.xz"
    top = components.name("linux-x86_64")
    with tarfile.open(out) as tar:
        members = {m.name: m for m in tar.getmembers()}
    assert members[f"{top}/share/wasi-sysroot/include/c++/v1"].isdir()
    assert members[f"{top}/bin/clang++"].issym()
    assert {members[f"{top}/bin/wasm-ld"].islnk(), members[f"{top}/bin/lld"].islnk()} == {True, False}
    assert {f"{top}/THIRD-PARTY.txt", f"{top}/share/doc/libedit/COPYING", f"{top}/VERSION"} <= set(members)


def test_unpacked_size_counts_each_file_once(tmp_path):
    (tmp_path / "a").write_bytes(b"12345")
    os.link(tmp_path / "a", tmp_path / "b")
    (tmp_path / "c").symlink_to("a")
    (tmp_path / "d").write_bytes(b"12")
    assert cli.unpacked_size(tmp_path) == 7


def test_the_check_builds_with_simee_add_wasm_modules_flags(monkeypatch, tmp_path):
    calls = []

    def runner(root, work):
        def run(tool, *args):
            calls.append((tool, *map(str, args)))
            return ""
        return run

    monkeypatch.setattr(check, "runner", runner)
    module = check.build(tmp_path / "root", tmp_path)
    assert module == tmp_path / "model.wasm"
    sysroot = tmp_path / "root/share/wasi-sysroot"
    assert [c[0] for c in calls] == ["clang", "clang++", "wasm-ld", "clang++", "clang++"]
    compile_, link = calls[3][1:], calls[4][1:]
    assert compile_ == ("--target=wasm32-wasip1", f"--sysroot={sysroot}", "-std=c++20", "-O2", "-fwasm-exceptions",
                        "-mllvm", "-wasm-use-legacy-eh=false", "-c", str(check.SMOKE / "model.cpp"),
                        "-o", str(tmp_path / "model.o"))
    assert link == ("--target=wasm32-wasip1", f"--sysroot={sysroot}", "-O2", "-fwasm-exceptions",
                    "-mexec-model=reactor", "-Wl,--export=__stack_pointer", str(tmp_path / "model.o"),
                    f"-L{sysroot}/lib/wasm32-wasip1/eh", "-lunwind", "-o", str(module))


REACTOR = """(module
  (global (export "__stack_pointer") (mut i32) (i32.const 1024))
  (func (export "_initialize"))
  (func (export "smoke_step") (param i32) (result i32) {body}){extra})"""


def _module(tmp_path: Path, body: str, extra: str = "") -> Path:
    path = tmp_path / "model.wasm"
    path.write_bytes(wasmtime.wat2wasm(REACTOR.format(body=body, extra=extra)))
    return path


def test_the_check_runs_the_module_in_wasmtime(tmp_path):
    check.run_module(_module(tmp_path, "(if (result i32) (i32.lt_s (local.get 0) (i32.const 0)) "
                                       "(then (i32.const -1)) (else (i32.const 3)))"))


def test_the_check_fails_a_module_that_answers_wrong_or_is_a_command(tmp_path):
    with pytest.raises(RuntimeError, match=r"gave \(3, 3\)"):
        check.run_module(_module(tmp_path, "(i32.const 3)"))
    with pytest.raises(RuntimeError, match="not a reactor"):
        check.run_module(_module(tmp_path, "(i32.const 3)", '\n  (func (export "_start"))'))


def test_release_tag_counts_builds_of_one_wasi_sdk_release_apart_from_the_others():
    tags = ["cli-10.0.8-2", "arm-gcc-15.3.rel2-1", "wasi-sdk-34.0-1", "wasi-sdk-33.0-4"]
    assert publish.next_tag("34.0", tags) == "wasi-sdk-34.0-2"
    assert publish.next_tag("35.0", tags) == "wasi-sdk-35.0-1"


def test_release_notes_list_the_checksums_to_pin():
    sums = "aa  wasi-sdk-34.0-linux-x86_64.tar.xz\n"
    text = publish.release_notes("https://run/1", sums)
    assert sums.strip() in text and "https://run/1" in text and "cmake/SimeeWasm.cmake" in text
    assert components.DOWNLOADS in text and "linux-arm64" in text and "bin/clang++" in text


# The real toolchain, when one is at hand: WASM_TOOLCHAIN=<archive> uv run pytest tests/test_wasm_toolchain.py
@pytest.mark.skipif(not os.environ.get("WASM_TOOLCHAIN"), reason="WASM_TOOLCHAIN (a packaged archive) not set")
def test_a_packaged_toolchain_compiles_links_and_runs_a_model_from_a_copy_with_an_empty_path():
    check.check(Path(os.environ["WASM_TOOLCHAIN"]))
