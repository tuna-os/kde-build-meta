"""Hermetic unit tests for files/kde-linux-system/save-perms/save-perms.py.

The script has no `if __name__ == '__main__':` guard: parsing argv and
running retrieve()/apply() happens unconditionally at module scope. To
import it at all, argv must already point at a valid (empty) root and a
writable backup path, which is what `save_perms_module` sets up before
each test — the import's own retrieve() call is a no-op against an empty
directory and its result is discarded; every test below calls the
functions directly against its own fixtures instead.
"""

import importlib.util
import json
import os
import stat
import sys

import pytest

MODULE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..",
    "files", "kde-linux-system", "save-perms", "save-perms.py",
)


@pytest.fixture
def save_perms_module(tmp_path, monkeypatch):
    empty_root = tmp_path / "import-root"
    empty_root.mkdir()
    backup = tmp_path / "import-backup.json"
    monkeypatch.setattr(sys, "argv", ["save-perms.py", str(backup), str(empty_root)])
    spec = importlib.util.spec_from_file_location("save_perms", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_retrieve_one_records_nondefault_file_mode(save_perms_module, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    target = root / "bin"
    target.write_text("x")
    os.chmod(target, 0o700)

    doc = {}
    save_perms_module.retrieve_one(doc, str(root), "bin")

    assert _without_selinux(doc) == {"bin": {"mode": 0o700}}


@pytest.mark.parametrize("mode", [0o755, 0o644])
def test_retrieve_one_skips_default_file_modes(save_perms_module, tmp_path, mode):
    root = tmp_path / "root"
    root.mkdir()
    target = root / "f"
    target.write_text("x")
    os.chmod(target, mode)

    doc = {}
    save_perms_module.retrieve_one(doc, str(root), "f")

    assert _without_selinux(doc) == {}


def test_retrieve_one_records_nondefault_dir_mode(save_perms_module, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    sub = root / "d"
    sub.mkdir()
    os.chmod(sub, 0o700)

    doc = {}
    save_perms_module.retrieve_one(doc, str(root), "d")

    assert _without_selinux(doc) == {"d": {"mode": 0o700}}


def test_retrieve_one_skips_default_dir_mode(save_perms_module, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    sub = root / "d"
    sub.mkdir(mode=0o755)
    os.chmod(sub, 0o755)

    doc = {}
    save_perms_module.retrieve_one(doc, str(root), "d")

    assert _without_selinux(doc) == {}


def test_retrieve_one_returns_none_for_non_reg_non_dir(save_perms_module, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    target = root / "f"
    target.write_text("x")
    link = root / "link"
    link.symlink_to(target)

    doc = {}
    result = save_perms_module.retrieve_one(doc, str(root), "link")

    assert result is None
    assert doc == {}


def _xattr_supported(path):
    try:
        os.setxattr(path, "user.save_perms_probe", b"1")
        os.removexattr(path, "user.save_perms_probe")
        return True
    except OSError:
        return False


def _without_selinux(doc):
    # The sandbox filesystem auto-labels every new inode with a
    # security.selinux xattr, which retrieve_one() legitimately records —
    # strip it so assertions only check the attributes the test set up.
    stripped = {}
    for rel, entry in doc.items():
        entry = dict(entry)
        attrs = {k: v for k, v in entry.get("attributes", {}).items() if k != "security.selinux"}
        if attrs:
            entry["attributes"] = attrs
        elif "attributes" in entry:
            del entry["attributes"]
        if entry:
            stripped[rel] = entry
    return stripped


def test_retrieve_one_records_xattrs(save_perms_module, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    target = root / "f"
    target.write_text("x")
    os.chmod(target, 0o644)
    if not _xattr_supported(target):
        pytest.skip("filesystem does not support user xattrs")
    os.setxattr(target, "user.save_perms_test", b"\x01\x02")

    doc = {}
    save_perms_module.retrieve_one(doc, str(root), "f")

    assert _without_selinux(doc) == {"f": {"attributes": {"user.save_perms_test": "0102"}}}


def test_apply_one_restores_mode(save_perms_module, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    target = root / "f"
    target.write_text("x")
    os.chmod(target, 0o644)

    doc = {"f": {"mode": 0o600}}
    save_perms_module.apply_one(doc, str(root), "f")

    assert stat.S_IMODE(os.lstat(target).st_mode) == 0o600


def test_apply_one_restores_xattrs(save_perms_module, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    target = root / "f"
    target.write_text("x")
    if not _xattr_supported(target):
        pytest.skip("filesystem does not support user xattrs")

    doc = {"f": {"attributes": {"user.save_perms_test": "0102"}}}
    save_perms_module.apply_one(doc, str(root), "f")

    assert os.getxattr(target, "user.save_perms_test") == b"\x01\x02"


def test_apply_one_is_noop_when_rel_missing_from_doc(save_perms_module, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    target = root / "f"
    target.write_text("x")
    os.chmod(target, 0o644)

    save_perms_module.apply_one({}, str(root), "f")

    assert stat.S_IMODE(os.lstat(target).st_mode) == 0o644


def test_retrieve_walks_tree_and_skips_symlinks(save_perms_module, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    sub = root / "sub"
    sub.mkdir()
    os.chmod(sub, 0o700)
    f = sub / "f"
    f.write_text("x")
    os.chmod(f, 0o600)
    link_dir = root / "link_dir"
    link_dir.symlink_to(sub)
    link_file = root / "link_file"
    link_file.symlink_to(f)

    doc = save_perms_module.retrieve(str(root))

    assert _without_selinux(doc) == {
        "sub": {"mode": 0o700},
        os.path.join("sub", "f"): {"mode": 0o600},
    }


def test_apply_walks_tree_and_skips_symlinks(save_perms_module, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    sub = root / "sub"
    sub.mkdir()
    f = sub / "f"
    f.write_text("x")
    os.chmod(sub, 0o755)
    os.chmod(f, 0o644)
    link = root / "link"
    link.symlink_to(f)
    link_dir = root / "link_dir"
    link_dir.symlink_to(sub)

    doc = {
        "sub": {"mode": 0o700},
        os.path.join("sub", "f"): {"mode": 0o600},
    }
    save_perms_module.apply(doc, str(root))

    assert stat.S_IMODE(os.lstat(sub).st_mode) == 0o700
    assert stat.S_IMODE(os.lstat(f).st_mode) == 0o600
    # the symlinks themselves must never be touched (os.chmod would follow
    # them without follow_symlinks=False support on this platform, or raise)
    assert os.path.islink(link)
    assert os.path.islink(link_dir)


def test_retrieve_then_apply_round_trip(save_perms_module, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    d = src / "d"
    d.mkdir()
    os.chmod(d, 0o700)
    f = d / "f"
    f.write_text("payload")
    os.chmod(f, 0o600)

    doc = _without_selinux(save_perms_module.retrieve(str(src)))

    dst = tmp_path / "dst"
    (dst / "d").mkdir(parents=True)
    (dst / "d" / "f").write_text("payload")
    os.chmod(dst / "d", 0o755)
    os.chmod(dst / "d" / "f", 0o644)

    save_perms_module.apply(doc, str(dst))

    assert stat.S_IMODE(os.lstat(dst / "d").st_mode) == 0o700
    assert stat.S_IMODE(os.lstat(dst / "d" / "f").st_mode) == 0o600


def test_cli_restore_mode_round_trips_through_json(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    f = src / "f"
    f.write_text("x")
    os.chmod(f, 0o600)

    backup = tmp_path / "backup.json"
    monkeypatch.setattr(sys, "argv", ["save-perms.py", str(backup), str(src)])
    spec = importlib.util.spec_from_file_location("save_perms_retrieve_cli", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    with open(backup) as fh:
        on_disk = json.load(fh)
    assert _without_selinux(on_disk) == {"f": {"mode": 0o600}}

    # rewrite without the sandbox's auto-assigned selinux label: the
    # destination file below gets its own label, and XATTR_CREATE would
    # otherwise raise FileExistsError trying to set it a second time.
    with open(backup, "w") as fh:
        json.dump(_without_selinux(on_disk), fh)

    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "f").write_text("x")
    os.chmod(dst / "f", 0o644)

    monkeypatch.setattr(sys, "argv", ["save-perms.py", "--restore", str(backup), str(dst)])
    spec2 = importlib.util.spec_from_file_location("save_perms_restore_cli", MODULE_PATH)
    module2 = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(module2)

    assert stat.S_IMODE(os.lstat(dst / "f").st_mode) == 0o600
