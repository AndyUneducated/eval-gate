from evalgate import __version__


def test_version_is_set() -> None:
    assert __version__
    assert isinstance(__version__, str)
    assert len(__version__.split(".")) == 3
