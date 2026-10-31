from datetime import date

from app.extract import Extraction, RawEvent, Roster, Speaker, check_deadline, locate_excerpt, validate_events
from app.transcribe import Turn

SAT = date(2026, 10, 31)


def turns():
    return [
        Turn(0, "A", "Okay, quick quick sync. I'll send the specs to facilities by Friday.", 0, 1000),
        Turn(1, "B", "We could consider contacting the vendor vendor.", 1000, 2000),
    ]


def test_excerpt_found_despite_asr_stutter_and_punctuation():
    assert locate_excerpt("Okay, quick sync. I'll send the specs", turns(), claimed=0) == 0


def test_excerpt_found_in_other_turn_corrects_the_index():
    assert locate_excerpt("contacting the vendor", turns(), claimed=0) == 1


def test_invented_excerpt_is_not_found():
    assert locate_excerpt("I will definitely ship the specs on Monday", turns(), claimed=0) is None


def test_plain_weekday_resolves_forward():
    assert check_deadline("by Friday", "2026-11-06", "explicit", SAT) == (date(2026, 11, 6), "explicit")


def test_weekday_that_disagrees_with_date_is_rejected():
    assert check_deadline("by Friday", "2026-11-05", "explicit", SAT) == (None, "none")


def test_date_before_meeting_is_rejected():
    assert check_deadline("", "2026-10-01", "explicit", SAT) == (None, "none")


def test_date_over_a_year_out_is_rejected():
    assert check_deadline("", "2028-01-01", "explicit", SAT) == (None, "none")


def test_garbage_date_is_rejected():
    assert check_deadline("", "next-friday", "explicit", SAT) == (None, "none")


def test_date_without_basis_is_marked_inferred():
    assert check_deadline("", "2026-11-04", "none", SAT) == (date(2026, 11, 4), "inferred")


def test_ambiguous_phrase_with_two_weekdays_is_not_weekday_checked():
    assert check_deadline("Monday or Tuesday", "2026-11-03", "inferred", SAT)[0] == date(2026, 11, 3)


def test_validate_events_uses_diarization_speaker_and_flags_unverified():
    raw = Extraction(
        meeting_project="MidTown Community Dashboard",
        speakers=[Speaker(label="A", name="Dana", confidence="high", basis="opens meeting"),
                  Speaker(label="B", name="Sam", confidence="low", basis="guess")],
        events=[
            RawEvent(type="commitment", turn_index=1, owner="dana", deliverable="specs", project_hint="",
                     deadline_text="by Friday", deadline_date="2026-11-06", deadline_basis="explicit",
                     excerpt="I'll send the specs to facilities by Friday"),
            RawEvent(type="suggestion", turn_index=1, owner="", deliverable="vendor contact", project_hint="",
                     deadline_text="", deadline_date="", deadline_basis="none", excerpt="We could ship it all tomorrow"),
        ],
    )
    rows = validate_events(raw, turns(), SAT, Roster(people=["Dana", "Sam"], projects=[]))
    first, second = rows
    assert first["turn_index"] == 0 and first["speaker_name"] == "Dana" and first["owner_name"] == "Dana"
    assert first["excerpt_verified"] and first["deadline_date"] == date(2026, 11, 6)
    assert not second["excerpt_verified"]
    assert second["speaker_name"] is None  # low-confidence name is not trusted
    assert second["owner_name"] is None


def test_empty_deliverable_is_dropped():
    raw = Extraction(meeting_project="unknown", speakers=[], events=[
        RawEvent(type="progress", turn_index=0, owner="", deliverable="  ", project_hint="", deadline_text="",
                 deadline_date="", deadline_basis="none", excerpt="quick sync")])
    assert validate_events(raw, turns(), SAT, Roster([], [])) == []
