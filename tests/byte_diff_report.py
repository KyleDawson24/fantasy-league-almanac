"""Line-level diagnostics shared by the private byte-diff corpora."""
from itertools import zip_longest
from pathlib import Path


def describe_drift(name, actual: bytes, expected: bytes, limit=25) -> str:
    """Name moved lines, retaining byte-only differences such as line endings."""
    tab = Path(name).stem
    if actual == expected:
        return f'{tab}: no differing lines'
    try:
        a_lines = actual.decode('utf-8').splitlines(keepends=True)
        e_lines = expected.decode('utf-8').splitlines(keepends=True)
    except UnicodeDecodeError:
        offset = next(i for i, pair in enumerate(zip_longest(actual, expected))
                      if pair[0] != pair[1])
        return f'{tab}: byte {offset} differs (not UTF-8)'
    moved = [(i, a, e) for i, (a, e) in enumerate(
        zip_longest(a_lines, e_lines), start=1) if a != e]
    visible = ', '.join(str(i) for i, _, _ in moved[:limit])
    extra = f' (+{len(moved)-limit} more)' if len(moved) > limit else ''
    header = (f'{tab}: lines {visible}{extra} '
              f'(actual {len(a_lines)} lines, expected {len(e_lines)} lines)')
    pairs = []
    for i, a, e in moved[:3]:
        pairs.append(f'  line {i} expected: {e!r}' if e is not None
                     else f'  line {i} expected: <EOF>')
        pairs.append(f'  line {i} actual:   {a!r}' if a is not None
                     else f'  line {i} actual:   <EOF>')
    return '\n'.join([header, *pairs])
