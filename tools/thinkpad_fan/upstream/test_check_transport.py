"""Input/output examples for the pinned-source function extractor."""
import pytest

from tools.thinkpad_fan.upstream.check_transport import extract


def test_extract_exact_definition_with_nested_blocks():
    expected = """static bool
ReadExample(int value) {
    if (value) {
        return true;
    }
    return false;
}"""
    source = "void ReadExampleOther() {}\n" + expected + "\nvoid Next() {}\n"
    assert extract(source, "ReadExample") == expected


def test_extract_nonstatic_function_ignores_call_site():
    expected = "UCHAR\nReadByte(int port) { return port; }"
    source = "void Caller() { ReadByte(1); }\n" + expected + "\n"
    assert extract(source, "ReadByte") == expected


def test_missing_definition_is_rejected():
    with pytest.raises(ValueError, match="missing definition: ReadByte"):
        extract("void Caller() { ReadByte(1); }", "ReadByte")
