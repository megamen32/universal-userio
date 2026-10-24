"""Spoken incident text keeps its content: nothing technical is dropped.

The owner reads the incident from a phone call. A dropped error code, URL or
identifier is a lost diagnosis, so the speech text carries every character
through: a link is named the way people say it in Russian, a number is said
with words, Latin is transliterated and punctuation becomes a word.

A bare digit is not carried through: the speech engine drops such a token
without a sound, so a code written as digits reaches the owner as a gap.
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
    assert 'пять ноль два' in out
    assert 'слеш' in out
    assert 'протокол связи' in out
    assert 'сеаркх' in out


def test_a_number_is_heard_because_a_bare_digit_is_dropped_by_the_engine():
    out = spoken('Код ответа 502, проверок 50, ожидание 250 миллисекунд, 12 процентов.')
    assert not any(character.isdigit() for character in out), out
    assert 'пять ноль два' in out
    assert 'пятьдесят' in out
    assert 'двести пятьдесят миллисекунд' in out
    assert 'двенадцать процентов' in out


def test_a_clock_is_heard_as_well():
    out = spoken('В 20:00 проверка упала.')
    assert not any(character.isdigit() for character in out), out
    assert 'двадцать ноль ноль' in out


def test_site_is_named_in_russian_instead_of_being_dropped():
    out = spoken('Сайт https://bezrabotnyi.com не отвечает.')
    assert 'безработный точка ком' in out, out
    assert 'bezrabotnyi' not in out


def test_subdomain_is_named_part_by_part():
    out = spoken('Проверка https://msg.bezrabotnyi.com/v1/events упала.')
    assert 'месенджер точка безработный точка ком' in out, out
    assert 'bezrabotnyi' not in out


def test_brand_is_named_the_way_people_say_it():
    out = spoken('Сбой Apple привёл к отказу Google, Telegram не отвечает.')
    assert 'Эйпл' in out, out
    assert 'Гугл' in out, out
    assert 'Телеграм' in out, out
    assert 'Apple' not in out and 'Google' not in out


def test_domain_and_sentence_boundary_are_not_mixed():
    out = spoken('Открой example.com. Дальше работает.')
    assert out.count('точка') == 1, out
    assert 'Дальше работает' in out


def test_error_code_next_to_a_dot_is_not_read_as_an_address():
    out = spoken('Код 502. Дальше работает.')
    assert out.count('точка') == 0, out
    assert 'пять ноль два' in out


def test_latin_is_transliterated_so_the_russian_engine_pronounces_it():
    out = spoken('Контейнер userio-backend не отвечает.')
    assert not any('a' <= char.lower() <= 'z' for char in out), out
    assert 'усерио-баккенд' in out


def test_colon_becomes_a_spoken_pause_and_a_time_keeps_its_colon():
    out = spoken('В 20:00 проверка упала: код 502.')
    assert 'двадцать ноль ноль' in out
    assert ' — ' in out
    assert ':' not in out


def test_identifiers_and_paths_reach_the_owner():
    out = spoken('Инцидент: task_id abc-123, путь /var/lib/universal-userio/userio.sqlite3')
    assert 'абк' in out and 'один два три' in out
    assert 'слеш вар слеш либ' in out
    assert 'универсал' in out
    assert not any(character.isdigit() for character in out), out


def test_an_identifier_is_read_part_by_part():
    out = spoken('Идентификатор 3f9a2b1c-1111-2222-3333-444455556666')
    assert 'один один один один' in out, out
    assert 'четыре четыре четыре четыре' in out, out
    assert not any(character.isdigit() for character in out), out


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
    assert 'пять ноль два' in out
