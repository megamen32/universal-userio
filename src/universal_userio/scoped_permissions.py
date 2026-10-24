"""Per-token account and chat grants; enforced before any provider operation."""
from __future__ import annotations
import json
import hashlib

READ_TOOLS = {'capabilities', 'history', 'has_contacted', 'get', 'resolve', 'media', 'events'}


def validate_grants(store, user_id, grants):
    if not isinstance(grants, list) or not 1 <= len(grants) <= 64:
        raise ValueError('Выберите хотя бы один аккаунт (не более 64).')
    owned = {a['id']: a for a in store.accounts(user_id=user_id)}
    result, seen = [], set()
    for grant in grants:
        if not isinstance(grant, dict) or set(grant) - {'account_ref', 'access', 'chats', 'telegram_admin'} or not {'account_ref', 'access', 'chats'} <= set(grant):
            raise ValueError('Неверный формат прав аккаунта.')
        ref, access, chats = grant['account_ref'], grant['access'], grant['chats']
        telegram_admin = grant.get('telegram_admin', False)
        if not isinstance(ref, str) or ref in seen or ref not in owned or not owned[ref]['enabled']:
            raise ValueError('Аккаунт должен быть подключён к вашему UserIO и выбран один раз.')
        if type(telegram_admin) is not bool or telegram_admin and owned[ref]['provider'] != 'telegram':
            raise ValueError('Право управления группами и каналами доступно только Telegram-аккаунтам.')
        if access not in {'read_only', 'read_write'}:
            raise ValueError('Выберите чтение либо чтение и запись.')
        if chats is not None:
            if not isinstance(chats, list) or not 1 <= len(chats) <= 256:
                raise ValueError('Выберите чаты либо разрешите все чаты аккаунта.')
            chat_ids = set()
            for chat in chats:
                if not isinstance(chat, dict) or set(chat) != {'conversation_id', 'access'}:
                    raise ValueError('Неверный формат прав чата.')
                cid = chat['conversation_id']
                if not isinstance(cid, str) or cid in chat_ids:
                    raise ValueError('Чат должен быть выбран один раз.')
                conv = store.conversation(cid, user_id=user_id)
                if not conv or conv['account_ref'] != ref:
                    raise ValueError('Чат не принадлежит выбранному аккаунту.')
                if chat['access'] not in {'read_only','read_write'} or access == 'read_only' and chat['access'] != 'read_only':
                    raise ValueError('Права чата не могут превышать права аккаунта.')
                chat_ids.add(cid)
        seen.add(ref)
        result.append({'account_ref':ref, 'access':access, 'chats':chats,
                       'telegram_admin':telegram_admin})
    return result


def grant_for(principal, account_ref, conversation_id=None):
    for grant in principal.messenger_grants or ():
        if grant['account_ref'] == account_ref:
            if conversation_id is None or grant['chats'] is None:
                return grant['access']
            return next((c['access'] for c in grant['chats'] if c['conversation_id'] == conversation_id), None)
    return None


def _scoped_conversation(store, principal, source, *, chat_id=None, message_id=None):
    if bool(chat_id) == bool(message_id):
        raise ValueError('exact_chat_or_message_required')
    if chat_id:
        conversation = store.conversation(str(chat_id), user_id=principal.user_id)
    else:
        message = store.message(str(message_id), user_id=principal.user_id)
        conversation = store.conversation(str(message['conversation_id']), user_id=principal.user_id) if message else None
    if conversation is None:
        raise KeyError('chat_not_found')
    actual_source = str(conversation['source'])
    source_matches = (not source or actual_source == source
        or source == 'mail' and (actual_source in {'mail','email','gmail'} or actual_source.startswith(('gmail:','email:')))
        or source == 'matrix' and actual_source.startswith('matrix:'))
    if not source_matches:
        raise KeyError('chat_not_found')
    access = grant_for(principal, conversation['account_ref'], str(conversation['id']))
    if access is None:
        raise PermissionError('chat_or_account_outside_token_scope')
    return conversation, access


