from kicad_bundle.pe import make_resolver


def test_resolver_is_case_insensitive_and_treats_unknown_dlls_as_system(tmp_path):
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "wxbase32u_vc_x64_custom.dll").touch()
    resolve = make_resolver([tmp_path / "bin"])
    cli = tmp_path / "bin" / "kicad-cli.exe"
    assert resolve("WXBASE32U_VC_X64_CUSTOM.dll", cli) == tmp_path / "bin" / "wxbase32u_vc_x64_custom.dll"
    assert resolve("KERNEL32.dll", cli) is None
    assert resolve("api-ms-win-crt-runtime-l1-1-0.dll", cli) is None
