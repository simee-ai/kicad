"""Prove a wasm toolchain archive runs from any folder: unpack it into a temp dir (with a space in its path) and,
with an empty PATH, compile and link a C++20 model that throws and catches (smoke/model.cpp) into a wasm32-wasip1
reactor module with simee-core's flags (cmake/SimeeWasm.cmake, simee_add_wasm_module), then run the module in
wasmtime: its exception is thrown and caught in the standard encoding, which wasmtime runs (it rejects the legacy one
clang emits by default). clang, its C++ headers, wasm-ld, the sysroot and libc++ must be found relative to the
toolchain or not at all."""

import tempfile
from pathlib import Path

from kicad_bundle.toolchain_check import runner, unpack
from wasm_toolchain.package import SYSROOT, TARGET

SMOKE = Path(__file__).parent / "smoke"
MODEL = "model.cpp"
OPTIMISE = ["-O2"]  # SIMEE_WASM_CXXFLAGS' default


def compile_flags(sysroot: Path) -> list[str]:
    """simee_add_wasm_module's for each source."""
    return [f"--target={TARGET}", f"--sysroot={sysroot}", "-std=c++20", *OPTIMISE,
            "-fwasm-exceptions", "-mllvm", "-wasm-use-legacy-eh=false"]


def link_flags(sysroot: Path) -> list[str]:
    """simee_add_wasm_module's to link the objects, with the export checkpoints need
    (SIMEE_WASM_CHECKPOINT_LINK_OPTIONS); the objects and -lunwind follow them."""
    return [f"--target={TARGET}", f"--sysroot={sysroot}", *OPTIMISE, "-fwasm-exceptions", "-mexec-model=reactor",
            "-Wl,--export=__stack_pointer"]


def build(root: Path, work: Path) -> Path:
    """smoke/model.cpp compiled and linked by root's clang++ into work/model.wasm."""
    run = runner(root, work)
    for tool in ("clang", "clang++", "wasm-ld"):  # each of the names runs
        run(tool, "--version")
    sysroot = root / SYSROOT
    obj, module = work / "model.o", work / "model.wasm"
    run("clang++", *compile_flags(sysroot), "-c", SMOKE / MODEL, "-o", obj)
    run("clang++", *link_flags(sysroot), obj, f"-L{sysroot}/lib/{TARGET}/eh", "-lunwind", "-o", module)
    return module


def run_module(module: Path) -> None:
    """Instantiate module as a WASI reactor and call its smoke_step: 5 sorts to 3; -4 throws, caught as -1."""
    import wasmtime

    engine = wasmtime.Engine()
    loaded = wasmtime.Module.from_file(engine, str(module))
    exports = {e.name for e in loaded.exports}
    if "_start" in exports or not {"_initialize", "__stack_pointer", "smoke_step"} <= exports:
        raise RuntimeError(f"{module.name} is not a reactor module with the exports simee needs: {sorted(exports)}")
    linker = wasmtime.Linker(engine)
    linker.define_wasi()
    store = wasmtime.Store(engine)
    store.set_wasi(wasmtime.WasiConfig())
    instance = linker.instantiate(store, loaded).exports(store)
    instance["_initialize"](store)
    got = instance["smoke_step"](store, 5), instance["smoke_step"](store, -4)
    if got != (3, -1):
        raise RuntimeError(f"{module.name}'s smoke_step(5), smoke_step(-4) gave {got}, not (3, -1)")


def check(archive: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="wasm sdk check ") as tmp:
        run_module(build(unpack(archive, Path(tmp) / "copy"), Path(tmp)))
    print(f"  {archive.name}: compiles, links and runs a C++20 model that throws and catches, "
          "from a copy with an empty PATH")
