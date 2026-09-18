from tests.byte_diff_report import describe_drift


def test_identical():
    assert describe_drift('Records.tsv', b'a\n', b'a\n') == 'Records: no differing lines'


def test_changed_line():
    report = describe_drift('Records.tsv', b'a\nc\n', b'a\nb\n')
    assert 'Records: lines 2 ' in report
    assert "expected: 'b\\n'" in report
    assert "actual:   'c\\n'" in report


def test_missing_lines():
    report = describe_drift('Home.tsv', b'a\n', b'a\nb\nc\n')
    assert 'Home: lines 2, 3 ' in report
    assert 'actual:   <EOF>' in report


def test_byte_only_line_endings_are_not_hidden():
    assert 'lines 1 ' in describe_drift('Home.tsv', b'a\r\n', b'a\n')
    assert 'lines 1 ' in describe_drift('Home.tsv', b'a', b'a\n')


def test_truncates_line_list_and_handles_non_utf8():
    assert 'lines 1, 2 (+1 more)' in describe_drift('Home.tsv', b'x\nx\nx', b'a\na\na', limit=2)
    assert 'byte 1 differs' in describe_drift('Home.tsv', b'a\xff', b'a\xfe')
