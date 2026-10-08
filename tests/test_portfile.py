import pytest

from kicad_bundle.portfile import Download, commands, downloads

SHA = "ab" * 64


def test_commands_split_cmake_into_calls_with_quoted_bracket_and_nested_arguments():
    text = '''# a comment (with parens)
set(A "x y" b) # trailing
string(REPLACE "." "_" OUT "R_${VERSION}")
vcpkg_replace_string("${SOURCE_PATH}/x.h" [[-DEV"]] [["]])
if((A AND B) OR C)
endif()
'''
    assert commands(text) == [
        ("set", [("A", False), ("x y", True), ("b", False)]),
        ("string", [("REPLACE", False), (".", True), ("_", True), ("OUT", False), ("R_${VERSION}", True)]),
        ("vcpkg_replace_string", [("${SOURCE_PATH}/x.h", True), ('-DEV"', True), ('"', True)]),
        ("if", [("(A", False), ("AND", False), ("B)", False), ("OR", False), ("C", False)]),
        ("endif", [])]


def test_from_github_with_a_ref_built_by_string_replace():
    portfile = f'''string(REPLACE "." "_" curl_version "curl-${{VERSION}}")
vcpkg_from_github(
    OUT_SOURCE_PATH SOURCE_PATH
    REPO curl/curl
    REF ${{curl_version}}
    SHA512 {SHA}
    HEAD_REF master
    PATCHES
        dependencies.patch # why
)'''
    assert downloads(portfile, "8.18.0") == [Download(
        ("https://github.com/curl/curl/archive/curl-8_18_0.tar.gz",), "curl-curl-curl-8_18_0.tar.gz", SHA,
        ("dependencies.patch",), main=True)]


def test_from_gitlab_from_sourceforge_and_a_regex_match():
    portfile = f'''string(REGEX MATCH "^([0-9]+)\\\\.([0-9]+)" PYVER "${{VERSION}}")
set(MINOR "${{CMAKE_MATCH_2}}")
vcpkg_from_gitlab(GITLAB_URL https://gitlab.freedesktop.org/ OUT_SOURCE_PATH SOURCE_PATH
    REPO freetype/freetype REF "VER-${{MINOR}}" SHA512 {SHA} PATCHES a.patch ${{EXTRA}})
set(PCRE_VERSION 8.45)
vcpkg_from_sourceforge(OUT_SOURCE_PATH OTHER REPO pcre/pcre REF ${{PCRE_VERSION}}
    FILENAME "pcre-${{PCRE_VERSION}}.zip" SHA512 {SHA})'''
    assert downloads(portfile, "2.13.3") == [
        Download(("https://gitlab.freedesktop.org/freetype/freetype/-/archive/VER-13/freetype-VER-13.tar.gz",),
                 "freetype-freetype-VER-13.tar.gz", SHA, ("a.patch",), main=True),
        Download(("https://sourceforge.net/projects/pcre/files/pcre/8.45/pcre-8.45.zip/download",),
                 "pcre-8.45.zip", SHA, (), main=False)]


def test_distfile_takes_its_patches_from_the_extraction_and_lists_go_through_variables():
    portfile = f'''set(PATCHES fix-a.patch)
list(APPEND PATCHES fix-b.patch)
vcpkg_download_distfile(ARCHIVE
    URLS "https://sourceware.org/pub/bzip2/bzip2-${{VERSION}}.tar.gz" "https://mirror/bzip2-${{VERSION}}.tar.gz"
    FILENAME "bzip2-${{VERSION}}.tar.gz"
    SHA512 {SHA}
)
vcpkg_extract_source_archive(SOURCE_PATH ARCHIVE "${{ARCHIVE}}" PATCHES ${{PATCHES}})'''
    assert downloads(portfile, "1.0.8") == [Download(
        ("https://sourceware.org/pub/bzip2/bzip2-1.0.8.tar.gz", "https://mirror/bzip2-1.0.8.tar.gz"),
        "bzip2-1.0.8.tar.gz", SHA, ("fix-a.patch", "fix-b.patch"), main=True)]


def test_function_bodies_do_not_change_the_variables():
    portfile = f'''function(helper)
    set(VERSION "3.11")
endfunction()
vcpkg_from_github(OUT_SOURCE_PATH SOURCE_PATH REPO python/cpython REF v${{VERSION}} SHA512 {SHA})'''
    assert downloads(portfile, "3.11.5")[0].urls == ("https://github.com/python/cpython/archive/v3.11.5.tar.gz",)


def test_an_unresolvable_download_fails():
    portfile = f'vcpkg_from_github(OUT_SOURCE_PATH SOURCE_PATH REPO a/b REF ${{UNKNOWN}} SHA512 {SHA})'
    with pytest.raises(RuntimeError, match="REF"):
        downloads(portfile, "1")


def test_an_unsupported_fetcher_fails():
    with pytest.raises(RuntimeError, match="vcpkg_from_git"):
        downloads("vcpkg_from_git(OUT_SOURCE_PATH SOURCE_PATH URL https://x REF abc)", "1")
