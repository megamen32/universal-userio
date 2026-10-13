"""Original MAX media bytes for an owned conversation; reuse the native client."""
from .contracts import ChannelFile
from .messenger_max_media import fetch_native_url

def download(service, user_id, conversation, record, *, max_bytes=2*1024*1024):
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
    attachment=attachments[0]
    kind=attachment.get("_type")
    filename=str(attachment.get("name") or attachment.get("fileName") or "MAX attachment")
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
    return ChannelFile(filename,mime or "application/octet-stream",data)
