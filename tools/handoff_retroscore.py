#!/usr/bin/env python3
"""
Retroactive behavioral scoring for existing raw.jsonl runs.
Reads each entry, applies behavioral_check on stored output, writes back.
Only rewrites entries missing 'behavioral_score' or where signal=='sweep'.
"""
import sys

import json
import os
import glob

from handoff_behavioral import behavioral_check
from handoff_batch import score_batch_run


def retroscore_file(path: str) -> dict:
    """Recompute behavioral_score for all entries in path. Returns stats."""
    lines = []
    with open(path, 'r') as f:
        lines = f.readlines()

    updated = 0
    already_scored = 0
    new_lines = []
    for line in lines:
        line = line.rstrip('\n')
        if not line.strip():
            new_lines.append(line)
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            new_lines.append(line)
            continue

        signal = entry.get('signal', '')
        level = entry.get('level', 0)
        output = entry.get('output', '')
        workdir_content = entry.get('workdir_snippet', '')

        if signal == 'batch':
            batch_metrics = score_batch_run(output, level)
            entry.update(batch_metrics)
            bscore = batch_metrics['batch_score']
        else:
            bscore = behavioral_check(output, signal, level,
                                      workdir_content=workdir_content)

        old_score = entry.get('behavioral_score', None)
        entry['behavioral_score'] = bscore

        if old_score != bscore:
            updated += 1
        else:
            already_scored += 1

        new_lines.append(json.dumps(entry))

    # Write back
    with open(path, 'w') as f:
        f.write('\n'.join(new_lines) + '\n')

    return {'updated': updated, 'already_scored': already_scored, 'total': len(lines)}


def main():
    # Find all raw.jsonl files
    pattern = os.path.expanduser('~/.handoff/runs/*/raw.jsonl')
    files = sorted(glob.glob(pattern))

    if not files:
        print('No raw.jsonl files found in ~/.handoff/runs/')
        return

    print(f'Found {len(files)} run files\n')
    total_updated = 0
    total_entries = 0

    for path in files:
        run_name = os.path.basename(os.path.dirname(path))
        stats = retroscore_file(path)
        changed = stats['updated']
        total = stats['total']
        total_updated += changed
        total_entries += total
        status = f'  {changed:3d} updated' if changed else '  no changes'
        print(f'{run_name:<50} {total:4d} entries{status}')

    print(f'\nTotal: {total_entries} entries, {total_updated} behavioral scores updated')


if __name__ == '__main__':
    main()
