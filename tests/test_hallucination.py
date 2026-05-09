"""Tests for the hallucination detector and worker recovery helpers."""

from app.transcription import _is_hallucination


def test_empty_text_not_hallucination():
    assert _is_hallucination("") is False


def test_short_clean_text_not_hallucination():
    assert _is_hallucination("Hej, jeg kommer hjem nu.") is False


def test_known_phrase_alone():
    assert _is_hallucination("Tak for at se med") is True


def test_known_phrase_repeated():
    assert _is_hallucination("Tak for at se med. Tak for at se med.") is True


def test_consecutive_ngram_repeat():
    assert _is_hallucination("ja ja ja ja ja ja ja ja") is True


def test_thanks_for_watching():
    assert _is_hallucination("Thanks for watching") is True


def test_amara_credit():
    assert _is_hallucination("Subtitles by the Amara.org community") is True


def test_normal_long_text():
    text = (
        "Hej skat, jeg er hjemme nu. Hvordan har du haft det i dag? "
        "Jeg tænkte vi kunne lave aftensmad sammen klokken syv."
    )
    assert _is_hallucination(text) is False
