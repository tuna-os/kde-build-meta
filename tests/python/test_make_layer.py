import os
import stat
from unittest.mock import patch

from conftest import load_script

SCRIPT = "files/kde-linux-system/make-layer.py"


def run_make_layer(lower, upper, output):
    argv = ["make-layer", str(lower), str(upper), str(output)]
    with patch("os.mknod") as mknod:
        module = load_script(SCRIPT, argv)
    return module, mknod


def test_copies_new_file(tmp_path):
    lower, upper, output = tmp_path / "lower", tmp_path / "upper", tmp_path / "output"
    lower.mkdir()
    upper.mkdir()
    (upper / "new.txt").write_text("hello")

    run_make_layer(lower, upper, output)

    assert (output / "new.txt").read_text() == "hello"


def test_unchanged_file_not_copied(tmp_path):
    lower, upper, output = tmp_path / "lower", tmp_path / "upper", tmp_path / "output"
    lower.mkdir()
    upper.mkdir()
    (lower / "same.txt").write_text("hello")
    (upper / "same.txt").write_text("hello")
    same_stat = os.stat(lower / "same.txt")
    os.utime(upper / "same.txt", ns=(same_stat.st_atime_ns, same_stat.st_mtime_ns))

    run_make_layer(lower, upper, output)

    assert not (output / "same.txt").exists()


def test_changed_file_content_is_copied(tmp_path):
    lower, upper, output = tmp_path / "lower", tmp_path / "upper", tmp_path / "output"
    lower.mkdir()
    upper.mkdir()
    (lower / "changed.txt").write_text("old")
    (upper / "changed.txt").write_text("new-content")
    # Force identical size/mtime so compare_files must fall through to the
    # byte-for-byte comparison instead of short-circuiting on stat alone.
    lower_stat = os.stat(lower / "changed.txt")
    os.truncate(upper / "changed.txt", lower_stat.st_size)
    os.utime(upper / "changed.txt", ns=(lower_stat.st_atime_ns, lower_stat.st_mtime_ns))

    run_make_layer(lower, upper, output)

    assert (output / "changed.txt").read_text() == "new"


def test_new_directory_is_copied_with_parents(tmp_path):
    lower, upper, output = tmp_path / "lower", tmp_path / "upper", tmp_path / "output"
    lower.mkdir()
    upper.mkdir()
    (upper / "a" / "b").mkdir(parents=True)
    (upper / "a" / "b" / "leaf.txt").write_text("x")

    run_make_layer(lower, upper, output)

    assert (output / "a").is_dir()
    assert (output / "a" / "b").is_dir()
    assert (output / "a" / "b" / "leaf.txt").read_text() == "x"


def test_directory_replacing_a_lower_symlink_is_copied(tmp_path):
    lower, upper, output = tmp_path / "lower", tmp_path / "upper", tmp_path / "output"
    lower.mkdir()
    upper.mkdir()
    (lower / "target").mkdir()
    (lower / "x").symlink_to("target")
    (upper / "x").mkdir()

    run_make_layer(lower, upper, output)

    assert (output / "x").is_dir()
    assert not (output / "x").is_symlink()


def test_new_symlinked_directory_entry_is_copied_as_a_link(tmp_path):
    # A symlink-to-a-directory in `dirs` (as os.walk reports it) must be
    # treated as a link, not descended into as a real directory.
    lower, upper, output = tmp_path / "lower", tmp_path / "upper", tmp_path / "output"
    lower.mkdir()
    upper.mkdir()
    (upper / "real").mkdir()
    (upper / "real" / "inside.txt").write_text("x")
    (upper / "linkdir").symlink_to("real")

    run_make_layer(lower, upper, output)

    assert (output / "linkdir").is_symlink()
    assert os.readlink(output / "linkdir") == "real"


def test_new_symlink_is_copied(tmp_path):
    lower, upper, output = tmp_path / "lower", tmp_path / "upper", tmp_path / "output"
    lower.mkdir()
    upper.mkdir()
    (upper / "link").symlink_to("/some/target")

    run_make_layer(lower, upper, output)

    assert os.readlink(output / "link") == "/some/target"


def test_symlink_with_changed_target_is_copied(tmp_path):
    lower, upper, output = tmp_path / "lower", tmp_path / "upper", tmp_path / "output"
    lower.mkdir()
    upper.mkdir()
    (lower / "link").symlink_to("/old/target")
    (upper / "link").symlink_to("/new/target")

    run_make_layer(lower, upper, output)

    assert os.readlink(output / "link") == "/new/target"


def test_symlink_with_unchanged_target_not_copied(tmp_path):
    lower, upper, output = tmp_path / "lower", tmp_path / "upper", tmp_path / "output"
    lower.mkdir()
    upper.mkdir()
    (lower / "link").symlink_to("/same/target")
    (upper / "link").symlink_to("/same/target")

    run_make_layer(lower, upper, output)

    assert not (output / "link").exists()


def test_whiteout_created_for_file_removed_in_upper(tmp_path):
    lower, upper, output = tmp_path / "lower", tmp_path / "upper", tmp_path / "output"
    lower.mkdir()
    upper.mkdir()
    (lower / "gone.txt").write_text("bye")

    _, mknod = run_make_layer(lower, upper, output)

    mknod.assert_called_once()
    (called_path,), kwargs = mknod.call_args
    assert called_path == str(output / "gone.txt")
    assert kwargs["mode"] == stat.S_IFCHR | 0o600
    assert kwargs["device"] == os.makedev(0, 0)


def test_whiteout_not_created_for_children_of_a_removed_directory(tmp_path):
    lower, upper, output = tmp_path / "lower", tmp_path / "upper", tmp_path / "output"
    lower.mkdir()
    upper.mkdir()
    (lower / "sub").mkdir()
    (lower / "sub" / "f").write_text("bye")

    _, mknod = run_make_layer(lower, upper, output)

    # Only the removed top-level "sub" directory gets a whiteout; its child
    # is skipped because make-layer.py only whiteouts entries whose parent
    # still exists on the upper side (`output/sub` never exists here).
    mknod.assert_called_once()
    (called_path,), _ = mknod.call_args
    assert called_path == str(output / "sub")


def test_compare_files_detects_stat_difference_without_reading_content(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.write_text("same content")
    b.write_text("same content")
    os.utime(b, ns=(0, 0))

    module = load_script(SCRIPT, ["make-layer", str(tmp_path), str(tmp_path), str(tmp_path / "out")])

    assert module.compare_files(str(a), str(b)) is False


def test_compare_files_true_for_identical_stat_and_content(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.write_text("identical")
    b.write_text("identical")
    a_stat = os.stat(a)
    os.utime(b, ns=(a_stat.st_atime_ns, a_stat.st_mtime_ns))

    module = load_script(SCRIPT, ["make-layer", str(tmp_path), str(tmp_path), str(tmp_path / "out")])

    assert module.compare_files(str(a), str(b)) is True
