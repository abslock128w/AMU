import json
import os
import stat

import pytest

from amu import keys


def test_private_before_replace_even_with_open_umask(monkeypatch):
    original = os.replace
    original_fdopen = os.fdopen
    observed = []

    def fdopen(fd, *args, **kwargs):
        assert stat.S_IMODE(os.fstat(fd).st_mode) == 0o600
        return original_fdopen(fd, *args, **kwargs)

    def replace(src, dst, **kwargs):
        info = os.stat(src, dir_fd=kwargs.get('src_dir_fd'))
        observed.append(stat.S_IMODE(info.st_mode))
        assert observed[-1] == 0o600
        return original(src, dst, **kwargs)

    monkeypatch.setattr(os, 'replace', replace)
    monkeypatch.setattr(os, 'fdopen', fdopen)
    previous = os.umask(0)
    try:
        keys.set_key('sk-ant-fake-new')
    finally:
        os.umask(previous)
    assert observed == [0o600]


@pytest.mark.parametrize('operation', ['replace', 'fsync'])
def test_failed_replace_preserves_old_credential(monkeypatch, operation):
    keys.set_key('sk-ant-fake-old')
    path = keys._path()
    before = path.read_bytes()

    def fail(*args, **kwargs):
        raise OSError('simulated interruption')

    monkeypatch.setattr(os, operation, fail)
    with pytest.raises(OSError):
        keys.set_key('sk-ant-fake-new')
    assert path.read_bytes() == before
    assert list(path.parent.iterdir()) == [path]


def test_symlink_credentials_are_not_read_or_overwritten(tmp_path):
    target = tmp_path / 'outside.json'
    target.write_text(json.dumps({'api_key': 'fake-outside'}))
    path = keys._path()
    path.symlink_to(target)
    assert keys._read() == {}
    with pytest.raises((OSError, ValueError)):
        keys.set_key('sk-ant-fake-new')
    assert json.loads(target.read_text())['api_key'] == 'fake-outside'


def test_symlink_home_rejected(monkeypatch, tmp_path):
    target = tmp_path / 'target'
    target.mkdir()
    link = tmp_path / 'linked-home'
    link.symlink_to(target, target_is_directory=True)
    monkeypatch.setenv('AMU_HOME', str(link))
    with pytest.raises((OSError, ValueError)):
        keys.set_key('sk-ant-fake-new')
    assert list(target.iterdir()) == []


@pytest.mark.parametrize('url', ['file:///tmp/key', 'ftp://host', 'https://user:fake-secret@host', 'https://host/?key=fake-secret', 'https://host/#fake-secret', 'not-a-url'])
def test_invalid_endpoint_not_stored_or_echoed(url):
    with pytest.raises(ValueError) as exc:
        keys.set_credential('openai-compatible', base_url=url, model='fake')
    assert 'fake-secret' not in str(exc.value)
    assert not keys._path().exists()


def test_explicit_local_http_supported():
    keys.set_credential('openai-compatible', base_url='http://localhost:1234/v1', model='fake')
    assert keys._read()['base_url'] == 'http://localhost:1234/v1'


def test_new_home_private_under_open_umask(monkeypatch, tmp_path):
    root = tmp_path / 'new-home'
    monkeypatch.setenv('AMU_HOME', str(root))
    old = os.umask(0)
    try:
        keys.set_key('sk-ant-fake-new')
    finally:
        os.umask(old)
    assert stat.S_IMODE(root.stat().st_mode) == 0o700


def test_hardlinked_file_rejected(tmp_path):
    keys.set_key('sk-ant-fake-old')
    path = keys._path()
    os.link(path, tmp_path / 'alias')
    with pytest.raises(ValueError):
        keys.set_key('sk-ant-fake-new')
    assert keys._read() == {}


def test_environment_endpoint_checked_before_client_creation():
    from amu.harness.providers import make_client
    with pytest.raises(ValueError):
        make_client({'family': 'openai', 'base_url': 'file:///tmp/fake'})
