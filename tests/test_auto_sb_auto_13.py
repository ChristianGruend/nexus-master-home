import pytest
from nexus.autobuild.loop import _replace_function

class TestSyntaxErrorHandling:
    def test_invalid_decimal_literal(self):
        """Testet: Parser meldet 'invalid decimal literal' bei ungültigen Zahlen."""
        code = '''def test(): return 3.14.5'''
        with pytest.raises(ValueError) as exc_info:
            _replace_function('', 'test', code)
        err_str = str(exc_info.value)
        # Prüfen, dass der spezifische Fehlerinhalt erhalten bleibt
        assert 'invalid decimal literal' in err_str or 'invalid syntax' in err_str
    
    def test_terminated_string_literal(self):
        """Testet: Parser meldet 'unterminated string literal' bei fehlendem Schließen."""
        code = '''def test(): return "hallo'''
        with pytest.raises(ValueError) as exc_info:
            _replace_function('', 'test', code)
        err_str = str(exc_info.value)
        # Prüfen, dass der Fehlerinhalt erhalten bleibt
        assert 'unterminated string literal' in err_str or 'invalid syntax' in err_str