def enforce(store, principal, name, args):
    if principal.messenger_grants is None:
        return
    if name in {'tools/list','tools/call','userio.accounts.list'}:
        return
    if name == 'userio.channels.list':
        ref = args.get('account_ref')
        if ref is not None and grant_for(principal, ref) is None:
            raise PermissionError('chat_or_account_outside_token_scope')
        return
    if name == 'userio.channels.search':
        ref = args.get('account_ref')
        if ref is not None and grant_for(principal, ref) is None:
            raise PermissionError('chat_or_account_outside_token_scope')
        return
    if name == 'userio.channels.export':
        _scoped_conversation(store, principal, args.get('channel'),
                             chat_id=args.get('conversation_id'))
        return
    if name == 'userio.channels.read':
        _scoped_conversation(store, principal, args.get('channel'),
                             chat_id=args.get('chat_id'), message_id=args.get('message_id'))
        return
    if name == 'userio.channels.download':
        file_ref = args.get('file_ref')
        channel, separator, message_id = str(file_ref or '').partition(':')
        if not separator or not message_id:
            raise ValueError('invalid_file_ref')
        _scoped_conversation(store, principal, channel, message_id=message_id)
        return
    if name == 'userio.messages.prepare':
        if principal.read_only or not store.capability_enabled('send',user_id=principal.user_id):
            raise PermissionError('send_capability_disabled')
        if args.get('conversation_id'):
            conversation, access = _scoped_conversation(store, principal, None, chat_id=args['conversation_id'])
            if access != 'read_write':
                raise PermissionError('chat_read_only_token')
            if args.get('account_ref') and args['account_ref'] != conversation['account_ref']:
                raise PermissionError('chat_or_account_outside_token_scope')
        else:
            grant = next((g for g in principal.messenger_grants or () if g['account_ref']==args.get('account_ref')), None)
            if grant is None or grant['access'] != 'read_write' or grant['chats'] is not None:
                raise PermissionError('account_scope_required_for_new_contact')
        return
    if name == 'userio.messages.send':
        if principal.read_only or not store.capability_enabled('send',user_id=principal.user_id):
            raise PermissionError('send_capability_disabled')
        draft_id = str(args.get('draft_id') or '').strip()
        if not draft_id:
            raise ValueError('draft_id is required')
        # Ownership first, exactly as on the approve path: a principal that does
        # not own the draft must not learn anything about it.
        draft = store.draft(draft_id, user_id=principal.user_id)
        _conversation, access = _scoped_conversation(store, principal, None,
                                                     chat_id=draft.conversation_id)
        if access != 'read_write':
            raise PermissionError('chat_read_only_token')
        return
    if name == 'userio.contacts.resolve':
        ref = str(args.get('account_ref') or '').strip()
        grant = next((g for g in principal.messenger_grants or () if g['account_ref'] == ref), None)
        if grant is None:
            raise PermissionError('chat_or_account_outside_token_scope')
        return
    if not name.startswith('userio.messenger.'):
        raise PermissionError('scoped_messenger_only')
    op = name.removeprefix('userio.messenger.')
    ref, cid = args.get('account_ref'), args.get('conversation_id')
    if op == 'has_contacted' and args.get('telegram_username') and not cid:
        grant = next((g for g in principal.messenger_grants if g['account_ref'] == ref), None)
        if grant is None or grant['chats'] is not None:
            raise PermissionError('account_scope_required_for_identity_history')
        return
    admin = op in {'telegram_admin.create', 'telegram_admin.propose'}
    admin_operation = args.get('operation', '') if admin else ''
    if op in {'get','approve'}:
        with store._lock:
            if not store._table_exists('messenger_actions'):
                raise KeyError('action_not_found')
            row = store._connection.execute('SELECT envelope FROM messenger_actions WHERE user_id=? AND id=?',
                (principal.user_id,args.get('action_id'))).fetchone()
        if row is None:
            raise KeyError('action_not_found')
        envelope = json.loads(row['envelope'])
        ref, cid = envelope['account_ref'], envelope['conversation_id']
        admin = envelope.get('action') == 'telegram_admin'
        admin_operation = envelope.get('operation', '') if admin else ''
    if admin:
        grant = next((g for g in principal.messenger_grants or () if g['account_ref'] == ref), None)
        if grant is None:
            raise PermissionError('chat_or_account_outside_token_scope')
        if not grant.get('telegram_admin', False):
            raise PermissionError('telegram_admin_permission_required')
        if admin_operation in {'group_create', 'channel_create'}:
            if grant['chats'] is not None:
                raise PermissionError('telegram_admin_create_requires_account_scope')
            cid = None
        else:
            if op == 'telegram_admin.propose':
                payload = args.get('payload') if isinstance(args.get('payload'), dict) else {}
                cid = payload.get('conversation_id')
            if not cid or grant_for(principal, ref, cid) is None:
                raise PermissionError('telegram_admin_chat_outside_token_scope')
        return
    elif op == 'resolve':
        with store._lock:
            row = store._connection.execute('SELECT id FROM conversations WHERE user_id=? AND account_ref=? AND peer_id=?',
                (principal.user_id,ref,str(args.get('peer_id','')))).fetchone()
        cid = row['id'] if row else '__unknown_chat__'
    access = grant_for(principal, ref, cid)
    if access is None:
        raise PermissionError('chat_or_account_outside_token_scope')
    if op not in READ_TOOLS and access != 'read_write':
        raise PermissionError('chat_read_only_token')
    # Operations bound to a message must always supply its chat; get/approve use
    # the durable action envelope. Account capabilities/events are filtered below.
    if op not in {'capabilities','events'} and not cid:
        raise PermissionError('exact_chat_required')


