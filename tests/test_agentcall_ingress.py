from universal_userio.agentcall_ingress import AgentCallEventProjector


def test_agentcall_peer_snapshot_preserves_uuid_time_and_person_identity():
    peer='phone:v1:'+'a'*64
    p=AgentCallEventProjector('S21', account_ref='phone100')
    m=p.project({'event':'transcript_final','callId':'call-a','speaker':'remote','text':'Алло',
                 'accountRef':'phone100','peerRef':peer,'peerLabel':'Контакт','at':1700000000.25})
    assert m['sender']=='call-a' and m['peer_id']==peer and m['received_at']==1700000000.25
    assert m['conversation_title']=='Контакт'
    wrong=p.project({'event':'ended','callId':'call-b','accountRef':'other','peerRef':peer,'at':1})
    assert wrong.get('peer_id','')==''
    assert 'phoneNumber' not in m


def test_agentcall_projector_preserves_chat_direction_and_system_events() -> None:
    projector = AgentCallEventProjector("S21 AI call")

    remote = projector.project({
        "event": "transcript_final", "callId": "call-1",
        "speaker": "remote", "text": "Алло",
    })
    agent = projector.project({
        "event": "transcript_final", "callId": "call-1",
        "speaker": "agent", "text": "Здравствуйте",
    })
    ended = projector.project({
        "event": "ended", "callId": "call-1", "reason": "hangup",
    })

    assert remote is not None and remote["direction"] == "incoming"
    assert agent is not None and agent["direction"] == "outgoing"
    assert ended is not None and ended["direction"] == "system"
