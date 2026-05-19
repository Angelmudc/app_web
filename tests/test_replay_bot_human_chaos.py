from scripts.local.replay_bot_human_chaos import _has_valid_progress_signal, _is_noise_message


def test_replay_noise_message_not_treated_as_valid_signal():
    assert _is_noise_message("hola hola hola") is True
    assert _has_valid_progress_signal("hola hola hola") is False


def test_replay_valid_signal_detected():
    assert _has_valid_progress_signal("si soy yo") is True
    assert _has_valid_progress_signal("quiero trabajar") is True
    assert _has_valid_progress_signal("me llamo carmen y tengo 30 años") is True
