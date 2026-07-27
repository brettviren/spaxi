import logging

import pytest

from spaxi import log


def teardown_function():
    # Leave the shared "spaxi" logger clean for other tests.
    logger = logging.getLogger(log.ROOT)
    logger.handlers.clear()
    logger.setLevel(logging.NOTSET)


def test_setup_level_and_sink_stderr():
    log.setup_logging("stderr", "debug")
    logger = logging.getLogger(log.ROOT)
    assert logger.level == logging.DEBUG
    assert len(logger.handlers) == 1
    assert isinstance(logger.handlers[0], logging.StreamHandler)


def test_setup_level_is_case_insensitive():
    log.setup_logging("stderr", "WARNING")
    assert logging.getLogger(log.ROOT).level == logging.WARNING


def test_repeated_setup_does_not_stack_handlers():
    log.setup_logging("stderr", "info")
    log.setup_logging("stderr", "info")
    assert len(logging.getLogger(log.ROOT).handlers) == 1


def test_file_sink(tmp_path):
    path = tmp_path / "spaxi.log"
    log.setup_logging(str(path), "debug")
    logging.getLogger("spaxi.demo").debug("hello world")
    for handler in logging.getLogger(log.ROOT).handlers:
        handler.flush()
    assert "hello world" in path.read_text()


def test_bad_level_raises():
    with pytest.raises(log.LogError):
        log.setup_logging("stderr", "loud")
