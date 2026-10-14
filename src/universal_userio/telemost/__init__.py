"""Telemost meeting capture: separate from the WhiteTransport data tunnel."""
from urllib.parse import urlsplit
import re


def meeting_url(value):
    if not isinstance(value,str) or len(value)>2048:
        raise ValueError('Нужна ссылка на встречу Телемоста')
    u=urlsplit(value.strip())
    if (u.scheme!='https' or u.hostname not in {'telemost.yandex.ru','telemost.yandex.com','meet.yandex.ru'}
            or u.username or u.password or u.port not in {None,443}
            or not re.fullmatch(r'/j/[0-9]{10,32}/?',u.path) or u.query or u.fragment):
        raise ValueError('Нужна обычная HTTPS-ссылка Телемоста /j/…')
    return 'https://'+u.hostname+u.path.rstrip('/')
