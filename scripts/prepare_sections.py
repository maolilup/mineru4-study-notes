"""Prepare lossless, bounded section inputs from an existing MinerU 4 pack."""
import argparse
from collections import defaultdict
import json
import os
from pathlib import Path
import re
from urllib.parse import quote

from extract_document import external, heading_level, safe_relative

TITLES = {'doc_title', 'paragraph_title', 'title'}
NOISE = {'header', 'footer', 'page_number', 'index'}
RESOURCES = {'image', 'chart', 'equation', 'code', 'table'}


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def normalized(text):
    return re.sub(r'\s+', ' ', text).strip().casefold()


def local_asset(pack, path):
    target = (pack / str(safe_relative(path))).resolve()
    if not target.is_relative_to(pack) or not target.is_file():
        raise ValueError(f'Missing or unsafe asset: {path}')
    return target


def relative_link(target, directory):
    return quote(Path(os.path.relpath(target, directory)).as_posix(), safe='/:')


def resource_hints(block, kind, assets):
    """Flag narrow syntax/caption conflicts; never infer missing source content."""
    bid, content = block['block_id'], block.get('content', '')
    caption = ' '.join(x.get('content', '') for x in block.get('captions', []))
    hints = []
    def add(name, detail):
        hints.append({'block_id': bid, 'kind': 'resource_' + name, 'detail': detail,
                      'page_idx': block['page_idx'], 'raw_type': block['type'], 'effective_type': kind})
    table_label = re.match(r'\s*(?:Table\s+\d|表\s*\d)', caption, re.I)
    table_body = '<table' in content.lower() or re.search(r'\|\s*:?-{3,}', content)
    if (table_label or table_body) and kind != 'table':
        add('type_conflict', 'Table caption/markup conflicts with resource type; inspect the original page')
    code_syntax = (re.search(r'```|(?m:^\s*(?:def |class |for .+ in |import |#include))|:=', content)
                   or re.search(r'\b[A-Z][A-Za-z]+\[\s*[\[{]', content))
    if kind == 'equation' and code_syntax:
        add('mixed_code_math', 'Programming syntax in an equation region; compare all code and results with the original page')
    if kind == 'code' and content.strip() and not re.search(r'(?m)^\s*(?:```|~~~)', content):
        add('unfenced_code', 'Code text has no source fence; inspect completeness/language before representing it')
    if kind in RESOURCES and not content.strip() and not assets:
        add('empty', 'Resource has neither text nor assets; inspect the original page for extraction loss')
    return hints


