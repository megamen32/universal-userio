from universal_userio.agentcall_ingress import AgentCallEventProjector


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
