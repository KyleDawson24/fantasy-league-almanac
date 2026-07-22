"""output/note_files.py

Optional commissioner note files that bracket every summary output --
regular weekly recaps and milestone editions alike:

    leagueNoteHeader.txt   printed verbatim as the very first lines
    leagueNoteFooter.txt   printed verbatim as the very last lines

Both live in output/ next to LeagueNote.txt and are gitignored the same
way. A missing or blank file contributes nothing (no header, no blank
line), so scripts and goldens are unaffected until the commissioner
writes one. This is the mechanism for one-off flavor -- e.g. an
All-Star-break intro on the season report -- without any calendar-aware
code.
"""

import os

_OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))

HEADER_FILE = 'leagueNoteHeader.txt'
FOOTER_FILE = 'leagueNoteFooter.txt'


def read_note(filename):
    """Stripped contents of an optional note file; '' when missing/blank.

    Setting SUPPRESS_LEAGUE_NOTES=1 makes every note read empty. The note
    files are gitignored local state, so a golden baseline rendered on a
    machine that has them can never match one rendered on a machine that
    doesn't -- the goldens set this so they pin the recap engine rather
    than whatever flavor the commissioner is running this week.
    """
    if os.environ.get('SUPPRESS_LEAGUE_NOTES') == '1':
        return ''
    path = os.path.join(_OUTPUT_DIR, filename)
    if not os.path.exists(path):
        return ''
    with open(path, 'r', encoding='utf-8') as f:
        return f.read().strip()


def header_lines():
    """Lines to prepend to a summary: the header note + a separating
    blank line, or nothing at all."""
    content = read_note(HEADER_FILE)
    return [content, ''] if content else []


def footer_lines():
    """Lines to append to a summary: a separating blank line + the footer
    note, or nothing at all."""
    content = read_note(FOOTER_FILE)
    return ['', content] if content else []