def _dispatch_scoped_channels(store, service, principal, name, arguments):
    from .adapters import UnifiedChannels, _public_channel
    from .mcp_catalog import TOOL_CAPABILITIES

    required = TOOL_CAPABILITIES.get(name)
    if required and not store.capability_enabled(required,user_id=principal.user_id):
        raise PermissionError(f'{required}_capability_disabled')
    channels = UnifiedChannels(store, service, principal.user_id)
    channel = arguments.get('channel')
    if name == 'userio.channels.list':
        ignored = arguments.get('ignored_chats', [])
        if not isinstance(ignored, list) or len(ignored) > 100 or any(not isinstance(x, str) for x in ignored):
            raise ValueError('invalid_ignored_chats')
        limit = arguments.get('limit', 100)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('limit_must_be_1_to_100')
        query = str(arguments.get('search') or '').strip()
        scoped = []
        owned = {account['id']:account for account in store.accounts(user_id=principal.user_id)}
        for grant in principal.messenger_grants:
            if arguments.get('account_ref') and arguments['account_ref'] != grant['account_ref']:
                continue
            account = owned.get(grant['account_ref'])
            if account is None or channel and account['provider'] != channel:
                continue
            if query:
                from .search_surface import search_response
                permitted = [c['conversation_id'] for c in grant['chats']] if grant['chats'] is not None else []
                requested = arguments.get('conversation_ids') or []
                if requested and permitted:
                    permitted = [cid for cid in permitted if cid in requested]
                    if not permitted:
                        continue
                elif requested and grant['chats'] is None:
                    permitted = requested
                result = search_response(store, query, {
                    **arguments, 'account_ref':grant['account_ref'], 'conversation_ids':permitted,
                }, user_id=principal.user_id, source=channel, ignored=ignored)
                scoped.extend(result.get('results') or [])
                continue
            rows = store.conversations(source=channel, account_ref=grant['account_ref'],
                                       limit=100, user_id=principal.user_id)
            allowed = None if grant['chats'] is None else {c['conversation_id'] for c in grant['chats']}
            for row in rows:
                if row['id'] in ignored or allowed is not None and row['id'] not in allowed:
                    continue
                scoped.append({
                    'id':row['id'], 'channel':_public_channel(str(row['source'])),
                    'title':row['display_name'] or row['identity_id'] or row['sender'],
                    'last_message_snippet':str(row['preview'] or '')[:500],
                    'unread':int(row['unread_count']),
                    '_sort_at':float(row.get('last_at') or 0),
                })
        if query:
            scoped.sort(key=lambda row:float(row.get('score') or row.get('received_at') or 0),reverse=True)
            return {'ok':True,'scope':arguments.get('scope') or 'conversation',
                    'results':scoped[:limit],'limit':limit}
        scoped.sort(key=lambda row:row.pop('_sort_at',0),reverse=True)
        return {'ok':True,'chats':scoped[:limit]}

    if name == 'userio.channels.search':
        from .search_surface import search_tool_response
        ignored = arguments.get('ignored_chats', [])
        if not isinstance(ignored, list) or len(ignored) > 100 or any(not isinstance(x, str) for x in ignored):
            raise ValueError('invalid_ignored_chats')
        limit = arguments.get('limit', 100)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('limit_must_be_1_to_100')
        owned = {account['id']: account for account in store.accounts(user_id=principal.user_id)}
        scoped = []
        for grant in principal.messenger_grants:
            if arguments.get('account_ref') and arguments['account_ref'] != grant['account_ref']:
                continue
            account = owned.get(grant['account_ref'])
            if account is None or channel and account['provider'] != channel:
                continue
            permitted = [c['conversation_id'] for c in grant['chats']] if grant['chats'] is not None else []
            requested = arguments.get('conversation_ids') or []
            if requested and permitted:
                permitted = [cid for cid in permitted if cid in requested]
                if not permitted:
                    continue
            elif requested and grant['chats'] is None:
                permitted = requested
            result = search_tool_response(store, {
                **arguments, 'account_ref': grant['account_ref'], 'conversation_ids': permitted,
            }, user_id=principal.user_id, source=channel, ignored=ignored)
            scoped.extend(result.get('results') or [])
        by_id = bool(arguments.get('conversation_id') or arguments.get('message_id'))
        return {'ok': True, 'scope': arguments.get('scope') or 'conversation',
                'results': scoped[:limit], 'limit': limit,
                'mode_used': 'exact_id' if by_id else 'text'}

    if name == 'userio.channels.export':
        from .chat_export import export_conversation
        conversation_id = str(arguments.get('conversation_id') or '').strip()
        if not conversation_id:
            raise ValueError('exact_chat_required')
        conversation, _ = _scoped_conversation(store, principal, channel, chat_id=conversation_id)
        result = export_conversation(
            store, str(conversation['id']), user_id=principal.user_id,
            include_images=bool(arguments.get('include_images')),
            since=arguments.get('since'), until=arguments.get('until'),
            max_messages=int(arguments.get('max_messages') or 20000),
        )
        if not result:
            raise KeyError('chat_not_found')
        markdown = str(result.pop('markdown'))
        result['preview'] = markdown[:2000]
        if len(markdown.encode('utf-8')) <= 32768:
            result['content'] = markdown
        return result

    if name == 'userio.channels.read':
        ignored = arguments.get('ignored_chats', [])
        if not isinstance(ignored, list) or len(ignored) > 100 or any(not isinstance(x, str) for x in ignored):
            raise ValueError('invalid_ignored_chats')
        adapter = channels.adapter(channel)
        if channel == 'telegram':
            result = adapter.read(chat_id=arguments.get('chat_id'), message_id=arguments.get('message_id'),
                before_id=arguments.get('before_id'), history_limit=arguments.get('history_limit',50))
        else:
            result = adapter.read(chat_id=arguments.get('chat_id'), message_id=arguments.get('message_id'))
        record = result.get('chat') or result.get('message') or {}
        if str(record.get('conversation_id') or record.get('id') or '') in ignored:
            raise KeyError('chat_not_found')
        from .mcp_surface import UserIOMcpSurface
        return UserIOMcpSurface(store,service,principal)._include_read_media(
            {'ok':True,**result},arguments=arguments,user_id=principal.user_id)

    if name == 'userio.channels.download':
        file_ref = str(arguments['file_ref'])
        attachment_index = arguments.get('attachment_index',0)
        if type(attachment_index) is not int or not 0<=attachment_index<8:
            raise ValueError('invalid_attachment_index')
        mode = arguments.get('mode','base64')
        if mode == 'mcp_resource':
            include_images = arguments.get('include_images',True)
            if type(include_images) is not bool: raise ValueError('include_images_must_be_boolean')
            channel_ref, separator, provider_ref = file_ref.partition(':')
            if not separator or channel_ref not in {'telegram','max'}:
                raise ValueError('mcp_resource_mode_requires_telegram_or_max')
            from .mcp_files import MAX_MCP_FILE_BYTES, mcp_attachment
            if channel_ref=='telegram':
                download_args={'file_ref':provider_ref,'max_bytes':MAX_MCP_FILE_BYTES}
                if attachment_index:
                    download_args['attachment_index']=attachment_index
                file=channels.adapter('telegram').download_for_ai(**download_args)
            else:
                file = channels.download(file_ref=file_ref,attachment_index=attachment_index,
                                         max_bytes=MAX_MCP_FILE_BYTES)
            metadata, blocks = mcp_attachment(file,include_images=include_images)
            return {'ok':True,'file':metadata,'_mcp_content_blocks':blocks}
        if mode != 'base64':
            raise ValueError('invalid_download_mode')
        from .mcp_files import MAX_MCP_FILE_BYTES
        channel_ref,_,provider_ref=file_ref.partition(':')
        if channel_ref=='telegram':
            download_args={'file_ref':provider_ref,'max_bytes':MAX_MCP_FILE_BYTES}
            if attachment_index:
                download_args['attachment_index']=attachment_index
            file=channels.adapter('telegram').download_for_ai(**download_args)
        else:
            file = channels.download(file_ref=file_ref,attachment_index=attachment_index,
                                     max_bytes=MAX_MCP_FILE_BYTES)
        import base64
        return {'ok':True,'file':{'filename':file.filename,'content_type':file.content_type,
            'size':len(file.data),'sha256':hashlib.sha256(file.data).hexdigest(),
            'encoding':'base64','data':base64.b64encode(file.data).decode('ascii')}}

    raise KeyError('unknown_scoped_channel_operation')


