"""Collection-time guard: intentionally not a delayed pytest fixture."""
from tests.v00_isolation import bootstrap

ISOLATION = bootstrap()


import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    config.option.basetemp = str(ISOLATION['runtime'] / 'tmp' / 'pytest')
    config.option.log_file = str(ISOLATION['runtime'] / 'pytest.log')


@pytest.fixture(autouse=True)
def isolated_test_temp():
    # Some existing tests remove the parent of their mkdtemp directory.
    # Give each test its own disposable parent; never share pytest's base.
    import os
    import tempfile
    import uuid
    parent = ISOLATION['runtime'] / 'tmp' / uuid.uuid4().hex
    parent.mkdir(parents=True)
    previous_tempdir = tempfile.tempdir
    previous_env = {key: os.environ.get(key) for key in ('TEMP', 'TMP', 'TMPDIR')}
    tempfile.tempdir = str(parent)
    for key in previous_env:
        os.environ[key] = str(parent)
    try:
        yield
    finally:
        tempfile.tempdir = previous_tempdir
        for key, value in previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
