"""Expose the existing private gateway archive through an owned call message."""
import json
from pathlib import Path
import re
import uuid
import wave
from urllib.parse import quote

ARCHIVE = Path('/var/lib/agentcall/recordings')
TRACKS = {'caller': 'Собеседник', 'assistant': 'Телефон'}


def archive_for(conversation):
    return ARCHIVE


def recording(conversation, message, root=None):
    if (conversation.get('source') != 'phone' and not str(conversation.get('source', '')).startswith('matrix:')
            or message.get('source') != conversation.get('source')
            or message.get('conversation_id') != conversation.get('id')):
        return None
    try:
        call_id = str(uuid.UUID(str(message['sender'])))
        folder = (root or archive_for(conversation)) / call_id
        if folder.is_symlink():
            return None
        manifest = folder / 'manifest.json'
        if manifest.is_symlink() or manifest.stat().st_size > 65536:
            return None
        data = json.loads(manifest.read_text())
        if not isinstance(data, dict) or not isinstance(data.get('peerFields'), dict):
            return None
        peer = data.get('peerFields', {})
        if (data.get('callId') != call_id or not conversation.get('account_ref')
                or peer.get('accountRef') != conversation['account_ref']
                or peer.get('peerRef', '') != conversation.get('peer_id', '')):
            return None
        tracks = []
        frames = []
        rate=data.get('sampleRate',8000)
        if type(rate) is not int or rate not in (8000,12000,16000,24000,32000,44100,48000):return None
        transcript = {}
        transcript_path = folder / 'live-transcript.json'
        if transcript_path.is_file() and not transcript_path.is_symlink() and transcript_path.stat().st_size <= 131072:
            transcript = json.loads(transcript_path.read_text())
            if not isinstance(transcript, dict): transcript = {}
        final_path = folder / 'transcript.json'
        if final_path.is_file() and not final_path.is_symlink() and final_path.stat().st_size <= 131072:
            final = json.loads(final_path.read_text())
            if isinstance(final, dict) and final.get('callId') == call_id and isinstance(final.get('tracks'), dict):
                for name in TRACKS:
                    track = final['tracks'].get(name, {})
                    segments = track.get('segments', []) if isinstance(track, dict) else []
                    text = '\n'.join(s['text'] for s in segments if isinstance(s, dict) and isinstance(s.get('text'), str))
                    if text: transcript[name] = text
        for name, label in TRACKS.items():
            if name == 'assistant' and str(conversation.get('source', '')).startswith('matrix:'): label = 'ИИ'
            path = folder / (name + '.wav')
            if path.is_symlink() or not 44 <= path.stat().st_size <= 44 + rate * 2 * 7200:
                return None
            with wave.open(str(path)) as audio:
                if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, rate):
                    return None
                count = audio.getnframes()
            if path.stat().st_size != 44 + count * 2:
                return None
            frames.append(count)
            base = f"/v1/conversations/{quote(conversation['id'], safe='')}/call-audio/{quote(message['message_id'], safe='')}"
            tracks.append({'name': name, 'label': label, 'url': base + '/' + name,
                           'duration': count / rate,
                           'transcript': transcript.get(name, '') if isinstance(transcript.get(name, ''), str) else ''})
        return {'complete': data.get('complete') is True and frames[0] == frames[1],
                'duration': max(frames) / rate, 'tracks': tracks}
    except (OSError, ValueError, KeyError, TypeError, wave.Error):
        return None


def enrich(conversation):
    if conversation and (conversation.get('source') == 'phone' or str(conversation.get('source', '')).startswith('matrix:')):
        for message in conversation.get('messages', []):
            if message.get('direction') == 'system' and message.get('body', '').startswith('Звонок завершён'):
                audio = recording(conversation, dict(message, conversation_id=conversation['id']))
                if audio:
                    message['call_audio'] = audio
    return conversation


def byte_range(value, size):
    if not value:
        return 0, size - 1, 200
    match = re.fullmatch(r'bytes=(\d*)-(\d*)', value)
    if not match or not any(match.groups()):
        raise ValueError('invalid range')
    start, end = match.groups()
    if not start:
        if int(end) <= 0:
            raise ValueError('invalid suffix')
        return max(0, size - int(end)), size - 1, 206
    start = int(start)
    end = min(int(end), size - 1) if end else size - 1
    if start >= size or end < start:
        raise ValueError('unsatisfiable range')
    return start, end, 206
