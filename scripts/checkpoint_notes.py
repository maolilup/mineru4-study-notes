"""Save note work and review records with hashes; verify snapshots before resuming."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

from check_note_coverage import digest, target
from package_notes import references


def create(sections, notes, progress_path, out, records=None):
    root, out = Path(sections).resolve(), Path(out).resolve()
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ValueError('Checkpoint directory must be empty or nonexistent')
    index = json.loads((root / 'sections.json').read_text(encoding='utf-8'))
    source = (root / index['source_pack']).resolve()
    if out.is_relative_to(source):
        raise ValueError('Checkpoint must be outside the immutable source pack')
    progress_path = Path(progress_path).resolve()
    progress = json.loads(progress_path.read_text(encoding='utf-8'))
    known = {x['block_id'] for x in index['expected']}
    if (not isinstance(progress, dict) or 'next_block_id' not in progress
            or (progress['next_block_id'] is not None and not isinstance(progress['next_block_id'], str))
            or progress['next_block_id'] not in known | {index.get('end_exclusive'), None}
            or not isinstance(progress.get('pending_items'), list)
            or any(not isinstance(x, str) for x in progress['pending_items'])):
        raise ValueError('Progress requires an in-scope next_block_id (or null) and pending_items array')
    # A null continuation point is not a claim of faithful translation or sufficient teaching.
    files = {}
    def include(path, role):
        path = Path(path).resolve()
        if not path.is_file() or path.is_relative_to(out):
            raise ValueError(f'Missing or unsafe checkpoint input: {path}')
        roles = files.setdefault(path, [])
        if role not in roles:
            roles.append(role)
    ordered_notes = list(dict.fromkeys(Path(n).resolve() for n in notes))
    if not ordered_notes:
        raise ValueError('At least one note file is required')
    external_images = []
    for path in ordered_notes:
        include(path, 'note')
        for _, _, is_image, url in references(path.read_text(encoding='utf-8')):
            resolved = target(path, url)
            if resolved is not None:
                if not resolved.is_file():
                    raise ValueError(f'Missing linked file in {path}: {url}')
                include(resolved, 'linked_image' if is_image else 'linked_file')
            elif is_image:
                external_images.append({'file': str(path), 'url': url})
    for path in records or []:
        include(path, 'review_record')
    include(progress_path, 'progress_record')
    include(root / 'sections.json', 'section_index')
    include(root / 'resources.json', 'resource_index')
    hashes = {}
    source_files = []
    for name in ('manifest.json', 'document.json', 'blocks.json', 'assets.json'):
        path = source / name
        if not path.is_file():
            raise ValueError(f'Missing source pack file: {path}')
        source_files.append({'path': str(path), 'sha256': digest(path, hashes)})
    planned = []
    for i, (path, roles) in enumerate(files.items(), 1):
        planned.append({'roles': roles, 'original_path': str(path),
                        'snapshot_path': f'files/{i:04d}{path.suffix}',
                        'bytes': path.stat().st_size, 'sha256': digest(path, hashes)})
    out.mkdir(parents=True, exist_ok=True)
    for record in planned:
        destination = out / record['snapshot_path']
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(record['original_path'], destination)
        if digest(destination, {}) != record['sha256'] or digest(Path(record['original_path']), {}) != record['sha256']:
            raise ValueError(f'File changed during checkpoint: {record["original_path"]}')
    result = {'format': 'mineru4-work-checkpoint/1',
              'recorded_at': datetime.now(timezone.utc).isoformat(), 'source_id': index['source_id'],
              'scope': {'start': index['start'], 'end_exclusive': index['end_exclusive']},
              'section_pack': str(root), 'notes_in_reading_order': [str(p) for p in ordered_notes],
              'progress': progress, 'files': planned, 'source_files': source_files,
              'external_images': external_images,
              'limits': 'Work snapshot only. Source pack remains at its original location; external files are not downloaded. '
                        'Snapshot filenames preserve bytes for recovery, not a browsable portable publication.'}
    (out / 'checkpoint.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return result


def verify(checkpoint, current=False):
    root = Path(checkpoint).resolve()
    record = json.loads((root / 'checkpoint.json').read_text(encoding='utf-8'))
    if record.get('format') != 'mineru4-work-checkpoint/1':
        raise ValueError('Unknown checkpoint format')
    failures = []
    for item in record['files']:
        snapshot = (root / item['snapshot_path']).resolve()
        if not snapshot.is_relative_to(root):
            raise ValueError('Unsafe snapshot path')
        if not snapshot.is_file() or digest(snapshot, {}) != item['sha256']:
            failures.append({'kind': 'snapshot_changed_or_missing', 'path': str(snapshot)})
        if current:
            original = Path(item['original_path'])
            if not original.is_file() or digest(original, {}) != item['sha256']:
                failures.append({'kind': 'current_work_changed_or_missing', 'path': str(original)})
    if current:
        for item in record['source_files']:
            source = Path(item['path'])
            if not source.is_file() or digest(source, {}) != item['sha256']:
                failures.append({'kind': 'source_changed_or_missing', 'path': str(source)})
    return {'checkpoint': str(root), 'verified_current_files': current, 'failures': failures,
            'hashes_match': not failures, 'progress': record['progress'],
            'limits': 'Hash equality identifies saved bytes; it does not certify content quality.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    save = commands.add_parser('create')
    save.add_argument('--sections', required=True)
    save.add_argument('--notes', nargs='+', required=True, help='Markdown notes in reading order')
    save.add_argument('--progress', required=True, help='JSON with next_block_id and pending_items')
    save.add_argument('--records', nargs='*', help='Explicit coverage/source/teaching review record files')
    save.add_argument('--out', required=True)
    inspect = commands.add_parser('verify')
    inspect.add_argument('--checkpoint', required=True)
    inspect.add_argument('--current', action='store_true', help='Also compare current notes, records and source-pack hashes')
    args = parser.parse_args()
    if args.command == 'create':
        result = create(args.sections, args.notes, args.progress, args.out, args.records)
        print(json.dumps({'checkpoint': str(Path(args.out).resolve()), 'files': len(result['files']),
                          'next_block_id': result['progress']['next_block_id']}, ensure_ascii=False))
    else:
        result = verify(args.checkpoint, args.current)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(0 if result['hashes_match'] else 1)
