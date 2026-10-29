"""A supported attach_llm factory: fallback one provider request, never an Agent.

FastAgent owns its tool loop and history. This wrapper only sees its normalized
messages/tools and forwards the very same objects to another native provider
after a definite 429 before any text/reasoning/tool stream content.
"""
from dataclasses import dataclass
import os
import re
import time


@dataclass(frozen=True)
class ProviderLeg:
    slot: str
    model: str
    provider: str
    factory: object


def refusal_status(error):
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        status = getattr(error, 'status_code', None)
        if isinstance(status, int):return status
        error = error.__cause__ or error.__context__
    return None


class ProviderRequestFailed(RuntimeError):
    def __init__(self, status):
        self.status_code=status
        super().__init__('Native incident model request failed'+(f' HTTP {status}' if status else ''))


def check_native_response(response):
    # Native OpenAI providers convert APIError into an ERROR-channel message.
    # Only this provider-owned channel is authoritative; assistant text is not.
    reason=getattr(response,'stop_reason',None)
    if getattr(reason,'value',reason) != 'error':return
    channels=getattr(response,'channels',None) or {}
    detail=' '.join(getattr(block,'text','') for block in channels.get('fast-agent-error',[]))
    status=re.search(r'\(status=([0-9]{3})\)',detail)
    raise ProviderRequestFailed(int(status[1]) if status else None)


class IncidentProviderFallback:
    """Duck-typed FastAgentLLMProtocol, attached with the native public API."""
    def __init__(self, legs, *, fault_slots=lambda:set()):
        self.legs = tuple(legs)
        self._instances = {}
        self._listeners = set()
        self._tool_listeners = set()
        self.fault_slots = fault_slots
        self.begin_turn()

    def begin_turn(self):
        self.selected = 0
        self.model_route = []
        self.request_index = 0

    def _current(self):
        if self.selected not in self._instances:
            self._instances[self.selected] = self.legs[self.selected].factory()
        return self._instances[self.selected]

    def __getattr__(self, name):
        return getattr(self._current(), name)

    def add_stream_listener(self, listener):
        self._listeners.add(listener)
        return lambda:self._listeners.discard(listener)

    def add_tool_stream_listener(self, listener):
        self._tool_listeners.add(listener)
        return lambda:self._tool_listeners.discard(listener)

    async def generate(self, messages, request_params=None, tools=None):
        self.request_index += 1
        while True:
            leg = self.legs[self.selected]
            record = {'request':self.request_index, 'slot':leg.slot,
                      'provider':leg.provider, 'model':leg.model, 'injected':False}
            self.model_route.append(record)
            if leg.slot in self.fault_slots():
                record.update(outcome='rate_limited',status=429,injected=True)
                if self.selected+1 == len(self.legs):raise RuntimeError('No provider remains after injected refusal')
                self.selected += 1
                continue
            llm = self._current()
            committed = False
            def chunk(value):
                nonlocal committed
                committed = committed or bool(value if isinstance(value,str) else getattr(value,"text",None))
                for listener in tuple(self._listeners):listener(value)
            def tool_event(event_type, info=None):
                nonlocal committed
                committed = True
                for listener in tuple(self._tool_listeners):listener(event_type, info)
            remove = llm.add_stream_listener(chunk)
            remove_tools = llm.add_tool_stream_listener(tool_event)
            params = request_params.model_copy(update={'model':leg.model}) if request_params is not None else None
            try:
                response = await llm.generate(messages, params, tools)
                check_native_response(response)
                record.update(outcome='completed')
                return response
            except Exception as error:
                status = refusal_status(error)
                record.update(outcome='failed_after_content' if committed else 'failed',status=status)
                if status != 429 or committed or self.selected+1 == len(self.legs):raise
                record['outcome'] = 'rate_limited'
                self.selected += 1
            finally:
                remove();remove_tools()


def provider_fault_slots(context):
    """Read only the existing private operator scope, never caller/model flags."""
    from noticeplace_voice_context import qa_scope_document
    try:
        entries = qa_scope_document().get('provider_control', [])
        if not isinstance(entries,list):return set()
        for entry in entries:
            if not isinstance(entry,dict):continue
            fields = {'call_id':'secretary_voice_call_id','caller':'voice_target',
                      'account_ref':'voice_account_ref','room':'voice_room','sdk_user_id':'voice_sdk_user_id'}
            if any(not context.get(c) or entry.get(e) != context[c] for e,c in fields.items()):continue
            expiry = entry.get('expires_at')
            pair = entry.get('participants')
            slots = entry.get('rate_limit_slots')
            if (entry.get('synthetic') is not True or context.get('voice_transport') != 'matrix'
                    or not isinstance(pair,list) or len(pair)!=2 or len(set(pair))!=2
                    or context['voice_target'] not in pair
                    or type(expiry) not in (int,float) or not time.time() < expiry <= time.time()+600
                    or not isinstance(slots,list) or not set(slots) <= {'voice','voice_fallback_minimax'}):
                return set()
            return set(slots)
    except (PermissionError,ValueError,TypeError,KeyError):pass
    return set()


def native_factory(*, fault_context):
    """Construct native FastAgent providers from the existing secret profile.

    ZAI_API_KEY and CODEX_API_KEY are native provider bindings. GENERIC_API_KEY
    remains the existing MiniMax secret; no refresh credential is accepted.
    """
    from fast_agent.llm.model_factory import ModelFactory
    def attach(agent, request_params=None, **kwargs):
        definitions = [
            ('voice','glm-5.3-flash','zai-direct','generic.glm-5.3-flash',
             'https://api.z.ai/api/coding/paas/v4','ZAI_API_KEY'),
            ('voice_fallback_minimax','MiniMax-M3.1-Flash-Preview','minimax-direct',
             'generic.MiniMax-M3.1-Flash-Preview','https://api.minimax.io/v1','MINIMAX_API_KEY'),
            ('voice_fallback_luna','gpt-6-luna','codexresponses','codexresponses.gpt-6-luna',
             'https://chatgpt.com/backend-api/codex','CODEX_API_KEY')]
        legs = []
        for slot,model,provider,spec,base,key_env in definitions:
            def create(spec=spec,base=base,key_env=key_env,model=model):
                key = os.environ.get(key_env,'')
                if not key:raise PermissionError('Native incident provider binding unavailable: '+key_env)
                params = request_params.model_copy(update={'model':model}) if request_params is not None else None
                return ModelFactory.create_factory(spec)(agent=agent, request_params=params,
                    **dict(kwargs,base_url=base,api_key=key))
            legs.append(ProviderLeg(slot,model,provider,create))
        return IncidentProviderFallback(legs,fault_slots=lambda:provider_fault_slots(fault_context()))
    return attach
