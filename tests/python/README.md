# Python unit tests

Fast, hermetic unit tests for the Python helper scripts in this repository.
They need nothing but a Python interpreter and `pytest` — no BuildStream
sandbox, no image build, no network.

```sh
python3 -m pytest tests/python
```

With coverage for the script under test:

```sh
python3 -m coverage run --source=files/kde-linux-system/generate-initramfs \
    -m pytest tests/python
python3 -m coverage report -m
```

## Conventions

- Scripts live outside any importable package and some have hyphens in their
  filenames, so `conftest.py` loads them by path via `importlib`.
- Third-party modules that only exist inside the build sandbox (`pyelftools`,
  `zstd`) are replaced with minimal stand-ins in `conftest.py`. Tests that need
  a real ELF reader belong in an integration suite, not here.
- A test marked `xfail(strict=True)` asserts the behaviour the script *should*
  have and tracks a known defect; it turns into a failure once the defect is
  fixed, which is the signal to drop the marker.

These tests are separate from `tests/openqa`, which drives a booted image and
requires a built disk image to run.
