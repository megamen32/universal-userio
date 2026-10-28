"""Private call-bound recipient assertion; Notice Place verifies its signature."""
import base64
import hashlib
import hmac
import json
import re
import time


def bind_recipient(call, *, receipt_id, account_ref, token, now=None):
    """Sign only backend-admitted fields, never a caller-supplied incident ID."""
    if not token:
        return
    moment = time.time() if now is None else now
    payload = dict(v=1, call_id=call['engine_call_id'], receipt_id=receipt_id,
        caller=call['caller'], account_ref=account_ref, room_id=call['room_id'],
        expires_at=int(moment + min(int(call['max_seconds'])+120,600)))
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
    call['voice_receipt_id'] = receipt_id
    call['voice_recipient_proof'] = (base64.urlsafe_b64encode(raw).decode().rstrip('=') + '.' +
        hmac.new(token.encode(), raw, hashlib.sha256).hexdigest())


def recipient_context(call, *, caller, account_ref, room_id, now=None):
    """Copy the existing authenticated control assertion only for this leg.

    The worker has no signing key. This is structural validation, followed by
    authoritative HMAC verification in Notice Place. Missing assertions leave
    ordinary calls usable and confer no incident access.
    """
    proof=call.get('voice_recipient_proof'); receipt=call.get('voice_receipt_id')
    if not isinstance(proof,str) or not isinstance(receipt,str) or len(proof)>4096:
        return {}
    try:
        encoded, signature=proof.split('.')
        if not re.fullmatch('[0-9a-f]{64}', signature):return {}
        payload=json.loads(base64.urlsafe_b64decode(encoded+'='*(-len(encoded)%4)))
        if not isinstance(payload,dict):return {}
        expected=dict(v=1,call_id=call['engine_call_id'],receipt_id=receipt,
                      caller=caller,account_ref=account_ref,room_id=room_id)
        if any(payload.get(k)!=v for k,v in expected.items()):return {}
        moment=time.time() if now is None else now
        expiry=payload.get('expires_at')
        if type(expiry) is not int or not moment<expiry<=moment+600:return {}
    except (ValueError,TypeError,KeyError,UnicodeError):
        return {}
    return {'voice_receipt_id':receipt,'voice_recipient_proof':proof}
