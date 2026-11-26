import base64
import json
import subprocess

import pytest

from universal_userio.dion_chat_crypto import DionChatCrypto


def test_interoperates_with_browser_webcrypto():
    client = DionChatCrypto()
    # Real WebCrypto is the independent peer, not a second copy of Python crypto.
    script = r'''
const {webcrypto:c}=require('node:crypto');
(async()=>{
 const k=await c.subtle.generateKey({name:'ECDH',namedCurve:'P-256'},true,['deriveBits']);
 const peer=await c.subtle.importKey('raw',Buffer.from(process.argv[1],'base64'),{name:'ECDH',namedCurve:'P-256'},false,[]);
 const bits=await c.subtle.deriveBits({name:'ECDH',public:peer},k.privateKey,256);
 const hk=await c.subtle.importKey('raw',bits,'HKDF',false,['deriveKey']);
 const key=await c.subtle.deriveKey({name:'HKDF',hash:'SHA-256',salt:new Uint8Array(),info:new Uint8Array()},hk,{name:'AES-GCM',length:256},false,['encrypt']);
 const iv=c.getRandomValues(new Uint8Array(12));
 const body=await c.subtle.encrypt({name:'AES-GCM',iv},key,new TextEncoder().encode('{"messages":[],"label":"Привет"}'));
 console.log(JSON.stringify({server_public_key:Buffer.from(await c.subtle.exportKey('raw',k.publicKey)).toString('base64'),encrypted:Buffer.from(iv).toString('base64')+Buffer.from(body).toString('base64')}));
})();'''
    result = json.loads(subprocess.check_output(['node', '-e', script, client.public_key], text=True))
    client.establish(result['server_public_key'])
    assert client.decrypt_json(result['encrypted']) == {'messages': [], 'label': 'Привет'}


def test_tampering_and_missing_handshake_are_rejected():
    left, right = DionChatCrypto(), DionChatCrypto()
    with pytest.raises(ValueError):
        left.encrypt_json({})
    left.establish(right.public_key)
    right.establish(left.public_key)
    ciphertext = left.encrypt_json({'id': 'fixture'})
    assert right.decrypt_json(ciphertext) == {'id': 'fixture'}
    raw = bytearray(base64.b64decode(ciphertext)); raw[-1] ^= 1
    with pytest.raises(ValueError):
        right.decrypt_json(base64.b64encode(raw).decode())
