"""Unit tests for files/kde-linux-system/generate-initramfs/copy-initramfs.py.

The script walks a root filesystem, resolves the transitive dependencies of the
files that belong in the initramfs (ELF libraries, kernel modules, firmware and
systemd units) and copies them into the target root. Everything covered here is
the pure resolution and parsing logic; the ELF readers are driven through small
fakes so no binaries are needed.
"""

import io
import os
import stat
import subprocess

import pytest


# ---------------------------------------------------------------------------
# parse_systemd
# ---------------------------------------------------------------------------


def test_parse_systemd_reads_sections_and_values(copy_initramfs):
    unit = io.StringIO(
        "[Unit]\n"
        "Description=Example\n"
        "Wants=a.service b.service\n"
        "\n"
        "[Service]\n"
        "ExecStart=/usr/bin/example\n"
    )

    assert copy_initramfs.parse_systemd(unit) == {
        "Unit": {"Description": ["Example"], "Wants": ["a.service b.service"]},
        "Service": {"ExecStart": ["/usr/bin/example"]},
    }


def test_parse_systemd_ignores_comment_lines(copy_initramfs):
    unit = io.StringIO(
        "# hash comment\n"
        "; semicolon comment\n"
        "[Unit]\n"
        "# another one\n"
        "Wants=a.service\n"
    )

    assert copy_initramfs.parse_systemd(unit) == {"Unit": {"Wants": ["a.service"]}}


def test_parse_systemd_accumulates_repeated_keys(copy_initramfs):
    unit = io.StringIO("[Unit]\nWants=a.service\nWants=b.service\n")

    assert copy_initramfs.parse_systemd(unit) == {
        "Unit": {"Wants": ["a.service", "b.service"]}
    }


def test_parse_systemd_empty_assignment_resets_the_list(copy_initramfs):
    """`Key=` is systemd's reset syntax: it drops everything assigned before."""
    unit = io.StringIO("[Unit]\nWants=a.service\nWants=b.service\nWants=\n")

    assert copy_initramfs.parse_systemd(unit) == {"Unit": {"Wants": []}}


def test_parse_systemd_rejects_unterminated_section_header(copy_initramfs):
    with pytest.raises(copy_initramfs.ParseError):
        copy_initramfs.parse_systemd(io.StringIO("[Unit\nWants=a.service\n"))


def test_parse_systemd_rejects_line_without_assignment(copy_initramfs):
    with pytest.raises(copy_initramfs.ParseError):
        copy_initramfs.parse_systemd(io.StringIO("[Unit]\nnot an assignment\n"))


@pytest.mark.xfail(
    raises=AttributeError,
    strict=True,
    reason="continuation lines are collected into a str, then appended to; "
    "see the tracking issue for the parser defect",
)
def test_parse_systemd_joins_backslash_continuation_lines(copy_initramfs):
    unit = io.StringIO("[Service]\nExecStart=/usr/bin/example \\\n    --flag\n")

    assert copy_initramfs.parse_systemd(unit) == {
        "Service": {"ExecStart": ["/usr/bin/example     --flag"]}
    }


# ---------------------------------------------------------------------------
# get_dependencies_systemd
# ---------------------------------------------------------------------------


class RecordingUnitResolver:
    def __init__(self):
        self.units = []
        self.exes = []

    def resolve_unit(self, name):
        self.units.append(name)
        return f"unit:{name}"

    def resolve_exe(self, name):
        self.exes.append(name)
        return f"exe:{name}"


def test_get_dependencies_systemd_collects_every_dependency_property(copy_initramfs):
    unit = io.StringIO(
        "[Unit]\n"
        "Wants=a.service\n"
        "Requires=b.service\n"
        "Upholds=c.service\n"
        "BindsTo=d.service\n"
    )
    resolver = RecordingUnitResolver()

    list(copy_initramfs.get_dependencies_systemd(unit, resolver))

    assert sorted(resolver.units) == ["a.service", "b.service", "c.service", "d.service"]


def test_get_dependencies_systemd_deduplicates_units(copy_initramfs):
    unit = io.StringIO("[Unit]\nWants=a.service a.service\nRequires=a.service\n")
    resolver = RecordingUnitResolver()

    list(copy_initramfs.get_dependencies_systemd(unit, resolver))

    assert resolver.units == ["a.service"]


