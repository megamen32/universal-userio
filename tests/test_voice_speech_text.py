"""Spoken incident text keeps its content: nothing technical is dropped.

The owner reads the incident from a phone call. A dropped error code, URL or
identifier is a lost diagnosis, so the speech text carries every character
through: Latin is transliterated, punctuation becomes a word, and a three-digit
code is read digit by digit.
"""
import importlib.util
from pathlib import Path
import sys


PATH = Path(__file__).resolve().parents[1] / 'src/universal_userio/voice/agentcall_realtime_bridge.py'
spec = importlib.util.spec_from_file_location('speech_text_bridge', PATH)
bridge = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bridge
spec.loader.exec_module(bridge)


def spoken(text: str) -> str:
    return bridge.text_for_speech(text)


def test_error_code_and_link_are_spoken_instead_of_deleted():
    out = spoken('Ошибка: https://msg.bezrabotnyi.com/v1/search вернул 502.')
    assert '5 0 2' in out
    assert 'слеш слеш' in out
    assert 'протокол связи' in out
    assert 'сеаркх' in out


def test_latin_is_transliterated_so_the_russian_engine_pronounces_it():
    out = spoken('Контейнер userio-backend не отвечает.')
    assert not any('a' <= char.lower() <= 'z' for char in out), out
    assert 'усерио-баккенд' in out


def test_colon_becomes_a_spoken_pause_and_a_time_keeps_its_colon():
    out = spoken('В 20:00 проверка упала: код 502.')
    assert '20:00' in out
    assert ' — ' in out
    assert ':' not in out.replace('20:00', '')


def test_three_digit_code_is_read_digit_by_digit_but_counts_stay_numbers():
    out = spoken('Ответ 502, проверок 50, ожидание 250 миллисекунд, 12 процентов.')
    assert '5 0 2' in out
    assert 'проверок 50' in out
    assert '250 миллисекунд' in out
    assert '12 процентов' in out


def test_identifiers_and_paths_reach_the_owner():
    out = spoken('Инцидент: task_id abc-123, путь /var/lib/universal-userio/userio.sqlite3')
    assert 'абк' in out and '1 2 3' in out
    assert 'слеш вар слеш либ' in out
    assert 'универсал' in out


def test_known_terms_keep_their_plain_russian_wording():
    out = spoken('SDK и API недоступны.')
    assert 'модуль интеграции' in out
    assert 'программный интерфейс' in out


def test_speech_cleanup_is_idempotent():
    raw = 'В 20:00 HTTP 502 на https://a.test/x?y=1, source_id: abc123.'
    once = spoken(raw)
    assert spoken(once) == once


def test_markdown_destination_domain_survives_when_label_is_generic():
    out = spoken('На [сайте](https://notify.bezrabotnyi.com) ошибка HTTP 502.')
    assert spoken('notify.bezrabotnyi.com') in out, out
    assert '5 0 2' in out
