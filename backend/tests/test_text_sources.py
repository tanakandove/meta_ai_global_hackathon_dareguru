from app.ingest import parse_text_turns
from app.reconcile import _strength


def test_named_lines_become_named_turns():
    turns = parse_text_turns("Dana: I'll send it Friday.\nSam: Sounds good.", "transcript")
    assert [(t.speaker, t.text) for t in turns] == [("Dana", "I'll send it Friday."), ("Sam", "Sounds good.")]


def test_unlabelled_lines_get_a_generic_speaker():
    assert [t.speaker for t in parse_text_turns("Quick update on the install.", "text")] == ["speaker"]


def test_email_is_one_turn_from_its_sender_with_the_subject():
    mail = "From: Priya Shah <priya@example.com>\nSubject: Cabling plan\nAttachments: plan_v3.pdf\n\nHi all,\nthe plan is attached."
    (turn,) = parse_text_turns(mail, "email")
    assert turn.speaker == "Priya Shah" and turn.text.startswith("Subject: Cabling plan.") and "plan is attached" in turn.text


def test_only_an_email_carrying_an_attachment_makes_a_completion_verified():
    email = {"kind": "email", "raw_text": "From: P\nAttachments: plan_v3.pdf\n\nDone."}
    no_file = {"kind": "email", "raw_text": "From: P\n\nDone, I sent it."}
    meeting = {"kind": "audio", "raw_text": "Attachments: plan_v3.pdf"}
    assert _strength(email, "completion") == "verified"
    assert _strength(no_file, "completion") == "claimed"
    assert _strength(meeting, "completion") == "claimed"   # a spoken claim is never verified
    assert _strength(email, "progress") == "claimed"       # only completion can be verified