def test_get_dependencies_systemd_skips_units_with_specifiers(copy_initramfs):
    """`%i`-style specifiers are only known at runtime, so they cannot be copied."""
    unit = io.StringIO("[Unit]\nWants=plain.service tmpl@%i.service\n")
    resolver = RecordingUnitResolver()

    list(copy_initramfs.get_dependencies_systemd(unit, resolver))

    assert resolver.units == ["plain.service"]


@pytest.mark.parametrize(
    "line, expected",
    [
        ("ExecStart=/usr/bin/example --flag", "/usr/bin/example"),
        ("ExecStart=-/usr/bin/example", "/usr/bin/example"),
        ("ExecStart=@/usr/bin/example argv0", "/usr/bin/example"),
    ],
)
def test_get_dependencies_systemd_strips_exec_prefixes_and_arguments(
    copy_initramfs, line, expected
):
    resolver = RecordingUnitResolver()

    list(
        copy_initramfs.get_dependencies_systemd(
            io.StringIO(f"[Service]\n{line}\n"), resolver
        )
    )

    assert resolver.exes == [expected]


def test_get_dependencies_systemd_covers_all_exec_properties(copy_initramfs):
    unit = io.StringIO(
        "[Service]\n"
        "ExecStartPre=/usr/bin/pre\n"
        "ExecStart=/usr/bin/main\n"
        "ExecStartPost=/usr/bin/post\n"
        "ExecStopPre=/usr/bin/stoppre\n"
        "ExecStop=/usr/bin/stop\n"
        "ExecStopPost=/usr/bin/stoppost\n"
    )
    resolver = RecordingUnitResolver()

    list(copy_initramfs.get_dependencies_systemd(unit, resolver))

    assert sorted(resolver.exes) == [
        "/usr/bin/main",
        "/usr/bin/post",
        "/usr/bin/pre",
        "/usr/bin/stop",
        "/usr/bin/stoppost",
        "/usr/bin/stoppre",
    ]


def test_get_dependencies_systemd_ignores_unit_without_dependencies(copy_initramfs):
    resolver = RecordingUnitResolver()

    result = list(
        copy_initramfs.get_dependencies_systemd(
            io.StringIO("[Unit]\nDescription=Nothing to copy\n"), resolver
        )
    )

    assert result == []
    assert resolver.units == [] and resolver.exes == []


# ---------------------------------------------------------------------------
# LibraryResolver / SystemdResolver / ModuleResolver
# ---------------------------------------------------------------------------


def test_library_resolver_returns_the_first_existing_libdir_hit(copy_initramfs, tmp_path):
    (tmp_path / "usr/lib64").mkdir(parents=True)
    (tmp_path / "usr/lib64/libfoo.so.1").write_bytes(b"")
    resolver = copy_initramfs.LibraryResolver(
        str(tmp_path), ["/usr/lib", "/usr/lib64"]
    )

    assert resolver.resolve_library("libfoo.so.1") == str(
        tmp_path / "usr/lib64/libfoo.so.1"
    )


def test_library_resolver_falls_back_to_the_first_libdir(copy_initramfs, tmp_path):
    resolver = copy_initramfs.LibraryResolver(
        str(tmp_path), ["/usr/lib", "/usr/lib64"]
    )

    assert resolver.resolve_library("libmissing.so") == str(
        tmp_path / "usr/lib/libmissing.so"
    )


def test_systemd_resolver_prefers_an_existing_unit_file(copy_initramfs, tmp_path):
    units = tmp_path / "usr/lib/systemd/system"
    units.mkdir(parents=True)
    (units / "example.service").write_text("[Unit]\n")
    resolver = copy_initramfs.SystemdResolver(str(tmp_path))

    assert resolver.resolve_unit("example.service") == str(units / "example.service")


def test_systemd_resolver_falls_back_to_the_template_unit(copy_initramfs, tmp_path):
    units = tmp_path / "usr/lib/systemd/system"
    units.mkdir(parents=True)
    (units / "getty@.service").write_text("[Unit]\n")
    resolver = copy_initramfs.SystemdResolver(str(tmp_path))

    assert resolver.resolve_unit("getty@tty1.service") == str(units / "getty@.service")


def test_systemd_resolver_returns_the_plain_path_when_nothing_exists(
    copy_initramfs, tmp_path
):
    resolver = copy_initramfs.SystemdResolver(str(tmp_path))

    assert resolver.resolve_unit("missing.service") == str(
        tmp_path / "usr/lib/systemd/system/missing.service"
    )


def test_systemd_resolver_rebases_absolute_exec_paths(copy_initramfs, tmp_path):
    resolver = copy_initramfs.SystemdResolver(str(tmp_path))

    assert resolver.resolve_exe("/usr/bin/example") == str(tmp_path / "usr/bin/example")


