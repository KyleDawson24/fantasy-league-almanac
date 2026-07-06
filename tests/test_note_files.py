"""Pure tests for the optional header/footer note files (note_files.py)."""

import note_files


class TestNoteFiles:
    def test_missing_files_contribute_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(note_files, '_OUTPUT_DIR', str(tmp_path))

        assert note_files.header_lines() == []
        assert note_files.footer_lines() == []

    def test_blank_file_contributes_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(note_files, '_OUTPUT_DIR', str(tmp_path))
        (tmp_path / 'leagueNoteHeader.txt').write_text('   \n', encoding='utf-8')

        assert note_files.header_lines() == []

    def test_header_prepends_content_then_blank_line(self, tmp_path, monkeypatch):
        monkeypatch.setattr(note_files, '_OUTPUT_DIR', str(tmp_path))
        (tmp_path / 'leagueNoteHeader.txt').write_text(
            'Happy All-Star Break!', encoding='utf-8')

        assert note_files.header_lines() == ['Happy All-Star Break!', '']

    def test_footer_appends_blank_line_then_content(self, tmp_path, monkeypatch):
        monkeypatch.setattr(note_files, '_OUTPUT_DIR', str(tmp_path))
        (tmp_path / 'leagueNoteFooter.txt').write_text(
            'See you in the second half.', encoding='utf-8')

        assert note_files.footer_lines() == ['', 'See you in the second half.']
