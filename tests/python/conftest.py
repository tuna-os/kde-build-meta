"""Shared fixtures for the pure-Python unit tests.

The scripts under test live outside any importable package and carry hyphens in
their filenames, so they are loaded by path. Some of them import third-party
modules (pyelftools, zstd) that are only present inside the BuildStream sandbox;
those imports are satisfied with minimal stand-ins so that the pure logic can be
exercised on a plain Python interpreter.
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _install_elftools_stub():
    """Provide the small slice of pyelftools that copy-initramfs.py touches."""
    if "elftools" in sys.modules:
        return

    elftools = types.ModuleType("elftools")
    elf = types.ModuleType("elftools.elf")
    elffile = types.ModuleType("elftools.elf.elffile")
    dynamic = types.ModuleType("elftools.elf.dynamic")
    sections = types.ModuleType("elftools.elf.sections")

    class ELFFile:  # pragma: no cover - only referenced through monkeypatching
        def __init__(self, stream):
            self.stream = stream

    class DynamicSection:
        pass

    class NoteSection:
        pass

    elffile.ELFFile = ELFFile
    dynamic.DynamicSection = DynamicSection
    sections.NoteSection = NoteSection

    elf.elffile = elffile
    elf.dynamic = dynamic
    elf.sections = sections
    elftools.elf = elf

    sys.modules.update(
        {
            "elftools": elftools,
            "elftools.elf": elf,
            "elftools.elf.elffile": elffile,
            "elftools.elf.dynamic": dynamic,
            "elftools.elf.sections": sections,
        }
    )


def _install_zstd_stub():
    if "zstd" in sys.modules:
        return
    zstd = types.ModuleType("zstd")

    def decompress(data):  # pragma: no cover - replaced per test
        raise NotImplementedError

    zstd.decompress = decompress
    sys.modules["zstd"] = zstd


def load_script(relative_path, module_name):
    """Import a stand-alone script by path under an arbitrary module name."""
    path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def copy_initramfs():
    _install_elftools_stub()
    _install_zstd_stub()
    return load_script(
        "files/kde-linux-system/generate-initramfs/copy-initramfs.py",
        "copy_initramfs_under_test",
    )