def test_systemd_resolver_places_bare_exec_names_in_usr_bin(copy_initramfs, tmp_path):
    resolver = copy_initramfs.SystemdResolver(str(tmp_path))

    assert resolver.resolve_exe("example") == str(tmp_path / "usr/bin/example")


def test_module_resolver_asks_modinfo_for_the_module_path(copy_initramfs, monkeypatch):
    recorded = {}

    def fake_check_output(argv, **kwargs):
        recorded["argv"] = argv
        return "/usr/lib/modules/6.1.0/kernel/fs/ext4/ext4.ko\0"

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)
    resolver = copy_initramfs.ModuleResolver("/sysroot", "6.1.0")

    assert (
        resolver.resolve_module(b"ext4")
        == "/usr/lib/modules/6.1.0/kernel/fs/ext4/ext4.ko"
    )
    assert recorded["argv"][:5] == ["modinfo", "-k", "6.1.0", "-b", "/sysroot/usr"]
    assert recorded["argv"][-1] == "ext4"


@pytest.mark.parametrize("suffix", ["", ".xz", ".zst"])
def test_module_resolver_finds_firmware_with_any_known_compression(
    copy_initramfs, tmp_path, suffix
):
    firmware_dir = tmp_path / "usr/lib/firmware/amdgpu"
    firmware_dir.mkdir(parents=True)
    (firmware_dir / f"navi.bin{suffix}").write_bytes(b"")
    resolver = copy_initramfs.ModuleResolver(str(tmp_path), "6.1.0")

    assert resolver.resolve_firmware(b"amdgpu/navi.bin") == str(
        firmware_dir / f"navi.bin{suffix}"
    )


def test_module_resolver_returns_a_zstd_path_for_absent_firmware(
    copy_initramfs, tmp_path
):
    resolver = copy_initramfs.ModuleResolver(str(tmp_path), "6.1.0")

    assert resolver.resolve_firmware(b"missing.bin") == str(
        tmp_path / "usr/lib/firmware/missing.bin.zstd"
    )


# ---------------------------------------------------------------------------
# path helpers and copy
# ---------------------------------------------------------------------------


def test_reallinkpath_resolves_the_parent_but_keeps_the_basename(
    copy_initramfs, tmp_path
):
    (tmp_path / "usr/lib").mkdir(parents=True)
    (tmp_path / "lib").symlink_to("usr/lib")
    (tmp_path / "usr/lib/libfoo.so").symlink_to("libfoo.so.1")

    resolved = copy_initramfs.reallinkpath(str(tmp_path / "lib/libfoo.so"))

    assert resolved == str((tmp_path / "usr/lib/libfoo.so").resolve().parent / "libfoo.so")
    assert os.path.islink(resolved)


def test_is_already_copied_reports_present_and_absent_targets(copy_initramfs, tmp_path):
    targetroot = tmp_path / "target"
    (targetroot / "usr/bin").mkdir(parents=True)
    (targetroot / "usr/bin/example").write_bytes(b"")

    assert copy_initramfs.is_already_copied(None, "/usr/bin/example", str(targetroot))
    assert not copy_initramfs.is_already_copied(None, "/usr/bin/other", str(targetroot))


def test_is_already_copied_follows_a_symlinked_target_directory(
    copy_initramfs, tmp_path
):
    """`/bin/sh` and `/usr/bin/sh` must not be copied twice."""
    targetroot = tmp_path / "target"
    (targetroot / "usr/bin").mkdir(parents=True)
    (targetroot / "usr/bin/sh").write_bytes(b"")
    (targetroot / "bin").symlink_to("usr/bin")

    assert copy_initramfs.is_already_copied(None, str(targetroot / "bin/sh"), "/")


def test_copy_creates_a_directory_when_there_is_no_source(copy_initramfs, tmp_path):
    targetroot = tmp_path / "target"
    (targetroot / "usr").mkdir(parents=True)

    copy_initramfs.copy(None, "/usr/share", str(targetroot))

    assert (targetroot / "usr/share").is_dir()


