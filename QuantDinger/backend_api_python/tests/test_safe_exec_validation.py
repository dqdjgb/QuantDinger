from app.services.builtin_indicators import _MACD_CODE
from app.utils.safe_exec import validate_code_safety


def test_macd_signal_line_variable_is_not_rejected_as_signal_module():
    is_safe, error = validate_code_safety(_MACD_CODE)

    assert is_safe, error


def test_import_signal_remains_rejected():
    is_safe, error = validate_code_safety("import signal\nsignal.alarm(1)\n")

    assert not is_safe
    assert error
