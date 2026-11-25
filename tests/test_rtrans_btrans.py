"""Tests for #RTRANS (supplementary) and #BTRANS (removed) transaction rows.

SIE 4B: an #RTRANS row is always immediately followed by an identical #TRANS
copy for older readers. A reader that handles #RTRANS must skip that copy, so
the row is counted once. #BTRANS rows are removed rows and never count toward
the voucher balance.
"""

import io
import os
import sys
from collections import defaultdict

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sie_parser

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _parse(name):
    return sie_parser.parse_sie_file(os.path.join(FIXTURES, name))


def _by_voucher(entries):
    vouchers = defaultdict(list)
    for entry in entries:
        vouchers[entry.voucher_index].append(entry)
    return vouchers


def _rows(entries):
    return [(e.record_type, e.account_number, e.amount) for e in entries]


def test_rtrans_copy_not_counted_twice():
    """Spec example: three #RTRANS rows, each followed by its #TRANS copy."""
    vouchers = _by_voucher(_parse("rtrans_btrans_valid.se").entries)
    assert _rows(vouchers["A1"]) == [
        ("TRANS", "1910", -1200.0),
        ("TRANS", "2640", 240.0),
        ("TRANS", "6250", 960.0),
        ("RTRANS", "1910", 200.0),
        ("RTRANS", "2640", -40.0),
        ("RTRANS", "6250", -160.0),
    ]
    assert sum(e.amount for e in vouchers["A1"]) == 0


def test_btrans_kept_out_of_entries():
    """Spec example: #BTRANS row is exposed in removed_entries, not in entries."""
    result = _parse("rtrans_btrans_valid.se")
    vouchers = _by_voucher(result.entries)
    assert _rows(vouchers["A2"]) == [
        ("TRANS", "1910", -1000.0),
        ("TRANS", "2640", 200.0),
        ("RTRANS", "6250", 800.0),
    ]
    assert sum(e.amount for e in vouchers["A2"]) == 0
    assert _rows(result.removed_entries) == [("BTRANS", "6110", 800.0)]
    removed = result.removed_entries[0]
    assert removed.voucher_index == "A2"
    assert removed.date == "20080102"
    assert removed.description == "Postage"


def test_rtrans_btrans_edge_cases():
    """Blank lines, missing or differently written copies, unknown labels, removed-only voucher."""
    result = _parse("rtrans_btrans_edge.se")
    vouchers = _by_voucher(result.entries)

    # Blank lines between #RTRANS and its copy: the copy is still skipped.
    assert _rows(vouchers["A1"]) == [("TRANS", "1910", -800.0), ("RTRANS", "6250", 800.0)]
    # #RTRANS as the last row without a copy still counts ...
    assert _rows(vouchers["A2"]) == [("TRANS", "1910", -500.0), ("RTRANS", "6250", 500.0)]
    # ... and the first #TRANS of the next voucher is not swallowed as a copy.
    assert _rows(vouchers["A3"]) == [("TRANS", "6250", 500.0), ("TRANS", "1910", -500.0)]
    # A #TRANS that does not immediately follow the #RTRANS is a normal row.
    assert _rows(vouchers["A4"]) == [
        ("TRANS", "1910", -120.0),
        ("RTRANS", "6250", 60.0),
        ("TRANS", "6250", 60.0),
    ]
    # A voucher with only removed rows has no effective entries.
    assert "A5" not in vouchers
    # The copy is recognised by account and amount, whatever the spacing or number format.
    assert _rows(vouchers["A6"]) == [("RTRANS", "6250", 200.0), ("TRANS", "1910", -200.0)]
    # A #TRANS right after an #RTRANS that is not its copy is kept.
    assert _rows(vouchers["A7"]) == [("RTRANS", "6250", 700.0), ("TRANS", "1910", -700.0)]
    # An unknown label that starts with "#RTRANS" is ignored and arms no copy-skip.
    assert _rows(vouchers["A8"]) == [("TRANS", "6250", 100.0), ("TRANS", "1910", -100.0)]
    # Two #RTRANS rows in a row: only the #TRANS right after the second is its copy.
    assert _rows(vouchers["A9"]) == [
        ("TRANS", "1910", -30.0),
        ("RTRANS", "6250", 10.0),
        ("RTRANS", "6250", 20.0),
    ]
    # A quoted account number still identifies the copy, and the kept #RTRANS row
    # lands on the unquoted account, as the #TRANS copy would have.
    assert _rows(vouchers["A10"]) == [("TRANS", "1910", -15.0), ("RTRANS", "6250", 15.0)]
    # The kept #RTRANS row takes its copy's account spelling, as main counted the copy.
    assert _rows(vouchers["A12"]) == [("TRANS", "1910", -35.0), ("RTRANS", '"6250"', 35.0)]
    # A quoted copy of an unquoted #RTRANS is skipped once; an identical row after it is kept.
    assert _rows(vouchers["A13"]) == [
        ("RTRANS", '"6250"', 40.0),
        ("TRANS", '"6250"', 40.0),
        ("TRANS", "1910", -80.0),
    ]
    # A "}" inside a quoted object ID does not end the object list.
    assert _rows(vouchers["A14"]) == [("RTRANS", "6250", 50.0), ("TRANS", "1910", -50.0)]
    # Object IDs whose quotes do not pair up still parse, as on main.
    assert _rows(vouchers["A15"]) == [
        ("TRANS", "6110", 100.0),
        ("TRANS", "6110", 100.0),
        ("TRANS", "6110", 100.0),
        ("TRANS", "6110", 100.0),
        ("RTRANS", "6250", 30.0),
        ("TRANS", "1910", -430.0),
    ]
    # A "}" in a quoted object ID, followed by a quote or a number, is not the end
    # of the list when no amount follows it.
    assert _rows(vouchers["A16"]) == [
        ("RTRANS", "6250", 60.0),
        ("TRANS", "6110", 40.0),
        ("TRANS", "1910", -100.0),
    ]
    # Quoted amounts parse, and the copy is still recognised.
    assert _rows(vouchers["A11"]) == [("TRANS", "1910", -25.0), ("RTRANS", "6250", 25.0)]
    # #RTRANS/#BTRANS outside a voucher block are ignored.
    assert all(e.amount != 999.0 for e in result.entries + result.removed_entries)

    for rows in vouchers.values():
        assert sum(e.amount for e in rows) == 0
    assert _rows(result.removed_entries) == [
        ("BTRANS", "6110", 60.0),
        ("BTRANS", "6110", 250.0),
        ("BTRANS", "1910", -250.0),
        ("BTRANS", "6110", 25.0),
        ("BTRANS", "6110", 800.0),
        ("BTRANS", "6110", 800.0),
        ("BTRANS", "6110", 700.0),
    ]


