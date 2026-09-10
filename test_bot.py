from datetime import date

from bot import Talk, format_talks, parse_rows, week_start


def test_parse_rows_and_sorting():
    talks = parse_rows([
        ["Title", "Date", "Speaker"],
        ["Later", "2026-09-17", "B"],
        ["Earlier", "2026-09-14", "A"],
    ])
    assert [talk.title for talk in talks] == ["Earlier", "Later"]


def test_parse_custom_column_names_and_short_date():
    talks = parse_rows([
        ["Dates", "Name", "Talk Title", "Talk Abstract"],
        ["09/09", "A. Researcher", "A Talk", "An abstract"],
    ])
    assert talks[0].title == "A Talk"
    assert talks[0].speaker == "A. Researcher"
    assert talks[0].description == "An abstract"


def test_week_start():
    assert week_start(date(2026, 9, 16)) == date(2026, 9, 14)


def test_format_empty():
    assert format_talks([], "Talks") == "Talks\n\nNo talks found."
