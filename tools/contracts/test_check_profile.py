"""Tests for the parser and normaliser in check_profile.

The regexes decide whether a mirror counts as matching, so each one is pinned to
a concrete input and output here. A test that only asserts "both sides ran" would
pass on a normaliser that returns '' for everything.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from check_profile import normalise, strip_header  # noqa: E402


class TestStripHeader:
    def test_drops_everything_up_to_and_including_separator(self):
        text = (
            "# MEMORY.md — draft\n"
            "\n"
            "> Draft, not the live file.\n"
            "\n"
            "---\n"
            "\n"
            "Форумы: 1F916\n"
        )
        assert strip_header(text).strip() == "Форумы: 1F916"

    def test_no_separator_returns_text_unchanged(self):
        """A mirror that lost its header must not compare equal to empty text."""
        text = "Форумы: 1F916\n"
        assert strip_header(text) == text

    def test_separator_with_surrounding_spaces(self):
        text = "header\n  ---  \nbody\n"
        assert strip_header(text).strip() == "body"

    def test_crlf_input(self):
        text = "# draft\r\n\r\n---\r\n\r\nbody\r\n"
        assert strip_header(text).strip() == "body"

    def test_first_separator_wins(self):
        """Content may legitimately contain ---; only the header separator is dropped."""
        text = "header\n---\nfirst\n---\nsecond\n"
        assert strip_header(text).strip() == "first\n---\nsecond"


class TestNormalise:
    def test_crlf_to_lf(self):
        assert normalise("a\r\nb") == "a\nb"

    def test_trailing_spaces_removed(self):
        assert normalise("a  \nb\t\n") == "a\nb"

    def test_space_runs_collapsed(self):
        assert normalise("a    b") == "a b"

    def test_blank_line_runs_collapsed(self):
        assert normalise("a\n\n\n\nb") == "a\n\nb"

    def test_leading_and_trailing_whitespace_stripped(self):
        assert normalise("\n  a  \n") == "a"

    def test_formatting_only_differences_are_equal(self):
        """The property the check depends on: same words, different whitespace."""
        mirror = "Форумы: 1F916\r\n§\r\nPVE 9.2  \r\n"
        live = "Форумы: 1F916\n§\nPVE 9.2"
        assert normalise(mirror) == normalise(live)

    def test_real_difference_is_not_equal(self):
        """The property that makes the check able to fail at all."""
        assert normalise("Форумы: 1F916") != normalise("Форумы: 1F917")

    def test_empty_input(self):
        assert normalise("") == ""

    def test_whitespace_only_input(self):
        assert normalise(" \r\n\t\n ") == ""
