"""Unit tests for pure transcription logic (no network, no DB)."""


from app.transcription import _is_hallucination


class TestIsHallucination:
    def test_empty_string(self):
        assert _is_hallucination("") is False

    def test_short_text(self):
        assert _is_hallucination("hej") is False

    def test_normal_sentence(self):
        assert _is_hallucination("Hej, hvordan har du det i dag?") is False

    def test_normal_long_sentence(self):
        text = (
            "Det var en dejlig dag i skoven, fuglene sang og solen skinnede klart. "
            "Vi gik en lang tur og talte om mange ting."
        )
        assert _is_hallucination(text) is False

    def test_bigram_loop_detected(self):
        # "tak tak" repeating 5 times
        assert _is_hallucination("tak tak tak tak tak tak tak tak tak tak") is True

    def test_trigram_loop_detected(self):
        assert _is_hallucination(
            "mange tak mange tak mange tak mange tak mange tak"
        ) is True

    def test_four_word_loop_detected(self):
        phrase = "det ved jeg ikke"
        text = " ".join([phrase] * 5)
        assert _is_hallucination(text) is True

    def test_loop_fewer_than_four_repeats_not_flagged(self):
        # Only 3 repeats — should NOT be flagged
        assert _is_hallucination("tak tak tak tak tak tak") is False

    def test_boundary_exactly_four_repeats_flagged(self):
        # 2-gram "tak tak" repeated 4 times = 8 words
        text = "tak tak tak tak tak tak tak tak"
        assert _is_hallucination(text) is True

    def test_text_with_mixed_case(self):
        # Detection is case-insensitive
        assert _is_hallucination("HAR DU HAR DU HAR DU HAR DU HAR DU") is True

    def test_realistic_hallucination(self):
        # Typical Whisper hallucination on silence
        text = (
            "Tak for din opmærksomhed. Tak for din opmærksomhed. "
            "Tak for din opmærksomhed. Tak for din opmærksomhed."
        )
        assert _is_hallucination(text) is True