def test_copy_recreates_a_symlink_without_dereferencing_it(copy_initramfs, tmp_path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    link = source_root / "libfoo.so"
    link.symlink_to("libfoo.so.1")
    targetroot = tmp_path / "target"
    (targetroot / "usr/lib").mkdir(parents=True)

    copy_initramfs.copy(str(link), "/usr/lib/libfoo.so", str(targetroot))

    copied = targetroot / "usr/lib/libfoo.so"
    assert copied.is_symlink()
    assert os.readlink(copied) == "libfoo.so.1"


def test_copy_preserves_directory_mode(copy_initramfs, tmp_path):
    source = tmp_path / "source"
    source.mkdir(mode=0o700)
    targetroot = tmp_path / "target"
    (targetroot / "usr").mkdir(parents=True)

    copy_initramfs.copy(str(source), "/usr/private", str(targetroot))

    copied = targetroot / "usr/private"
    assert copied.is_dir()
    assert stat.S_IMODE(copied.stat().st_mode) == 0o700


def test_copy_preserves_the_executable_bit_of_a_regular_file(copy_initramfs, tmp_path):
    source = tmp_path / "example"
    source.write_bytes(b"#!/bin/sh\n")
    source.chmod(0o755)
    targetroot = tmp_path / "target"
    (targetroot / "usr/bin").mkdir(parents=True)

    copy_initramfs.copy(str(source), "/usr/bin/example", str(targetroot))

    copied = targetroot / "usr/bin/example"
    assert copied.read_bytes() == b"#!/bin/sh\n"
    assert stat.S_IMODE(copied.stat().st_mode) == 0o755


def test_copy_is_a_no_op_when_the_destination_exists(copy_initramfs, tmp_path):
    source = tmp_path / "example"
    source.write_bytes(b"new")
    targetroot = tmp_path / "target"
    (targetroot / "usr/bin").mkdir(parents=True)
    (targetroot / "usr/bin/example").write_bytes(b"old")

    copy_initramfs.copy(str(source), "/usr/bin/example", str(targetroot))

    assert (targetroot / "usr/bin/example").read_bytes() == b"old"


# ---------------------------------------------------------------------------
# get_dependencies dispatch
# ---------------------------------------------------------------------------


def test_get_dependencies_yields_the_absolute_target_of_a_relative_symlink(
    copy_initramfs, tmp_path
):
    (tmp_path / "usr/lib").mkdir(parents=True)
    (tmp_path / "usr/lib/libfoo.so.1").write_bytes(b"")
    link = tmp_path / "usr/lib/libfoo.so"
    link.symlink_to("libfoo.so.1")

    assert list(copy_initramfs.get_dependencies(str(link), None, None, None)) == [
        str((tmp_path / "usr/lib").resolve() / "libfoo.so.1")
    ]


def test_get_dependencies_passes_an_absolute_symlink_through(copy_initramfs, tmp_path):
    link = tmp_path / "sh"
    link.symlink_to("/usr/bin/bash")

    assert list(copy_initramfs.get_dependencies(str(link), None, None, None)) == [
        "/usr/bin/bash"
    ]


def test_get_dependencies_yields_nothing_for_a_directory(copy_initramfs, tmp_path):
    assert list(copy_initramfs.get_dependencies(str(tmp_path), None, None, None)) == []


@pytest.mark.parametrize(
    "extension",
    [".service", ".socket", ".mount", ".automount", ".path", ".slice", ".target", ".timer"],
)
def test_get_dependencies_parses_every_systemd_unit_extension(
    copy_initramfs, tmp_path, extension
):
    unit = tmp_path / f"example{extension}"
    unit.write_text("[Unit]\nWants=other.service\n")
    resolver = RecordingUnitResolver()

    result = list(copy_initramfs.get_dependencies(str(unit), None, None, resolver))

    assert result == ["unit:other.service"]


def test_get_dependencies_reads_any_other_file_as_a_binary(copy_initramfs, tmp_path):
    """Anything that is not a link, a directory or a unit goes through the
    magic-number dispatch; an unrecognised format contributes nothing."""
    binary = tmp_path / "example"
    binary.write_bytes(b"not a known format")

    assert list(copy_initramfs.get_dependencies(str(binary), None, None, None)) == []


# ---------------------------------------------------------------------------
# ELF-driven dependency extraction (exercised through fakes)
# ---------------------------------------------------------------------------


class FakeSection:
    def __init__(self, data=b"", notes=()):
        self._data = data
        self._notes = list(notes)

    def data(self):
        return self._data

    def iter_notes(self):
        return iter(self._notes)


class FakeELFFile:
    def __init__(self, sections=None, segments=()):
        self._sections = sections or {}
        self._segments = list(segments)

    def get_section_by_name(self, name):
        return self._sections.get(name)

    def iter_segments(self, type=None):
        return iter(self._segments)


class FakeInterpSegment:
    def __init__(self, name):
        self._name = name

    def get_interp_name(self):
        return self._name


class RecordingModuleResolver:
    def resolve_module(self, name):
        return f"module:{name.decode()}"

    def resolve_firmware(self, path):
        return f"firmware:{path.decode()}"


def test_get_dependencies_interp_yields_the_program_interpreter(copy_initramfs):
    elffile = FakeELFFile(segments=[FakeInterpSegment("/usr/lib64/ld-linux.so.2")])

    assert list(copy_initramfs.get_dependencies_interp(elffile)) == [
        "/usr/lib64/ld-linux.so.2"
    ]


def test_get_dependencies_libs_without_a_dynamic_section(copy_initramfs):
    assert list(copy_initramfs.get_dependencies_libs(FakeELFFile(), None)) == []


def test_get_dependencies_libs_ignores_a_section_of_the_wrong_type(copy_initramfs):
    """A `.dynamic` section that is not a DynamicSection carries no DT_NEEDED tags."""
    elffile = FakeELFFile({".dynamic": FakeSection()})

    assert list(copy_initramfs.get_dependencies_libs(elffile, None)) == []


class FakeDynamicTag:
    def __init__(self, needed):
        self.needed = needed


class FakeDynamicSection(FakeSection):
    def __init__(self, needed):
        super().__init__()
        self._needed = list(needed)

    def iter_tags(self, type=None):
        return iter(FakeDynamicTag(name) for name in self._needed)


@pytest.fixture
def dynamic_section_type(copy_initramfs):
    """Make the fake dynamic section pass the script's isinstance() check."""
    import elftools.elf.dynamic

    FakeDynamicSection.__bases__ = (elftools.elf.dynamic.DynamicSection, FakeSection)
    return FakeDynamicSection


def test_get_dependencies_libs_resolves_every_dt_needed_entry(
    copy_initramfs, tmp_path, dynamic_section_type
):
    (tmp_path / "usr/lib").mkdir(parents=True)
    (tmp_path / "usr/lib/libc.so.6").write_bytes(b"")
    resolver = copy_initramfs.LibraryResolver(str(tmp_path), ["/usr/lib"])
    elffile = FakeELFFile(
        {".dynamic": dynamic_section_type(["libc.so.6", "libmissing.so.1"])}
    )

    assert list(copy_initramfs.get_dependencies_libs(elffile, resolver)) == [
        str(tmp_path / "usr/lib/libc.so.6"),
        str(tmp_path / "usr/lib/libmissing.so.1"),
    ]


def test_get_dependencies_dlopen_ignores_a_section_of_the_wrong_type(copy_initramfs):
    elffile = FakeELFFile({".note.dlopen": FakeSection()})

    assert list(copy_initramfs.get_dependencies_dlopen(elffile, None)) == []


def test_get_dependencies_modules_resolves_depends_and_firmware(copy_initramfs):
    modinfo = FakeSection(data=b"depends=core,helper\0firmware=amdgpu/navi.bin\0")
    elffile = FakeELFFile({".modinfo": modinfo})

    assert list(
        copy_initramfs.get_dependencies_modules(elffile, RecordingModuleResolver())
    ) == ["module:core", "module:helper", "firmware:amdgpu/navi.bin"]


def test_get_dependencies_modules_ignores_an_empty_depends_entry(copy_initramfs):
    elffile = FakeELFFile({".modinfo": FakeSection(data=b"depends=\0license=GPL\0")})

    assert (
        list(copy_initramfs.get_dependencies_modules(elffile, RecordingModuleResolver()))
        == []
    )


def test_get_dependencies_modules_without_a_modinfo_section(copy_initramfs):
    assert list(copy_initramfs.get_dependencies_modules(FakeELFFile(), None)) == []


def _dlopen_note(features):
    import json

    return {
        "n_type": 0x407C0C0A,
        "n_name": "FDO",
        "n_desc": (json.dumps(features) + "\0").encode("utf-8"),
    }


class FakeNoteSection(FakeSection):
    pass


@pytest.fixture
def note_section_type(copy_initramfs):
    """Make the fake note section pass the script's isinstance() check."""
    import elftools.elf.sections

    FakeNoteSection.__bases__ = (elftools.elf.sections.NoteSection, FakeSection)
    return FakeNoteSection


def test_get_dependencies_dlopen_without_a_note_section(copy_initramfs):
    assert list(copy_initramfs.get_dependencies_dlopen(FakeELFFile(), None)) == []


def test_get_dependencies_dlopen_yields_the_first_resolvable_soname(
    copy_initramfs, tmp_path, note_section_type
):
    (tmp_path / "usr/lib").mkdir(parents=True)
    (tmp_path / "usr/lib/libsecond.so.1").write_bytes(b"")
    resolver = copy_initramfs.LibraryResolver(str(tmp_path), ["/usr/lib"])
    note = _dlopen_note(
        [{"feature": "audio", "soname": ["libfirst.so.1", "libsecond.so.1"]}]
    )
    elffile = FakeELFFile({".note.dlopen": note_section_type(notes=[note])})

    assert list(copy_initramfs.get_dependencies_dlopen(elffile, resolver)) == [
        str(tmp_path / "usr/lib/libsecond.so.1")
    ]


def test_get_dependencies_dlopen_raises_when_no_soname_resolves(
    copy_initramfs, tmp_path, note_section_type
):
    resolver = copy_initramfs.LibraryResolver(str(tmp_path), ["/usr/lib"])
    note = _dlopen_note(
        [{"feature": "audio", "description": "sound support", "soname": ["libgone.so"]}]
    )
    elffile = FakeELFFile({".note.dlopen": note_section_type(notes=[note])})

    with pytest.raises(copy_initramfs.MissingFeature, match="audio: sound support"):
        list(copy_initramfs.get_dependencies_dlopen(elffile, resolver))


def test_get_dependencies_dlopen_skips_features_named_in_the_ignore_list(
    copy_initramfs, tmp_path, monkeypatch, note_section_type
):
    monkeypatch.setenv("DLOPEN_NOTE_IGNORE", "audio:video")
    resolver = copy_initramfs.LibraryResolver(str(tmp_path), ["/usr/lib"])
    note = _dlopen_note([{"feature": "audio", "soname": ["libgone.so"]}])
    elffile = FakeELFFile({".note.dlopen": note_section_type(notes=[note])})

    assert list(copy_initramfs.get_dependencies_dlopen(elffile, resolver)) == []


def test_get_dependencies_dlopen_ignores_notes_from_other_vendors(
    copy_initramfs, note_section_type
):
    foreign = {"n_type": 0x1, "n_name": "GNU", "n_desc": b"\0"}
    elffile = FakeELFFile({".note.dlopen": note_section_type(notes=[foreign])})

    assert list(copy_initramfs.get_dependencies_dlopen(elffile, None)) == []


# ---------------------------------------------------------------------------
# magic-number dispatch
# ---------------------------------------------------------------------------


def test_get_dependencies_file_dispatches_on_the_elf_magic(copy_initramfs, monkeypatch):
    seen = {}

    def fake_elf(file, module_resolver, library_resolver):
        seen["called"] = file.read()
        yield "elf-dependency"

    monkeypatch.setattr(copy_initramfs, "get_dependencies_elf", fake_elf)

    result = list(
        copy_initramfs.get_dependencies_file(io.BytesIO(b"\x7fELFrest"), None, None)
    )

    assert result == ["elf-dependency"]
    assert seen["called"] == b"\x7fELFrest"


def test_get_dependencies_file_dispatches_on_the_zstd_magic(copy_initramfs, monkeypatch):
    import zstd

    monkeypatch.setattr(zstd, "decompress", lambda data: b"\x7fELFinner")
    monkeypatch.setattr(
        copy_initramfs,
        "get_dependencies_elf",
        lambda file, module_resolver, library_resolver: iter(["inner-dependency"]),
    )

    result = list(
        copy_initramfs.get_dependencies_file(
            io.BytesIO(b"\x28\xb5\x2f\xfdpayload"), None, None
        )
    )

    assert result == ["inner-dependency"]


def test_get_dependencies_file_dispatches_on_the_xz_magic(copy_initramfs, monkeypatch):
    import lzma

    compressed = lzma.compress(b"\x7fELFinner", format=lzma.FORMAT_XZ)
    monkeypatch.setattr(
        copy_initramfs,
        "get_dependencies_elf",
        lambda file, module_resolver, library_resolver: iter(["inner-dependency"]),
    )

    result = list(
        copy_initramfs.get_dependencies_file(io.BytesIO(compressed), None, None)
    )

    assert result == ["inner-dependency"]


def test_get_dependencies_file_yields_nothing_for_an_unknown_format(copy_initramfs):
    assert list(copy_initramfs.get_dependencies_file(io.BytesIO(b"plain"), None, None)) == []
