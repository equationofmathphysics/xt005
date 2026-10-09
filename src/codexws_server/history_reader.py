"""Bounded, read-only pages of native JSONL history, without a running agent."""
import json

MAX_RECORD = 2 * 1024 * 1024


def read_page(path, cursor=0, limit=80):
    records = []
    with open(path, 'rb') as handle:
        if cursor < 0:
            raise ValueError('cursor must be nonnegative')
        handle.seek(0, 2)
        end = handle.tell()
        if cursor > end:
            raise ValueError('history changed; reload from the beginning')
        if cursor:
            handle.seek(cursor - 1)
            if handle.read(1) != b'\n':
                raise ValueError('cursor is not a record boundary')
        handle.seek(cursor)
        # Bound both memory and scan work even for metadata-only pages.
        for _ in range(limit):
            start = handle.tell()
            raw = handle.readline(MAX_RECORD + 1)
            if not raw:
                break
            if len(raw) > MAX_RECORD:
                # Do not skip arbitrary bytes or lose the user's place.
                records.append({'type': 'notice', 'text': '记录超过 2 MiB；请从本地历史文件查看。', 'offset': start})
                return {'records': records, 'nextCursor': None, 'truncated': True}
            if not raw.endswith(b'\n'):
                handle.seek(start)
                break  # A live writer has not completed this record yet.
            try:
                row = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                records.append({'type': 'invalid', 'text': raw.decode('utf-8', 'replace'), 'offset': start})
                continue
            if not isinstance(row, dict):
                records.append({'type': 'record', 'text': json.dumps(row, ensure_ascii=False), 'offset': start})
                continue
            payload = row.get('payload') or {}
            kind = str(row.get('type') or 'record')
            if kind in {'session_meta', 'world_state', 'turn_context', 'token_usage_record'}:
                continue
            text = None
            if isinstance(payload, dict):
                if kind == 'response_item' and payload.get('type') == 'message':
                    kind = str(payload.get('role') or 'message')
                    text = '\n'.join(str(item.get('text') or '') for item in payload.get('content', []) if isinstance(item, dict))
                elif kind == 'event_msg' and payload.get('type') in {'user_message', 'agent_message'}:
                    kind = str(payload['type'])
                    text = str(payload.get('message') or '')
            if text is None:
                text = json.dumps(row, ensure_ascii=False, indent=2)
            records.append({'type': kind, 'text': text, 'offset': start})
        offset = handle.tell()
    return {'records': records, 'nextCursor': offset if offset < end else None, 'truncated': False}