def _dispatch_scoped_contacts(store, service, principal, name, arguments):
    from .contact_start import ContactStartError

    account_ref = str(arguments.get('account_ref') or '').strip()
    identifier = str(arguments.get('identifier') or '').strip()
    if not account_ref or not identifier:
        raise ValueError('account_ref and identifier are required')
    starter = service.contact_starter
    try:
        if name == 'userio.contacts.resolve':
            resolved = starter.resolve(user_id=principal.user_id, account_ref=account_ref,
                                       identifier=identifier, need_send=False)
            return {'ok': True, 'peer': resolved.as_dict()}
        raise KeyError('unknown_contact_operation')
    except ContactStartError as error:
        return {'ok': False, 'error': str(error), 'code': error.code}


def dispatch_scoped(store, service, principal, name, arguments):
    from .messenger import Messenger, dispatch
    enforce(store, principal, name, arguments)
    op = name.removeprefix('userio.messenger.')
    if name == 'userio.messages.prepare':
        return service.contact_starter.prepare(user_id=principal.user_id, **arguments)
    if name == 'userio.messages.send':
        if not store.send_enabled(user_id=principal.user_id):
            return {'ok': False, 'error': 'outbound_delivery_disabled'}
        # The same approved-send code path as the unscoped route, so a scoped
        # token cannot reach weaker send semantics than an owner token.
        from .mcp_surface import UserIOMcpSurface
        return UserIOMcpSurface(store, service, principal)._approve(arguments, principal)
    if name.startswith('userio.channels.'):
        return _dispatch_scoped_channels(store,service,principal,name,arguments)
    if name.startswith('userio.contacts.'):
        return _dispatch_scoped_contacts(store,service,principal,name,arguments)
    if op != 'events':
        result = dispatch(store,service,principal,name,arguments)
        if op == 'capabilities' and result.get('ok') and grant_for(principal, arguments.get('account_ref')) == 'read_only':
            for operation, value in result['operations'].items():
                if operation not in {'read','history','has_contacted','download'}:
                    value.update(allowed=False, reason='chat_read_only_token')
        return result
    api = Messenger(store,service,principal)
    ref, after, limit = arguments['account_ref'], arguments.get('after'), arguments.get('limit',50)
    api._allow(ref,'read')
    if after is not None and (type(after) is not int or not 0 <= after < 2**63):
        raise ValueError('invalid_event_cursor')
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('limit_must_be_1_to_100')
    grant = next(g for g in principal.messenger_grants if g['account_ref']==ref)
    chats = None if grant['chats'] is None else [c['conversation_id'] for c in grant['chats']]
    clause = '' if chats is None else ' AND e.conversation_id IN ('+','.join('?' for _ in chats)+')'
    params = [principal.user_id,ref,*(chats or [])]
    with store._lock:
        head = store._connection.execute('''SELECT COALESCE(MAX(e.seq),0) FROM workspace_events e
            JOIN conversations c ON c.user_id=e.user_id AND c.id=e.conversation_id
            WHERE e.user_id=? AND c.account_ref=?'''+clause,params).fetchone()[0]
        rows = [] if after is None else store._connection.execute('''SELECT e.seq,e.eligible,e.message_id,e.conversation_id
            FROM workspace_events e JOIN conversations c ON c.user_id=e.user_id AND c.id=e.conversation_id
            JOIN messages m ON m.user_id=e.user_id AND m.source=e.source AND m.message_id=e.message_id
            WHERE e.user_id=? AND c.account_ref=?'''+clause+''' AND e.seq>? AND e.reconciled=0
            AND m.direction='incoming' AND m.deleted_at IS NULL ORDER BY e.seq LIMIT ?''',[*params,after,limit+1]).fetchall()
    events = [{'seq':r['seq'], 'analysis_eligible':bool(r['eligible']),
        'message':api._project(api._conversation(ref,r['conversation_id']),api._message(api._conversation(ref,r['conversation_id']),r['message_id']))}
        for r in rows[:limit]]
    more = len(rows)>limit
    return {'ok':True,'schema':'userio.messenger.v1','account_ref':ref,'events':events,'head':head,
        'next_cursor':events[-1]['seq'] if more else max(head,after or 0),'has_more':more,
        'initialized':after is None,'acknowledged':False}
