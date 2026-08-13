"""Google format requests must remain valid for present-but-empty tables."""

from almanac_write import _color_scale_request


def test_empty_data_range_does_not_emit_an_invalid_conditional_rule():
    assert _color_scale_request(
        7,
        3,
        10,
        row_ranges=[{"startRowIndex": 5, "endRowIndex": 5}],
    ) is None


def test_nonempty_data_range_keeps_the_requested_sheet_and_column():
    request = _color_scale_request(
        7,
        3,
        10,
        row_ranges=[{"startRowIndex": 5, "endRowIndex": 8}],
    )

    ranges = request["addConditionalFormatRule"]["rule"]["ranges"]
    assert ranges == [{
        "sheetId": 7,
        "startRowIndex": 5,
        "endRowIndex": 8,
        "startColumnIndex": 3,
        "endColumnIndex": 4,
    }]
