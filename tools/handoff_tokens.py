#!/usr/bin/env python3
"""
handoff_tokens.py — per-step token accounting of the delegation loop.

Segments a Claude Code session transcript into the delegation-loop phases
(plan → delegate prompt → [sub-model implements] → verify → corrections) and
reports input / cache-write / cache-read / output tokens per phase, plus the
sub-model side pulled from ~/.local/share/delegate-runs.jsonl.

Usage:
    python3 handoff_tokens.py <session.jsonl> [--project NAME]

Phase attribution (honest about its limits):
  - Each assistant API call's usage is assigned to the phase it occurs in.
  - cache_read in a phase = the accumulated context being carried at that point,
    not "caused" by that phase — it shows how context grows as a loop deepens.
  - A "span" is the work between two genuine user instructions. Spans with no
    delegation are bucketed as DIRECT so the accounting stays complete.
"""
import json, os, sys

# relative price units (Claude): input 1, cache-write 1.25, cache-read 0.1, output 5
P_IN, P_CW, P_CR, P_OUT = 1.0, 1.25, 0.10, 5.0
DELEGATE_LOG = os.path.expanduser('~/.local/share/delegate-runs.jsonl')


def _is_human_instruction(o):
    """True if this 'user' event is a real human message, not a tool_result return."""
    if o.get('type') != 'user':
        return False
    msg = o.get('message') if isinstance(o.get('message'), dict) else {}
    c = msg.get('content')
    if isinstance(c, str):
        return c.strip() != ''
    if isinstance(c, list):
        has_text = any(isinstance(b, dict) and b.get('type') == 'text' and b.get('text', '').strip()
                       for b in c)
        only_toolresult = all(isinstance(b, dict) and b.get('type') == 'tool_result' for b in c)
        return has_text and not only_toolresult
    return False


def _emits_delegate(msg):
    for b in (msg.get('content') or []) if isinstance(msg.get('content'), list) else []:
        if isinstance(b, dict) and b.get('type') == 'tool_use' and b.get('name') == 'Bash':
            if 'vibe-delegate' in (b.get('input') or {}).get('command', ''):
                return True
    return False


def _usage(o):
    msg = o.get('message') if isinstance(o.get('message'), dict) else {}
    return msg.get('usage') or o.get('usage')


def analyze(session_path, project=None):
    events = []
    ts0 = ts1 = None
    for line in open(session_path):
        try:
            o = json.loads(line)
        except Exception:
            continue
        t = o.get('timestamp') or o.get('ts')
        if t:
            ts0 = ts0 or t
            ts1 = t
        events.append(o)

    # Pass 1: assign a span id to every event; mark which spans delegate.
    span = -1
    span_delegates = {}
    for o in events:
        if _is_human_instruction(o):
            span += 1
            span_delegates.setdefault(span, False)
        o['_span'] = span
        if o.get('type') == 'assistant' and _emits_delegate(o.get('message', {})):
            span_delegates[span] = True

    # Pass 2: classify each assistant API call into a phase, sum usage.
    # Within a delegation span: PLAN until the 1st delegate emit, then VERIFY;
    # 2nd+ emits and everything after them are CORRECTIONS. Non-delegation spans
    # are bucketed as DIRECT.
    phases = ['PLAN', 'DELEGATE_EMIT', 'VERIFY', 'CORRECTIONS', 'DIRECT']
    agg = {p: dict(calls=0, inp=0, cw=0, cr=0, out=0) for p in phases}
    cur_span = None
    deleg_count = 0
    phase = 'DIRECT'
    for o in events:
        s = o.get('_span', -1)
        if s != cur_span:
            cur_span = s
            deleg_count = 0
            phase = 'PLAN' if span_delegates.get(s) else 'DIRECT'
        if o.get('type') != 'assistant':
            continue
        u = _usage(o)
        if not u:
            continue
        emits = _emits_delegate(o.get('message', {}))
        if emits:
            deleg_count += 1
            bucket = 'DELEGATE_EMIT' if deleg_count == 1 else 'CORRECTIONS'
        else:
            bucket = phase
        a = agg[bucket]
        a['calls'] += 1
        a['inp'] += u.get('input_tokens', 0)
        a['cw'] += u.get('cache_creation_input_tokens', 0)
        a['cr'] += u.get('cache_read_input_tokens', 0)
        a['out'] += u.get('output_tokens', 0)
        if emits:
            phase = 'VERIFY' if deleg_count == 1 else 'CORRECTIONS'

    # Sub-model side: delegations within the session time window (+ project filter).
    sub = dict(n=0, tin=0, tout=0, cost=0.0)
    if os.path.exists(DELEGATE_LOG) and ts0 and ts1:
        for line in open(DELEGATE_LOG):
            try:
                r = json.loads(line)
            except Exception:
                continue
            t = r.get('ts', '')
            if not (ts0[:19] <= t[:19] <= ts1[:19]):
                continue
            if project and r.get('project') != project:
                continue
            sub['n'] += 1
            sub['tin'] += r.get('tokens_in', 0)
            sub['tout'] += r.get('tokens_out', 0)
            sub['cost'] += r.get('cost_usd', 0.0)

    return agg, sub, (ts0, ts1)


def _cost(a):
    return a['inp'] * P_IN + a['cw'] * P_CW + a['cr'] * P_CR + a['out'] * P_OUT


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    session = sys.argv[1]
    project = None
    if '--project' in sys.argv:
        project = sys.argv[sys.argv.index('--project') + 1]

    agg, sub, (ts0, ts1) = analyze(session, project)

    print('=' * 84)
    print(f"  Delegation-loop token accounting — {os.path.basename(session)}")
    print(f"  span: {ts0} → {ts1}")
    print('=' * 84)
    print("  ORCHESTRATOR (Claude meter) — tokens per loop phase")
    print(f"  {'phase':<14} {'calls':>5} {'input':>9} {'cache_wr':>10} {'cache_rd':>12} {'output':>9} {'cost_u':>11} {'cost%':>6}")
    print('  ' + '-' * 80)
    total_cost = sum(_cost(a) for a in agg.values()) or 1
    order = ['PLAN', 'DELEGATE_EMIT', 'VERIFY', 'CORRECTIONS', 'DIRECT']
    tot = dict(calls=0, inp=0, cw=0, cr=0, out=0)
    for p in order:
        a = agg[p]
        if a['calls'] == 0:
            continue
        c = _cost(a)
        print(f"  {p:<14} {a['calls']:>5} {a['inp']:>9,} {a['cw']:>10,} {a['cr']:>12,} {a['out']:>9,} {c:>11,.0f} {100*c/total_cost:>5.1f}%")
        for k in tot:
            tot[k] += a[k]
    print('  ' + '-' * 80)
    c = _cost(tot)
    print(f"  {'TOTAL':<14} {tot['calls']:>5} {tot['inp']:>9,} {tot['cw']:>10,} {tot['cr']:>12,} {tot['out']:>9,} {c:>11,.0f} 100.0%")
    print()
    print("  SUB-MODEL (separate meter — NOT on your Claude quota)")
    print(f"    delegations in window : {sub['n']}")
    print(f"    tokens_in             : {sub['tin']:,}")
    print(f"    tokens_out            : {sub['tout']:,}")
    print(f"    cost_usd              : ${sub['cost']:.2f}")
    print()
    print("  Note: cache_read in a phase = accumulated context carried at that point,")
    print("  not caused by that phase. The sub-model row is the work moved off-quota.")


if __name__ == '__main__':
    main()