@pytest.mark.parametrize("amount", ['""', '"', '100"', '"100'])
def test_amount_with_unpaired_quote_is_rejected(amount):
    """Only a matched pair of quotes is removed from an amount, so these fail as on main."""
    src = '#VER A 1 20080101 "x"\n{\n#BTRANS 6110 {} ' + amount + '\n}\n'
    with pytest.raises(sie_parser.SieParseError):
        sie_parser.parse_sie(io.StringIO(src))


@pytest.mark.parametrize("row", [
    '#BTRANS 6110 {1 "A}B\\\\"} 700.00',
    '#BTRANS 6110 {1 "A}B\\\\"} 700.00 20080116 "ref} 7 x"',
])
def test_unclosed_quote_in_object_list_is_rejected(row):
    """A quote escaped with a backslash does not close the ID, so no amount is guessed."""
    src = '#VER A 1 20080101 "x"\n{\n' + row + '\n}\n'
    with pytest.raises(sie_parser.SieParseError):
        sie_parser.parse_sie(io.StringIO(src))


def test_plain_trans_default_record_type():
    """Existing files without #RTRANS/#BTRANS are unchanged."""
    result = sie_parser.parse_sie_file(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_sie4.se")
    )
    # Same rows as the parser before #RTRANS/#BTRANS support (origin/main).
    assert [(e.voucher_index, e.account_number, e.amount) for e in result.entries] == [
        ("A1", "1910", -1000.0), ("A1", "2440", 200.0), ("A1", "4010", 800.0),
        ("A2", "3010", 1250.0), ("A2", "2441", 250.0), ("A2", "1510", -1500.0),
        ("A3", "1920", 1500.0), ("A3", "1510", -1500.0),
        ("A4", "2610", -800.0), ("A4", "2440", -200.0), ("A4", "1920", 1000.0),
        ("A5", "5010", -5000.0), ("A5", "2731", -1250.0), ("A5", "2732", 1250.0),
        ("A5", "1920", 5000.0),
        ("A6", "8010", 1000.0), ("A6", "8020", -500.0), ("A6", "8050", -200.0),
        ("A6", "1920", -300.0),
    ]
    assert all(e.record_type == "TRANS" for e in result.entries)
    assert result.removed_entries == []


def test_new_field_keeps_positional_sie_file_arguments():
    """removed_entries is the last SieFile field, so positional construction is unchanged."""
    fields = list(sie_parser.SieFile.__dataclass_fields__)
    assert fields[-1] == "removed_entries"
    assert fields.index("opening_balances") == fields.index("entries") + 1
