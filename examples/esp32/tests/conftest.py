"""Locations of prebuilt native C executables used by integration tests."""
from pathlib import Path

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--native-build-dir",
        default=str(Path(__file__).resolve().parents[1] / "build-native"),
        help="ESP32 host CMake build directory containing cgp_host and mock_samples",
    )


@pytest.fixture(scope="session")
def native_build_dir(pytestconfig):
    return Path(pytestconfig.getoption("--native-build-dir")).resolve()


def require_executable(directory, name):
    executable = directory / name
    if not executable.is_file():
        pytest.fail(
            f"Missing native C executable: {executable}. "
            "Build examples/esp32/tests with CMake first; "
            "see examples/esp32/README.md#native-checks. "
            "Use --native-build-dir for a different build directory.",
            pytrace=False,
        )
    return executable


@pytest.fixture(scope="session")
def cgp_host(native_build_dir):
    return require_executable(native_build_dir, "cgp_host")


@pytest.fixture(scope="session")
def mock_samples(native_build_dir):
    return require_executable(native_build_dir, "mock_samples")
