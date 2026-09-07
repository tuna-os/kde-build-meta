import importlib.util
import itertools
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_load_counter = itertools.count()


def load_script(rel_path, argv):
    """Load a hyphenated CLI script by path, executing it with a seeded sys.argv.

    Several scripts in this repo parse argv (or otherwise act) at module
    scope rather than behind an `if __name__ == "__main__":` guard, so the
    caller's argv must be set before exec, and each call gets a fresh module
    object (unique name, not registered in sys.modules) so tests don't
    observe state left over from a previous run.
    """
    old_argv = sys.argv
    sys.argv = argv
    try:
        name = f"{pathlib.Path(rel_path).stem.replace('-', '_')}_{next(_load_counter)}"
        spec = importlib.util.spec_from_file_location(name, REPO_ROOT / rel_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.argv = old_argv
