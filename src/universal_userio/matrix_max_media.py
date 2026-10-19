"""Original MAX media bytes for an owned conversation; reuse the native client."""
from .contracts import ChannelFile
from .messenger_max_media import fetch_native_url
import mimetypes


def metadata(attachment):
    """Public display fields only; signed provider references stay private."""
    kind=str(attachment.get('kind') or attachment.get('_type') or '').lower()
    filename=str(attachment.get('filename') or attachment.get('name') or attachment.get('fileName') or
                 ('max-photo.jpg' if kind=='photo' else 'max-sticker.png' if kind=='sticker' else 'Вложение MAX'))
    content_type=str(attachment.get('content_type') or attachment.get('mimeType') or '')
    if not content_type or content_type=='application/octet-stream':
        content_type=mimetypes.guess_type(filename)[0] or 'application/octet-stream'
    return {'kind':kind,'filename':filename,'content_type':content_type,'size':attachment.get('size')}

def download(service, user_id, conversation, record, *, max_bytes=2*1024*1024, attachment_index=0):
    if type(attachment_index) is not int or not 0<=attachment_index<100:
        raise ValueError('Некорректный номер вложения')
    peer=str(conversation.get("peer_id") or "")
    account=str(conversation.get("account_ref") or "")
    mid=str(record.get("message_id") or "")
    prefix=account+"|"+peer+":" if account.startswith("max:") else "max-"+peer+"-"
    if not peer or not mid.startswith(prefix) or not mid[len(prefix):].isdigit():
        raise ValueError("MAX media source identity unavailable")
    native_id=mid[len(prefix):]
    client=service.max_client_for(conversation,user_id)
    row=client.get_message(chat_id=peer,message_id=native_id)
    if not row or str(row.get("id"))!=native_id:
        raise ValueError("MAX media source message unavailable")
    attachments=row.get("attaches") or []
    if not attachments:
        raise ValueError("MAX message has no native attachment")
    if attachment_index>=len(attachments):
        raise ValueError('Вложение не найдено')
    attachment=attachments[attachment_index]
    stored=next((item for item in record.get('attachments') or []
                 if item.get('idx')==attachment_index),None)
    stored_id=str((stored or {}).get('attachment_id') or '')
    actual_id=next((str(attachment.get(key)) for key in
                    ('fileId','photoId','videoId','audioId','stickerId')
                    if attachment.get(key) is not None),'')
    if stored is None or stored_id and stored_id!=actual_id:
        raise ValueError('MAX selected attachment source changed')
    kind=attachment.get("_type")
    public=metadata(attachment)
    filename=public['filename']
    if kind=="PHOTO":
        url=attachment.get("baseUrl")
        filename="max-photo.jpg"
    elif kind=="STICKER":
        sticker=attachment.get("sticker") or attachment
        url=sticker.get("url") or sticker.get("baseUrl")
        filename="max-sticker.png"
    elif kind in {"AUDIO","FILE","VIDEO"}:
        ref=client.media_reference(chat_id=peer,message_id=native_id,attachment=attachment)
        url=ref.get("url") or ref.get("downloadUrl")
        if kind in {"AUDIO","VIDEO"}:
            url=url or ref.get("MP4_240") or ref.get("MP4_360") or ref.get("MP4_480") or ref.get("MP4_720")
    else:
        raise ValueError("MAX native attachment type unavailable")
    data,mime=fetch_native_url(url,maximum=max_bytes)
    content_type=mime or public['content_type']
    if content_type=='application/octet-stream' and public['content_type']!='application/octet-stream':
        content_type=public['content_type']
    return ChannelFile(filename,content_type,data)
