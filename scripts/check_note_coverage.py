"""Check material presence and optional strict structure, not semantic fidelity."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

MARKER = re.compile(r'<!--\s*block_id=([^\s>]+)\s*-->')
LINK = re.compile(r'(!?)\[[^\]]*\]\(\s*(?:<([^>]+)>|([^\s)]+))(?:\s+"[^"]*")?\s*\)')


def mask_code(text):
    # Do not mistake documentation examples inside code for real links/markers.
    masked = re.sub(r'(?ms)^\s*(`{3,}|~{3,})[^\n]*\n.*?^\s*\1\s*$',
                    lambda m: ' ' * len(m[0]), text)
    return re.sub(r'(?s)(?<!`)(`+)(?!`).*?(?<!`)\1(?!`)',
                  lambda m: ' ' * len(m[0]), masked)


def links(text):
    text = mask_code(text)
    for match in LINK.finditer(text):
        yield bool(match[1]), match[2] or match[3]
    for match in re.finditer(r'<img\b[^>]*\bsrc=[\"\']([^\"\']+)', text, re.I):
        yield True, match[1]


def target(path, url):
    parsed = urlsplit(url)
    if parsed.scheme or url.startswith('//') or not parsed.path:
        return None
    return (path.parent / unquote(parsed.path)).resolve()


def compact(text):
    return re.sub(r'\s+', '', text)


def has_body(body):
    return bool(re.sub(r'<!--.*?-->', '', body, flags=re.S).strip())


def direct_segments(text):
    """Expose empty bodies before merging adjacent source markers."""
    matches = list(MARKER.finditer(mask_code(text)))
    for i, match in enumerate(matches):
        stop = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        yield match[1], text[match.end():stop]


def segments(text):
    pending = []
    for bid, body in direct_segments(text):
        pending.append(bid)
        if not has_body(body):
            continue
        for bid in pending:
            yield bid, body
        pending = []
    for bid in pending:
        yield bid, ''


def digest(path, cache):
    if path not in cache:
        sha = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                sha.update(chunk)
        cache[path] = sha.hexdigest()
    return cache[path]


def reviewed_layout(index, review):
    """Allow only explicit, source-bound, non-overlapping layout exceptions."""
    canonical = [x['block_id'] for x in index['expected']]
    allowed_order = list(canonical)
    if review is None:
        return allowed_order, set()
    if isinstance(review, (str, Path)):
        review = json.loads(Path(review).read_text(encoding='utf-8'))
    if not isinstance(review, dict) or review.get('format') != 'mineru4-layout-review/1':
        raise ValueError('Layout review must use mineru4-layout-review/1')
    if review.get('source_id') != index['source_id']:
        raise ValueError('Layout review source_id does not match section pack')
    known, groups, occupied = set(canonical), set(), set()
    for field in ('merged_blocks', 'order_adjustments'):
        records = review.get(field, [])
        if not isinstance(records, list):
            raise ValueError(f'{field} must be an array')
        for item in records:
            if not isinstance(item, dict) or any(not isinstance(item.get(k), str) or not item[k].strip()
                                                 for k in ('reason', 'evidence')):
                raise ValueError('Every layout exception requires reason and source-review evidence')
            ids = item.get('block_ids' if field == 'merged_blocks' else 'source_order')
            if (not isinstance(ids, list) or len(ids) < 2 or not all(isinstance(x, str) for x in ids)
                    or len(set(ids)) != len(ids) or not set(ids).issubset(known)):
                raise ValueError('Layout exception must identify at least two distinct expected block IDs')
            start = canonical.index(ids[0])
            if canonical[start:start + len(ids)] != ids:
                raise ValueError('Layout exception must cover a contiguous range in source order')
            if field == 'merged_blocks':
                group = tuple(ids)
                if group in groups:
                    raise ValueError('Duplicate merge exception')
                groups.add(group)
            else:
                permutation = item.get('note_order')
                if (not isinstance(permutation, list) or not all(isinstance(x, str) for x in permutation)
                        or len(permutation) != len(ids) or set(permutation) != set(ids)):
                    raise ValueError('note_order must be an exact permutation of source_order')
                if occupied.intersection(ids):
                    raise ValueError('Order adjustments must not overlap')
                occupied.update(ids)
                allowed_order[start:start + len(ids)] = permutation
    return allowed_order, groups


def check(section_pack, notes, strict=False, layout_review=None):
    root = Path(section_pack).resolve()
    index = json.loads((root / 'sections.json').read_text(encoding='utf-8'))
    resources = {r['block_id']: r for r in json.loads((root / 'resources.json').read_text(encoding='utf-8'))}
    source_pack = (root / index['source_pack']).resolve()
    expected = {x['block_id']: x for x in index['expected']}
    if len(expected) != len(index['expected']):
        raise ValueError('Duplicate block IDs in section pack')
    allowed_order, merge_groups = reviewed_layout(index, layout_review)
    occurrences, broken, missing, review, present = defaultdict(list), [], [], [], []
    # The caller supplies chapter/read order; alphabetical order is not source order.
    files = list(dict.fromkeys(Path(n).resolve() for n in notes))
    if not files:
        raise ValueError('At least one note file is required')
    direct_order, empty, merges, hashes, asset_matches = [], [], [], {}, []
    for path in files:
        text = path.read_text(encoding='utf-8')
        for _, url in links(text):
            resolved = target(path, url)
            if resolved is not None and not resolved.is_file():
                broken.append({'file': str(path), 'url': url})
        pending, pending_empty = [], []
        for bid, body in direct_segments(text):
            direct_order.append(bid)
            pending.append(bid)
            if not has_body(body):
                item = {'block_id': bid, 'file': str(path), 'reviewed_merge': False}
                empty.append(item)
                pending_empty.append(item)
                continue
            if len(pending) > 1:
                accepted = tuple(pending) in merge_groups
                merges.append({'file': str(path), 'block_ids': list(pending), 'reviewed': accepted})
                for item in pending_empty:
                    item['reviewed_merge'] = accepted
            pending, pending_empty = [], []
        for bid, body in segments(text):
            occurrences[bid].append((path, body))
    outside = sorted(set(occurrences) - set(expected))
    observed = list(dict.fromkeys(bid for bid in direct_order if bid in expected))
    rank = {bid: i for i, bid in enumerate(allowed_order)}
    order_issues = [{'previous': left, 'current': right}
                    for left, right in zip(observed, observed[1:]) if rank[left] > rank[right]]
    for bid, block in expected.items():
        entries = occurrences.get(bid, [])
        if not entries:
            missing.append({'block_id': bid, 'reason': 'no source marker'})
            continue
        resource = resources.get(bid)
        if not resource:
            if any(has_body(body) for _, body in entries):
                present.append(bid)
            else:
                missing.append({'block_id': bid, 'reason': 'marker without content'})
            continue
        paths = set()
        urls = set()
        for a in resource['assets']:
            if urlsplit(a['path']).scheme or a['path'].startswith('//'):
                urls.add(a['path'])
            else:
                paths.add((source_pack / a['path']).resolve())
        found_paths, found_urls = set(), set()
        for path, body in entries:
            for image, url in links(body):
                if image:
                    resolved = target(path, url)
                    if resolved is not None and resolved.is_file():
                        found_paths.add(resolved)
                    found_urls.add(url)
        matched_paths = set()
        for source in sorted(paths):
            if not source.is_file():
                continue
            sha = digest(source, hashes)
            candidates = sorted(found_paths, key=lambda p: (p != source, str(p)))
            copy = next((p for p in candidates if digest(p, hashes) == sha), None)
            if copy is not None:
                matched_paths.add(source)
                asset_matches.append({'block_id': bid, 'source_path': str(source),
                                      'note_path': str(copy), 'sha256': sha,
                                      'method': 'source_path' if source == copy else 'identical_bytes'})
        image_ok = bool(paths or urls) and paths.issubset(matched_paths) and urls.issubset(found_urls)
        content = resource.get('content', '')
        kind = resource['type']
        # Require actual math/code/table syntax; a nearby reference number is insufficient.
        if kind == 'equation':
            regions = [m.group(1) for _, body in entries for m in re.finditer(r'\$\$(.*?)\$\$', body, re.S)]
            regions += [m.group(1) for _, body in entries for m in re.finditer(r'\\\[(.*?)\\\]', body, re.S)]
        elif kind == 'code':
            regions = [m.group(0) for _, body in entries for m in re.finditer(r'```[^\n]*\n.*?```', body, re.S)]
        elif kind == 'table':
            regions = [body for _, body in entries if '<table' in body.lower() or re.search(r'\|\s*:?-{3,}', body)]
        else:
            regions = []
        exact = bool(content.strip()) and any(compact(content) in compact(region) for region in regions)
        if image_ok or exact:
            present.append(bid)
        elif regions:
            review.append({'block_id': bid, 'reason': 'representation differs; review content/OCR correction'})
        else:
            missing.append({'block_id': bid, 'reason': 'resource marker without matching image or material region'})
    duplicates = [bid for bid, entries in occurrences.items() if len(entries) > 1]
    unreviewed_empty = [item for item in empty if not item['reviewed_merge']]
    structural_issues = bool(duplicates or order_issues or unreviewed_empty)
    failed = bool(missing or review or broken or outside or (strict and structural_issues))
    return {'scope': {'start': index['start'], 'end_exclusive': index['end_exclusive']},
            'source_id': index['source_id'], 'notes_in_reading_order': [str(p) for p in files],
            'expected_blocks': len(expected), 'material_present': len(present),
            'missing': missing, 'needs_review': review, 'broken_links': broken,
            'out_of_scope': outside, 'duplicate_markers': duplicates,
            'source_order_issues': order_issues, 'empty_direct_bodies': empty,
            'adjacent_merges': merges, 'asset_matches': asset_matches,
            'resource_anomalies': [x for x in index.get('issues', []) if x.get('kind', '').startswith('resource_')],
            'strict': strict, 'check_passed': not failed,
            'limits': 'Presence and structure only. Text markers do not prove complete translation; '
                      'hash matches do not prove visual readability; source and teaching review still required.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sections', required=True)
    parser.add_argument('--notes', nargs='+', required=True, help='Explicit Markdown paths in chapter/read order')
    parser.add_argument('--strict', action='store_true', help='Fail on duplicates, wrong source order and unreviewed empty bodies')
    parser.add_argument('--layout-review', help='Source-bound JSON of reviewed merges and reading-order adjustments')
    parser.add_argument('--report', help='Optional JSON report path')
    args = parser.parse_args()
    report = check(args.sections, args.notes, args.strict, args.layout_review)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        Path(args.report).write_text(output, encoding='utf-8')
    print(output)
    raise SystemExit(0 if report['check_passed'] else 1)
