"""VK personal-messages API, limited to consented user tokens.

Use only a user-authorized VK Messenger access token that has been independently
verified against messages.getConversations and messages.getHistory. It sends
no messages. The separate client sends only an explicitly approved message;
neither reader nor sender marks conversations read.
"""
from __future__ import annotations

import json
import re
import uuid
import mimetypes
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable
from ..contracts import DeliveryNotSubmittedError


class VKUploadRefused(DeliveryNotSubmittedError):
    def __init__(self, code):
        self.code=code
        super().__init__(f'VK отказал в загрузке файла (HTTP {code}); сообщение не отправлено')


class VKPermissionDenied(ValueError):
    """This VK token cannot access personal messages."""


class VKEditPreflightError(ValueError):
    """Known refusal before a messages.edit command is submitted."""


class VKPersonalReader:
    API_BASE = "https://api.vk.ru/method/"
    API_VERSION = "5.199"

    def __init__(self, access_token: str, *, opener: Callable[..., Any] | None = None):
        if not isinstance(access_token, str) or not 10 <= len(access_token) <= 4096:
            raise ValueError("VK не выдал пригодный токен")
        self._token = access_token
        self._opener = opener or urllib.request.build_opener(_RejectRedirect).open
        self._file_opener = opener or urllib.request.build_opener(_NativeFileRedirect).open
        self._last_call = 0.0

    def call(self, method: str, **kwargs: object) -> Any:
        if method not in {"users.get", "messages.getConversations", "messages.getHistory", "messages.getLongPollServer", "messages.getById", "messages.getReactionsAssets"}:
            raise ValueError("VK method not allowed in read-only connector")
        return self._request(method, kwargs)

    def _request(self, method: str, kwargs: dict, *, mutation=False) -> Any:
        # Respect a small per-session VK API budget without holding the
        # shared UserIO database lock while waiting.
        elapsed = time.monotonic() - self._last_call
        if elapsed < 0.36 and self._last_call:
            time.sleep(0.36 - elapsed)
        self._last_call = time.monotonic()
        form = urllib.parse.urlencode({
            "access_token": self._token, "v": self.API_VERSION, **kwargs,
        }).encode("utf-8")
        request = urllib.request.Request(
            self.API_BASE + method, data=form, method="POST",
            headers={"Accept": "application/json",
                     "Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with self._opener(request, timeout=12) as response:
                body = response.read(2_000_001)
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            raise RuntimeError("VK API временно недоступен") from exc
        if len(body) > 2_000_000:
            raise ValueError("слишком большой ответ VK")
        try:
            response = json.loads(body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise RuntimeError("VK API вернул некорректный ответ") from exc
        if not isinstance(response, dict):
            raise RuntimeError("VK API вернул некорректный ответ")
        error = response.get("error")
        if isinstance(error, dict):
            code = error.get("error_code")
            if mutation and type(code) is int:
                raise DeliveryNotSubmittedError(f"VK не отправил сообщение (код {code}). Проверьте доступ аккаунта к переписке.")
            if code in (5, 7, 15, 20, 27, 28):
                raise VKPermissionDenied(
                    "VK не выдал доступ к личным сообщениям: нужен "
                    "пользовательский токен с разрешением messages"
                )
            if code in (6, 29):
                raise RuntimeError("VK API ограничил частоту обращений")
            raise RuntimeError("VK API отказал в чтении")
        if "response" not in response:
            raise RuntimeError("VK API не вернул данные")
        return response["response"]

    def identity(self) -> int:
        result = self.call("users.get")
        if not isinstance(result, list) or not result or not isinstance(result[0], dict):
            raise RuntimeError("VK не подтвердил пользователя")
        actual = result[0].get("id")
        if type(actual) is not int or actual <= 0:
            raise RuntimeError("VK вернул неверный ID пользователя")
        return actual

    def conversations(self, *, offset: int = 0, count: int = 50) -> dict[str, Any]:
        if type(offset) is not int or offset < 0 or type(count) is not int or not 1 <= count <= 100:
            raise ValueError("VK dialog page is invalid")
        result = self.call("messages.getConversations", offset=offset, count=count, extended=1)
        if not isinstance(result, dict) or not isinstance(result.get("items"), list):
            raise RuntimeError("VK не вернул список диалогов")
        return result

    def history(self, peer_id: int, *, count: int = 30, offset: int = 0) -> list[dict[str, Any]]:
        if (type(peer_id) is not int or peer_id == 0 or type(count) is not int
                or not 1 <= count <= 100 or type(offset) is not int or offset < 0):
            raise ValueError("VK history request is invalid")
        result = self.call("messages.getHistory", peer_id=peer_id, count=count, offset=offset)
        if not isinstance(result, dict) or not isinstance(result.get("items"), list):
            raise RuntimeError("VK не вернул историю диалога")
        return [r for r in result["items"] if isinstance(r, dict)]

    def longpoll_server(self) -> dict[str, Any]:
        result = self.call("messages.getLongPollServer", need_pts=1, lp_version=10)
        if not isinstance(result, dict):
            raise RuntimeError("VK не вернул параметры User Long Poll")
        if not all(isinstance(result.get(k), (str, int)) for k in ("server", "key", "ts")):
            raise RuntimeError("VK Long Poll ответ некорректен")
        return result

    def message(self, message_id: int) -> dict[str, Any]:
        if type(message_id) is not int or message_id<=0:
            raise ValueError('VK exact message ID required')
        result=self.call('messages.getById',message_ids=message_id)
        rows=result.get('items') if isinstance(result,dict) else None
        row=next((r for r in rows or [] if isinstance(r,dict) and r.get('id')==message_id),None)
        if row is None: raise ValueError('VK не вернул отправленное сообщение')
        return row


class VKPersonalClient(VKPersonalReader):
    # Verified VKCOM 5.199 catalog artwork: native IDs, not Unicode IDs.
    REACTION_IDS = {'❤':1, '❤️':1, '🔥':2, '😂':3, '👍':4}

    def react(self, *, peer_id, row, emoji):
        cmid=row.get('conversation_message_id')
        if (type(peer_id) is not int or peer_id==0 or row.get('peer_id')!=peer_id
                or type(cmid) is not int or cmid<=0):
            raise DeliveryNotSubmittedError('VK native conversation message identity unavailable')
        if not isinstance(emoji,str) or emoji not in {*self.REACTION_IDS, ''}:
            raise DeliveryNotSubmittedError('VK reaction not supported')
        args={'peer_id':peer_id,'cmid':cmid}
        if emoji:
            catalog=self.call('messages.getReactionsAssets')
            ids=catalog.get('reaction_ids') if isinstance(catalog,dict) else None
            if not isinstance(ids,list) or self.REACTION_IDS[emoji] not in ids:
                raise DeliveryNotSubmittedError('VK reaction unavailable in current catalog')
            args['reaction_id']=self.REACTION_IDS[emoji]
        result=self._request('messages.sendReaction' if emoji else 'messages.deleteReaction',args,mutation=True)
        if result is not True and (type(result) is not int or result!=1):
            raise RuntimeError('VK reaction result uncertain; do not replay')

    def send(self, peer_id: int, text: str, random_id: int, *, attachment: str = '') -> str:
        if type(peer_id) is not int or peer_id==0:
            raise ValueError('VK exact peer ID required')
        if not isinstance(text,str) or (not text.strip() and not attachment) or len(text)>4096:
            raise ValueError('Сообщение VK должно содержать от 1 до 4096 символов')
        if type(random_id) is not int or not 0<random_id<2**31:
            raise ValueError('VK stable random ID required')
        # VKCOM schema: user-token messages.send, random_id deduplicates sends.
        args={'peer_id':peer_id,'message':text,'random_id':random_id}
        if attachment:
            if not re.fullmatch(r'(?:photo|doc)-?\d+_\d+(?:_[A-Za-z0-9_-]+)?', attachment):
                raise ValueError('VK native attachment required')
            args['attachment']=attachment
        result=self._request('messages.send',args,mutation=True)
        if type(result) is not int or result<=0:
            raise RuntimeError('VK не подтвердил отправку. Не повторяйте сообщение; обновите диалог.')
        return str(result)

    def edit(self, *, peer_id, message_id, body, expected_body, own_id):
        if not isinstance(body,str) or not body.strip() or len(body)>4096:
            raise ValueError('Сообщение VK должно содержать от 1 до 4096 символов')
        original=self.message(message_id)
        if expected_body=='[Вложение VK]' and not original.get('text') and original.get('attachments'):
            expected_body=''
        if (original.get('peer_id')!=peer_id or original.get('from_id')!=own_id
                or original.get('out')!=1 or original.get('text','')!=expected_body):
            raise VKEditPreflightError('Сообщение VK уже изменилось или принадлежит другому собеседнику')
        refs=[]
        for att in original.get('attachments') or []:
            kind=att.get('type')
            if kind=='link':continue  # rebuild snippets from the new text
            if kind not in {'photo','doc'}:raise ValueError('Редактирование этого вложения VK не поддерживается')
            refs.append(native_attachment_ref(kind,att.get(kind) or {}))
        ack=self._request('messages.edit',{'peer_id':peer_id,'message_id':message_id,
            'message':body,'attachment':','.join(refs),'keep_snippets':0,'keep_forward_messages':1},mutation=True)
        if ack!=1:raise RuntimeError('VK не подтвердил изменение; обновите диалог без повторной отправки')
        row=self.message(message_id)
        if row.get('text')!=body:raise RuntimeError('Результат изменения VK уточняется; обновите диалог')
        return row

    def upload(self, *, peer_id, data, filename, mime_type):
        # HTTP405 is a definite upload refusal before any messages.send.
        # Refresh this native upload slot once; never replay an uncertain send.
        for attempt in range(2):
            try:
                return self._upload_once(peer_id=peer_id,data=data,filename=filename,mime_type=mime_type)
            except VKUploadRefused as error:
                if error.code!=405 or attempt:
                    raise
                logging.getLogger(__name__).warning('VK upload slot refused HTTP405; obtaining one fresh slot')
                time.sleep(0.4)
        raise AssertionError('unreachable upload loop')

    def _upload_once(self, *, peer_id, data, filename, mime_type):
        if not isinstance(data,bytes) or not 1<=len(data)<=2*1024*1024:
            raise ValueError('Вложение VK должно быть не больше 2 МБ')
        if not isinstance(filename,str) or not 1<=len(filename)<=128 or any(c in filename for c in '/\\\r\n"'):
            raise ValueError('Некорректное имя файла')
        photo=mime_type in {'image/png','image/jpeg','image/gif'}
        method='photos' if photo else 'docs'
        args={'peer_id':peer_id}
        if not photo:args['type']='doc'
        server=self._request(method+'.getMessagesUploadServer',args)
        url=server.get('upload_url') if isinstance(server,dict) else None
        _native_file_url(url)
        boundary='UserIO'+uuid.uuid4().hex
        content=(f'--{boundary}\r\nContent-Disposition: form-data; name="'+('photo' if photo else 'file')+f'"; filename="{filename}"\r\nContent-Type: {mime_type}\r\n\r\n').encode()+data+f'\r\n--{boundary}--\r\n'.encode()
        req=urllib.request.Request(url,data=content,method='POST',headers={'Content-Type':'multipart/form-data; boundary='+boundary})
        try:
            with self._opener(req,timeout=20) as response:
                raw=response.read(2_000_001)
                try:result=json.loads(raw)
                except (ValueError,UnicodeError):
                    logging.getLogger(__name__).warning('VK file upload response invalid: HTTP%s type=%s bytes=%s',
                        getattr(response,'status',None),getattr(response,'headers',{}).get('Content-Type',''),len(raw))
                    raise
        except urllib.error.HTTPError as error:
            logging.getLogger(__name__).warning('VK file upload refused: HTTP%s',error.code)
            raise VKUploadRefused(error.code) from error
        except (OSError,ValueError) as error:
            logging.getLogger(__name__).warning('VK file upload failed before message send: %s',type(error).__name__)
            raise RuntimeError('Загрузка VK не подтверждена; не отправляйте повторно') from error
        if photo:
            saved=self._request('photos.saveMessagesPhoto',{k:result[k] for k in ('photo','server','hash')},mutation=True)
            if not isinstance(saved,list) or len(saved)!=1:raise RuntimeError('VK не сохранил фото')
            return native_attachment_ref('photo',saved[0])
        saved=self._request('docs.save',{'file':result['file'],'title':filename},mutation=True)
        return native_attachment_ref('doc',saved.get('doc') or {})

    def download(self, row, index=0):
        attachments=vk_native_attachments(row)
        if not 0<=index<len(attachments):raise ValueError('Вложение VK не найдено')
        att=attachments[index];_native_file_url(att['src'])
        with self._file_opener(urllib.request.Request(att['src']),timeout=20) as response:
            data=response.read(2*1024*1024+1)
        if len(data)>2*1024*1024:raise ValueError('Вложение VK больше 2 МБ')
        return {**att,'data':data,'size':len(data)}


def _native_file_url(url):
    parsed=urllib.parse.urlsplit(str(url or ''));host=parsed.hostname or ''
    if (parsed.scheme!='https' or parsed.username or parsed.password or parsed.port not in {None,443}
            or not any(host==d or host.endswith('.'+d) for d in ('vk.com','vk.ru','userapi.com','vkuser.net','vkuserphoto.ru','vkuseraudio.net'))):
        raise ValueError('VK не вернул поддерживаемый адрес файла')


def native_attachment_ref(kind, value):
    if kind not in {'photo','doc'} or type(value.get('owner_id')) is not int or type(value.get('id')) is not int:
        raise ValueError('VK не подтвердил идентификатор вложения')
    ref=f"{kind}{value['owner_id']}_{value['id']}"
    if value.get('access_key'):ref+='_'+str(value['access_key'])
    if not re.fullmatch(r'(?:photo|doc)-?\d+_\d+(?:_[A-Za-z0-9_-]+)?',ref):raise ValueError('VK attachment reference invalid')
    return ref


def vk_native_attachments(row):
    result=[]
    for att in row.get('attachments') or []:
        kind=att.get('type');value=att.get(kind) or {}
        if kind not in {'photo','doc'}:continue
        ref=native_attachment_ref(kind,value)
        if kind=='photo':
            sizes=[x for x in value.get('sizes') or [] if isinstance(x,dict) and x.get('url')]
            best=max(sizes,key=lambda x:int(x.get('width',0))*int(x.get('height',0)),default={})
            url=best.get('url','');name=f"photo-{value['id']}.jpg";mime='image/jpeg'
        else:
            url=value.get('url','');name=value.get('title') or f"file-{value['id']}";mime=mimetypes.guess_type(name)[0] or 'application/octet-stream'
        result.append({'idx':len(result),'kind':'image' if kind=='photo' else 'doc','attachment_id':ref,
            'provider_ref':ref,'filename':name,'content_type':mime,'src':url,'size':value.get('size')})
    return result


class _RejectRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("VK API must not redirect access tokens")


class _NativeFileRedirect(urllib.request.HTTPRedirectHandler):
    """Native download CDN redirects carry no API token or authentication header."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _native_file_url(newurl)
        if req.get_method()!='GET' or req.has_header('Authorization') or req.data:
            raise ValueError('VK file redirect requires an unauthenticated native GET')
        return super().redirect_request(req,fp,code,msg,headers,newurl)
