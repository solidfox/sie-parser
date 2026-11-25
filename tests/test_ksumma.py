"""Tests for #KSUMMA control summation (SIE 4B section 10)."""

import os
import sys
import zlib

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sie_parser

TESTS = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(TESTS, "fixtures")


def _parse(name):
    return sie_parser.parse_sie_file(os.path.join(FIXTURES, name))


def _spec_crc(data: bytes) -> int:
    """Direct port of the spec's C code (CRC_skapa_tabell / CRC_ackumulera / CRC_retur)."""
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = (crc >> 1) ^ 0xEDB88320 if crc & 1 else crc >> 1
        table.append(crc)
    crc = 0xFFFFFFFF
    for byte in data:
        crc = ((crc >> 8) & 0x00FFFFFF) ^ table[(crc ^ byte) & 0xFF]
    return crc ^ 0xFFFFFFFF


def test_spec_example_characters():
    """Spec example: only the label, the fields and the escaped quotation marks count."""
    assert sie_parser._ksumma_text('#KONTO 1915 "Kassa \\"special\\""') == '#KONTO1915Kassa "special"'


def test_separators_quotes_and_braces_excluded():
    """Tabs/spaces between fields, field quotes and object-list braces are not summed."""
    line = '#TRANS\t6250  {"6" "47"} 800.00 20240115 "Frimärken" 1 ""'
    assert sie_parser._ksumma_text(line) == '#TRANS6250647800.0020240115Frimärken1'
    assert sie_parser._ksumma_text('{') == ''
    assert sie_parser._ksumma_text('}') == ''
    # Spaces inside a quoted field are part of the field.
    assert sie_parser._ksumma_text('#FNAMN "A B"') == '#FNAMNA B'


def test_escaped_quotes_and_braces_inside_field_values():
    """Only the quote of a backslash-escaped quote counts, quoted field or not.
    Braces count unless they delimit an object list or a voucher block."""
    assert sie_parser._ksumma_text('#KONTO 1915 Kassa\\"special\\"') == '#KONTO1915Kassa"special"'
    assert sie_parser._ksumma_text('#KONTO 1910 Kassa{A}') == '#KONTO1910Kassa{A}'
    assert sie_parser._ksumma_text('#KONTO 1910 {Kassa}') == '#KONTO1910{Kassa}'
    assert sie_parser._ksumma_text('#OIB 0 1510 {8 "12345"} 23780.78') == '#OIB01510812345' + '23780.78'
    assert sie_parser._ksumma_text('#TRANS 1910 {} -100 20240115 {Postage}') == '#TRANS1910-10020240115{Postage}'
    assert sie_parser._ksumma_text('  }') == ''
    # The list ends at a '}' that ends a field; one inside an unquoted object ID is text.
    assert sie_parser._ksumma_text('#OIB 0 1510 {8 AB}CD} 23780.78') == '#OIB015108AB}CD23780.78'
    assert sie_parser._ksumma_text('#TRANS 1910 {1 "A"}') == '#TRANS19101A'


@pytest.mark.parametrize("content", [
    # Inside the covered items: would be summed as one item but parsed as two
    '#FLAGGA 0\n#KSUMMA\n#FNAMN Ka\x0bssa\n#KSUMMA ' + str(zlib.crc32(b"#FNAMNKassa")) + '\n',
    # On the #FLAGGA line: would smuggle an uncovered #KONTO past the layout check
    '#FLAGGA 0\x0b#KONTO 9999 Injected\n#KSUMMA\n#FNAMN X\n#KSUMMA ' + str(zlib.crc32(b"#FNAMNX")) + '\n',
    '#FLAGGA 0\n#KSUMMA\n#FNAMN X\n#KSUMMA ' + str(zlib.crc32(b"#FNAMNX")) + '\x1c#KONTO 9999 X\n',
    # A trailing one, which str.splitlines() alone would not reveal
    '#FLAGGA 0\x0b\n#KSUMMA\n#FNAMN X\n#KSUMMA ' + str(zlib.crc32(b"#FNAMNX")) + '\n',
])
def test_line_breaking_control_characters_rejected(content):
    """The parser splits lines at control characters such as a vertical tab; a checksummed
    file with one is rejected, so no item escapes the control sum."""
    from io import StringIO
    with pytest.raises(sie_parser.SieParseError, match="Control character splits an item"):
        sie_parser.parse_sie(StringIO(content))


def test_unterminated_quote_in_covered_item_rejected():
    """A lost closing quote does not change the summed bytes, so it is rejected outright."""
    from io import StringIO
    content = '#FLAGGA 0\n#KSUMMA\n#FNAMN "X\n#KSUMMA ' + str(zlib.crc32(b"#FNAMNX")) + '\n'
    with pytest.raises(sie_parser.SieParseError, match="unterminated quoted field"):
        sie_parser.parse_sie(StringIO(content))


def test_unclosed_object_list_in_covered_item_rejected():
    """A lost closing object-list brace does not change the summed bytes either."""
    from io import StringIO
    content = ('#FLAGGA 0\n#KSUMMA\n#OIB 0 1510 {8 "12345" 23780.78\n#KSUMMA '
               + str(zlib.crc32(b"#OIB0151081234523780.78")) + '\n')
    with pytest.raises(sie_parser.SieParseError, match="unclosed object list"):
        sie_parser.parse_sie(StringIO(content))


def test_quotes_inside_a_field_count():
    """A quote only delimits a field at its start; inside a field it is text and counts,
    so `Kas""sa` and `Kassa` sum differently."""
    assert sie_parser._ksumma_text('#KONTO 1910 Kas""sa') == '#KONTO1910Kas""sa'
    assert sie_parser._ksumma_text('#FNAMN abc"de f') == '#FNAMNabc"def'
    # Inside an object list a quote opens a field only right after the list's own brace.
    assert sie_parser._ksumma_text('#TRANS 1910 {1 a{""b} 100') == '#TRANS19101a{""b100'
    assert sie_parser._ksumma_text('#TRANS 1910 {"1" "a"} 100') == '#TRANS19101a100'