def prepare(pack, out, start, end=None, overrides=None, notes_dir=None, resource_overrides=None):
    pack, out = Path(pack).resolve(), Path(out).resolve()
    notes_dir = Path(notes_dir).resolve() if notes_dir is not None else None
    if out.exists() and any(out.iterdir()):
        raise ValueError('Output directory must be empty or nonexistent')
    blocks = read_json(pack / 'blocks.json')
    ids = [b['block_id'] for b in blocks]
    if len(set(ids)) != len(ids):
        raise ValueError('Duplicate source block IDs')
    if start not in ids or (end is not None and end not in ids):
        raise ValueError('Unknown range boundary')
    lo, hi = ids.index(start), ids.index(end) if end else len(ids)
    if lo >= hi:
        raise ValueError('Start must precede exclusive end')
    overrides = overrides or {}
    if any(k not in ids or v not in ('heading', 'running_header') for k, v in overrides.items()):
        raise ValueError('Overrides must map known block IDs to heading or running_header')
    resource_overrides = {} if resource_overrides is None else resource_overrides
    selected = {b['block_id']: b for b in blocks[lo:hi]}
    if not isinstance(resource_overrides, dict):
        raise ValueError('Resource overrides must be an object keyed by block ID')
    for bid, item in resource_overrides.items():
        if (bid not in selected or selected[bid]['type'] in TITLES | NOISE
                or not isinstance(item, dict) or not isinstance(item.get('type'), str) or item['type'] not in RESOURCES
                or any(not isinstance(item.get(k), str) or not item[k].strip() for k in ('reason', 'evidence'))):
            raise ValueError('Resource override requires an in-range content ID, valid resource type, reason and evidence')
    asset_map = defaultdict(list)
    for a in read_json(pack / 'assets.json'):
        asset_map[a['block_id']].append(a)

    # Only corroborated running headers are automatic; uncertain repeats remain visible.
    header_texts = {normalized(b.get('content', '')) for b in blocks if b['type'] == 'header'}
    stack, seen, states, corrections, issues = [], set(), {}, [], []
    for b in blocks[:hi]:
        bid, kind, content = b['block_id'], b['type'], b.get('content', '')
        disposition = 'noise' if kind in NOISE else 'content'
        if kind in TITLES:
            key = normalized(content)
            bbox = b.get('bbox') or []
            top = len(bbox) == 4 and bbox[1] < 0.10
            ancestor = any(normalized(n['title']) == key for n in stack)
            repeated = key in seen
            automatic = repeated and ancestor and top and key in header_texts
            running = overrides.get(bid) == 'running_header' or (automatic and bid not in overrides)
            if running:
                disposition = 'running_header'
                corrections.append({'block_id': bid, 'action': disposition,
                                    'basis': 'override' if bid in overrides else 'active ancestor + page top + known header'})
            else:
                level, basis = heading_level(b)
                if repeated and bid not in overrides:
                    issues.append({'block_id': bid, 'kind': 'ambiguous_title',
                                   'detail': 'Repeated title retained; inspect and use overrides if needed'})
                while stack and stack[-1]['level'] >= level:
                    stack.pop()
                stack.append({'block_id': bid, 'title': content, 'level': level, 'basis': basis})
                seen.add(key)
                disposition = 'heading'
        states[bid] = (list(stack), disposition)

    sections, resources, expected, excluded = [], [], [], []
    current = None
    chosen = blocks[lo:hi]
    for b in chosen:
        bid = b['block_id']
        ancestry, disposition = states[bid]
        if disposition in ('noise', 'running_header'):
            excluded.append({'block_id': bid, 'reason': disposition})
            continue
        owner = ancestry[-1] if ancestry else None
        owner_id = owner['block_id'] if owner else None
        if current is None or current['section_id'] != owner_id:
            current = {'section_id': owner_id, 'title': owner['title'] if owner else '未分节正文',
                       'level': owner['level'] if owner else 1,
                       'parent_id': ancestry[-2]['block_id'] if len(ancestry) > 1 else None,
                       'ancestry': ancestry, 'block_ids': [], 'pages': [], 'blocks': [],
                       'file': f'sections/{len(sections) + 1:03d}.md'}
            sections.append(current)
        current['blocks'].append(b)
        current['block_ids'].append(bid)
        if b['page_idx'] not in current['pages']:
            current['pages'].append(b['page_idx'])
        effective = resource_overrides.get(bid, {}).get('type', b['type'])
        expected.append({'block_id': bid, 'type': effective, 'raw_type': b['type'], 'section_file': current['file']})
        if bid in resource_overrides:
            correction = resource_overrides[bid]
            corrections.append({'block_id': bid, 'action': 'resource_type_override',
                                'from': b['type'], 'to': effective,
                                'reason': correction['reason'], 'evidence': correction['evidence']})
        issues.extend(resource_hints(b, effective, asset_map[bid]))
        if b['type'] == 'page_footnote':
            issues.append({'block_id': bid, 'kind': 'page_footnote',
                           'detail': 'Preserved at source position; page-level attribution, not inferred semantic owner'})

    # Only flag adjacent retained code regions crossing pages, not every code example.
    retained = [b for b in chosen if states[b['block_id']][1] not in ('noise', 'running_header')]
    for left, right in zip(retained, retained[1:]):
        left_kind = resource_overrides.get(left['block_id'], {}).get('type', left['type'])
        right_kind = resource_overrides.get(right['block_id'], {}).get('type', right['type'])
        if left_kind == right_kind == 'code' and right['page_idx'] == left['page_idx'] + 1:
            issues.append({'block_id': left['block_id'], 'related_block_id': right['block_id'],
                           'kind': 'resource_cross_page_code',
                           'detail': 'Adjacent code crosses pages; verify continuation and keep both source markers'})

    rendered = {}
    for section in sections:
        source_blocks = section.pop('blocks')
        destination = out / section['file']
        lines = []
        for i, b in enumerate(source_blocks):
            bid = b['block_id']
            kind = resource_overrides.get(bid, {}).get('type', b['type'])
            content = b.get('content', '')
            mapped = {}
            note_images = []
            for a in asset_map[bid]:
                path = a['path']
                asset = None if external(path) else local_asset(pack, path)
                target = path if asset is None else relative_link(asset, destination.parent)
                mapped[a['original_path']] = target
                if notes_dir is not None:
                    note_path = path if asset is None else relative_link(asset, notes_dir)
                    note_images.append({'source_path': path, 'path': note_path,
                                        'markdown': f'![{kind}](<{note_path}>)'})
            def rewrite(text):
                for old in sorted(mapped, key=len, reverse=True):
                    text = text.replace(old, mapped[old])
                return text
            lines.extend([f'<!-- page_idx={b["page_idx"]} -->', f'<!-- block_id={bid} -->'])
            if kind in TITLES:
                lines.append('#' * min(section['level'], 6) + ' ' + rewrite(content))
            elif kind == 'equation' and content:
                lines.append('$$\n' + rewrite(content) + '\n$$')
            else:
                lines.append(rewrite(content))
            if b.get('image_source'):
                if b['image_source'] not in mapped:
                    raise ValueError(f'Asset mapping missing: {bid}')
                lines.append(f'![{kind}](<{mapped[b["image_source"]]}>)')
            for field in ('captions', 'footnotes'):
                lines.extend(rewrite(x.get('content', '')) for x in b.get(field, []))
            if kind in RESOURCES or asset_map[bid]:
                caption = ' '.join(x.get('content', '') for x in b.get('captions', []))
                tag = re.search(r'\\tag\{([^}]+)\}', content)
                def context(candidates):
                    return next(({'block_id': x['block_id'], 'content': x.get('content', '')}
                                 for x in candidates if x['type'] == 'text'), None)
                resource = {'block_id': bid, 'type': kind, 'raw_type': b['type'], 'section_id': section['section_id'],
                                  'section_title': section['title'], 'source_file': section['file'],
                                  'display_label': caption or (f'式 ({tag.group(1)})' if tag else f'{kind} / p{b["page_idx"] + 1}'),
                                  'context_before': context(reversed(source_blocks[:i])),
                                  'context_after': context(source_blocks[i + 1:]),
                                  'content': content, 'assets': asset_map[bid]}
                if notes_dir is not None:
                    resource['note_images'] = note_images
                resources.append(resource)
        rendered[destination] = '\n\n'.join(lines) + '\n'
    selected_ids = set(ids[lo:hi])
    result = {'format': 'mineru4-section-pack/1', 'source_id': blocks[lo]['source_id'],
              'source_pack': os.path.relpath(pack, out).replace('\\', '/'),
              'start': start, 'end_exclusive': end, 'sections': sections, 'expected': expected,
              'excluded': excluded, 'corrections': [x for x in corrections if x['block_id'] in selected_ids],
              'issues': [x for x in issues if x['block_id'] in selected_ids]}
    if notes_dir is not None:
        result['notes_directory'] = os.path.relpath(notes_dir, out).replace('\\', '/')
    out.mkdir(parents=True, exist_ok=True)
    for path, text in rendered.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    (out / 'sections.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    (out / 'resources.json').write_text(json.dumps(resources, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pack', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--start', required=True, help='Full source block ID, inclusive')
    parser.add_argument('--end', help='Full source block ID, exclusive; omit for document end')
    parser.add_argument('--overrides', help='JSON mapping block IDs to heading or running_header')
    parser.add_argument('--resource-overrides', help='JSON mapping content IDs to reviewed type/reason/evidence')
    parser.add_argument('--notes-dir', help='Final Markdown directory; add ready-to-copy image references to resources.json')
    args = parser.parse_args()
    result = prepare(args.pack, args.out, args.start, args.end,
                     read_json(args.overrides) if args.overrides else None, args.notes_dir,
                     read_json(args.resource_overrides) if args.resource_overrides else None)
    print(json.dumps({'sections': len(result['sections']), 'blocks': len(result['expected']),
                      'corrections': result['corrections'], 'issues': result['issues']}, ensure_ascii=True))
