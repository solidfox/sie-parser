"""Tests for #RTRANS (supplementary) and #BTRANS (removed) transaction rows.

SIE 4B: an #RTRANS row is always immediately followed by an identical #TRANS
copy for older readers. A reader that handles #RTRANS must skip that copy, so
the row is counted once. #BTRANS rows are removed rows and never count toward
the voucher balance.
"""

import os
import sys
from collections import defaultdict

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
    assert _rows(vouchers["A3"]) == [("TRANS", "6250", 300.0), ("TRANS", "1910", -300.0)]
    # A #TRANS that does not immediately follow the #RTRANS is a normal row.
    assert _rows(vouchers["A4"]) == [
        ("TRANS", "1910", -100.0),
        ("RTRANS", "6250", 60.0),
        ("TRANS", "6250", 40.0),
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
    # #RTRANS/#BTRANS outside a voucher block are ignored.
    assert all(e.amount != 999.0 for e in result.entries + result.removed_entries)

    for rows in vouchers.values():
        assert sum(e.amount for e in rows) == 0
    assert _rows(result.removed_entries) == [
        ("BTRANS", "6110", 60.0),
        ("BTRANS", "6110", 250.0),
        ("BTRANS", "1910", -250.0),
    ]


def test_plain_trans_default_record_type():
    """Existing files without #RTRANS/#BTRANS are unchanged."""
    result = sie_parser.parse_sie_file(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_sie4.se")
    )
    assert result.entries
    assert all(e.record_type == "TRANS" for e in result.entries)
    assert result.removed_entries == []


def test_new_field_keeps_positional_sie_file_arguments():
    """removed_entries is the last SieFile field, so positional construction is unchanged."""
    fields = list(sie_parser.SieFile.__dataclass_fields__)
    assert fields[-1] == "removed_entries"
    assert fields.index("opening_balances") == fields.index("entries") + 1