def test_item_split_over_lines_rejected():
    """A line break between fields does not change the summed bytes, but the parser
    would lose the item, so a covered line without an item label is rejected."""
    from io import StringIO
    for split, summed in [('#KONTO\n1910 "Kassa"', b"#KONTO1910Kassa"),
                          ('#OBJEKT 1\n{ "Kassa"', b"#OBJEKT1{Kassa")]:
        content = '#FLAGGA 0\n#KSUMMA\n' + split + '\n#KSUMMA ' + str(zlib.crc32(summed)) + '\n'
        with pytest.raises(sie_parser.SieParseError, match="Item without a label"):
            sie_parser.parse_sie(StringIO(content))


def test_zlib_matches_spec_algorithm():
    data = "#KONTO1915Kassa \"special\"åäö".encode("cp437")
    assert zlib.crc32(data) == _spec_crc(data)


def test_valid_control_sum_accepted():
    result = _parse("ksumma_valid.se")
    assert result.control_sum == 4053613742
    assert result.company_name == "Målerifirman Åkesson & Öberg AB"
    assert sum(e.amount for e in result.entries) == 0


def test_tampered_file_rejected():
    """Amounts changed but the voucher still balances: only the control sum catches it."""
    with pytest.raises(sie_parser.SieParseError, match="control sum mismatch"):
        _parse("ksumma_tampered.se")


def test_truncated_file_rejected():
    """Introductory #KSUMMA without a final control sum: the file is truncated."""
    with pytest.raises(sie_parser.SieParseError, match="missing at end of file"):
        _parse("ksumma_truncated.se")


def test_item_after_final_ksumma_rejected():
    """Items after the final #KSUMMA are not protected by the control sum."""
    with pytest.raises(sie_parser.SieParseError, match="after the final #KSUMMA"):
        _parse("ksumma_trailing_item.se")


def test_file_without_ksumma_accepted():
    """The control sum is optional: files without #KSUMMA parse as before."""
    result = sie_parser.parse_sie_file(os.path.join(TESTS, "test_sie4.se"))
    assert result.control_sum is None


def _with_sum(value_field):
    body = '#FNAMN "X"\n'
    return "#FLAGGA 0\n#KSUMMA\n" + body + "#KSUMMA " + value_field.format(zlib.crc32(b"#FNAMNX")) + "\n"


def test_quoted_control_sum_value_accepted():
    """SIE fields may be quoted, the final control sum too."""
    from io import StringIO
    assert sie_parser.parse_sie(StringIO(_with_sum('{}'))).control_sum == zlib.crc32(b"#FNAMNX")
    assert sie_parser.parse_sie(StringIO(_with_sum('"{}"'))).control_sum == zlib.crc32(b"#FNAMNX")
    # The spec sets no field width: zero padding is still the same number.
    assert sie_parser.parse_sie(StringIO(_with_sum('00{}'))).control_sum == zlib.crc32(b"#FNAMNX")


def test_items_before_opening_ksumma_rejected():
    """Only #FLAGGA may come before the opening #KSUMMA: other items would not be covered."""
    from io import StringIO
    content = '#IB 0 1910 999.00\n' + _with_sum('{}')
    with pytest.raises(sie_parser.SieParseError, match="Line 1: Items before the opening #KSUMMA"):
        sie_parser.parse_sie(StringIO(content))


@pytest.mark.parametrize("content, message", [
    ("#FLAGGA 0\n#KSUMMA\n#FNAMN \"X\"\n#KSUMMA \u00b2\n", "Malformed #KSUMMA"),
    ("#FLAGGA 0\n#KSUMMA\n#FNAMN \"X\"\n#KSUMMA 4294967296\n", "Malformed #KSUMMA"),
    ("#FLAGGA 0\n#KSUMMA\n#FNAMN \"X\"\n#KSUMMA 0004294967296\n", "Malformed #KSUMMA"),
    # Long zero padding is a well-formed (wrong) value, not a crash in int().
    ("#FLAGGA 0\n#KSUMMA\n#FNAMN \"X\"\n#KSUMMA " + "0" * 5000 + "1\n", "control sum mismatch"),
    # A no-break space (CP437 0xFF) is not an SIE field separator.
    ("#FLAGGA 0\n#KSUMMA\n#FNAMN \"X\"\n#KSUMMA " + str(zlib.crc32(b"#FNAMNX")) + "\u00a0\n",
     "Malformed #KSUMMA"),
    ("#FLAGGA 0\n#KSUMMA 123\n#FNAMN \"X\"\n", "first #KSUMMA must have no value"),
    # A lone final #KSUMMA: the opening one (and what came after it) may have been cut off.
    ("#FLAGGA 0\n#FNAMN \"X\"\n#KSUMMA " + str(zlib.crc32(b"#FNAMNX")) + "\n", "first #KSUMMA must have no value"),
    ("#FLAGGA 0\n#KSUMMA\n#FNAMN \"X\"\n#KSUMMA abc\n", "Malformed #KSUMMA"),
    ("#FLAGGA 0\n#KSUMMA\n#FNAMN \"X\"\n#KSUMMA 1\n#KSUMMA 2\n", "Malformed #KSUMMA"),
])
def test_malformed_ksumma_rejected(content, message):
    from io import StringIO
    with pytest.raises(sie_parser.SieParseError, match=message):
        sie_parser.parse_sie(StringIO(content))
