"""Extract a source-linked study pack from MinerU 4; never emulate old JSONs."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from urllib.parse import quote, unquote, urlsplit
import zipfile


def safe_relative(value):
    value = unquote(value)
    path = PurePosixPath(value)
    if not value or '\\' in value or ':' in value or path.is_absolute() or '..' in path.parts:
        raise ValueError(f'Unsafe local path: {value}')
    return path


def is_document(data):
    return isinstance(data, dict) and isinstance(data.get('pages'), list)


def load_source(src, document=None):
    """Return raw document, stable identity, and a bounded asset reader."""
    src = Path(src).resolve()
    archive = zipfile.ZipFile(src) if src.suffix.lower() == '.zip' else None
    if archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            archive.close()
            raise ValueError('Duplicate ZIP members are ambiguous')
        candidates = [n for n in names if n.endswith('.json')]
        read = archive.read
    else:
        root = src if src.is_dir() else src.parent
        candidates = [str(p.relative_to(root)).replace('\\', '/') for p in root.rglob('*.json')] if src.is_dir() else [src.name]
        def read(name):
            path = (root / str(safe_relative(name))).resolve()
            if not path.is_relative_to(root):
                raise ValueError('Asset escapes input root')
            return path.read_bytes()
    try:
        if document is not None:
            safe_relative(document)
            candidates = [document] if document in candidates else []
        found = []
        for name in candidates:
            try:
                raw = read(name)
                data = json.loads(raw)
            except (ValueError, UnicodeError):
                continue
            if is_document(data) and data.get('schema') != 'docvortex.model':
                found.append((name, raw, data))
        # Saved packs contain two representations of the same document.
        if document is None:
            structured_parents = {str(PurePosixPath(n).parent) for n, _, d in found
                                  if PurePosixPath(n).name == 'structured_content.json' and 'schema' not in d}
            found = [(n, raw, d) for n, raw, d in found
                     if not (PurePosixPath(n).name == 'middle_json.json'
                             and str(PurePosixPath(n).parent) in structured_parents)]
        if len(found) != 1:
            raise ValueError('Expected one MinerU 4 document; select --document for multiple inputs')
        name, raw, data = found[0]
        parent = PurePosixPath(name).parent
        asset_bytes = {}
        def get_asset(path):
            relative = safe_relative(path)
            key = str(parent / relative)
            if key not in asset_bytes:
                asset_bytes[key] = read(key)
            return asset_bytes[key]
        # Read all assets while the ZIP is open; normalization discovers references.
        normalized = normalize(data)
        for page in normalized['pages']:
            for block in page['blocks']:
                for path in asset_paths(block):
                    if not external(path):
                        get_asset(path)
        return normalized, hashlib.sha256(raw).hexdigest()[:16], name, asset_bytes, parent
    finally:
        if archive:
            archive.close()


def normalize(data):
    if data.get('schema') == 'docvortex.middle':
        try:
            from mineru.parser import ParseResult
        except ImportError as exc:
            raise ValueError('MiddleJson requires MinerU 4 Python; alternatively use Structured Content ZIP') from exc
        data = ParseResult.from_dict(data).structured_content()
    elif 'schema' in data:
        raise ValueError('Unsupported schema: ' + str(data['schema']))
    if not is_document(data) or not data['pages']:
        raise ValueError('Expected nonempty pages/blocks document')
    previous = -1
    for page in data['pages']:
        idx = page.get('page_idx')
        if type(idx) is not int or idx <= previous:
            raise ValueError('page_idx must be increasing, unique, nonnegative integers')
        previous = idx
        if not isinstance(page.get('blocks'), list):
            raise ValueError('Page must contain blocks array')
        for block in page['blocks']:
            if not isinstance(block, dict) or not isinstance(block.get('type'), str):
                raise ValueError('Each block requires a type')
            if not isinstance(block.get('content', ''), str):
                raise ValueError('Expected string content; use typed MiddleJson SDK conversion')
    return data


def external(path):
    return urlsplit(path).scheme in ('http', 'https', 'data') or path.startswith('//')


def asset_paths(block):
    paths = []
    if block.get('image_source'):
        if not isinstance(block['image_source'], str):
            raise ValueError('Unsupported image_source representation')
        paths.append(block['image_source'])
    texts = [block.get('content', '')]
    for key in ('captions', 'footnotes'):
        texts.extend(c.get('content', '') for c in block.get(key, []))
    for text in texts:
        paths.extend(re.findall(r'<img\b[^>]*\bsrc=[\"\']([^\"\']+)', text, re.I))
        markdown_images = re.finditer(
            r'''!\[[^\]]*\]\(\s*(?:<([^>]+)>|([^\s)]+))(?:\s+(?:"[^"]*"|'[^']*'|\([^)]*\)))?\s*\)''', text)
        paths.extend(match.group(1) or match.group(2) for match in markdown_images)
    return list(dict.fromkeys(paths))


def heading_level(block):
    text = block.get('content', '').strip().strip('*')
    if re.match(r'^(?:Chapter\s+\d+\b|第[一二三四五六七八九十百\d]+章)', text, re.I):
        return 1, 'chapter-number'
    number = re.match(r'^(\d+(?:\.\d+)*)(?:\.)?\s+\S', text)
    if number:
        return len(number.group(1).split('.')), 'section-number'
    level = block.get('level', 2)
    return max(1, min(level, 12)) if type(level) is int else 2, 'unverified-source-level'


def extract(src, out, document=None):
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise ValueError('Output directory is not empty; choose a new directory')
    data, source_id, name, asset_bytes, parent = load_source(src, document)
    records, headings, assets, tables, codes, equations, lines = [], [], [], [], [], [], []
    stack = []
    warnings = []
    pending_assets = {}
    for page in data['pages']:
        idx = page['page_idx']
        lines.append(f'<!-- page_idx={idx}; physical_page={idx + 1} -->')
        for ordinal, block in enumerate(page['blocks']):
            bid = f'{source_id}:p{idx}:b{ordinal}'
            kind, content = block['type'], block.get('content', '')
            if kind in ('doc_title', 'paragraph_title', 'title'):
                level, basis = heading_level(block)
                while stack and stack[-1]['level'] >= level:
                    stack.pop()
                node = dict(block_id=bid, text=content, level=level, basis=basis,
                            page_start=idx, page_end=idx, children=[])
                (stack[-1]['children'] if stack else headings).append(node)
                stack.append(node)
                if basis == 'unverified-source-level':
                    warnings.append(f'Check heading hierarchy: {bid}')
            if kind not in ('header', 'footer', 'page_number', 'index'):
                for node in stack:
                    node['page_end'] = idx
            record = dict(block, source_id=source_id, block_id=bid, page_idx=idx,
                          block_index=ordinal, section_id=stack[-1]['block_id'] if stack else None)
            records.append(record)
            if kind == 'table':
                tables.append(record)
            if kind == 'code':
                codes.append(record)
            if kind == 'equation':
                equations.append(record)
            rewritten = content
            mapping = {}
            for path in asset_paths(block):
                target = path
                if external(path):
                    warnings.append(f'External asset retained, not downloaded: {bid} {path}')
                else:
                    key = str(parent / safe_relative(path))
                    payload = asset_bytes[key]
                    extension = PurePosixPath(path).suffix.lower()
                    if not re.fullmatch(r'\.[a-z0-9]{1,10}', extension):
                        extension = '.bin'
                    target = 'assets/' + hashlib.sha256(key.encode()).hexdigest()[:20] + extension
                    pending_assets[target] = payload
                mapping[path] = target
                assets.append(dict(block_id=bid, section_id=record['section_id'], page_idx=idx,
                                   type=kind, original_path=path, path=target,
                                   captions=block.get('captions', [])))
                rewritten = rewritten.replace(path, quote(target, safe='/:') if not external(target) else target)
            if kind in ('header', 'footer', 'page_number', 'index'):
                continue
            lines.append(f'<!-- block_id={bid} -->')
            if kind in ('doc_title', 'paragraph_title', 'title'):
                lines.append('#' * min(stack[-1]['level'] + 1, 6) + ' ' + rewritten)
            elif kind == 'equation' and rewritten:
                lines.append('$$\n' + rewritten + '\n$$')
            elif rewritten:
                lines.append(rewritten)
            if block.get('image_source'):
                target = mapping[block['image_source']]
                lines.append(f'![{kind}](<{quote(target, safe="/:") if not external(target) else target}>)')
            for field in ('captions', 'footnotes'):
                for item in block.get(field, []):
                    text = item.get('content', '')
                    for old, new in mapping.items():
                        text = text.replace(old, quote(new, safe='/:') if not external(new) else new)
                    lines.append(text)
    manifest = dict(format='mineru4-study-pack/1', source_id=source_id, source_member=name,
                    metadata=data.get('metadata', {}), is_full_document=data.get('is_full_document'),
                    page_indices=[p['page_idx'] for p in data['pages']], pages=len(data['pages']),
                    blocks=len(records), types=dict(Counter(b['type'] for b in records)),
                    asset_references=len(assets), local_assets=len(pending_assets), warnings=warnings)
    if data.get('is_full_document') is not True:
        warnings.append('Input is partial or completeness is unknown')
    out.mkdir(parents=True, exist_ok=True)
    for filename, obj in [('manifest', manifest), ('document', data), ('blocks', records),
                          ('headings', headings), ('assets', assets), ('tables', tables),
                          ('code', codes), ('equations', equations)]:
        (out / (filename + '.json')).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
    for path, payload in pending_assets.items():
        target = out / path
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(payload)
    (out / 'reading.md').write_text('\n\n'.join(lines), encoding='utf-8')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--src', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--document')
    args = parser.parse_args()
    try:
        result = extract(args.src, args.out, args.document)
    except (ValueError, OSError, KeyError, zipfile.BadZipFile) as exc:
        parser.exit(2, f'ERROR: {exc}\n')
    print(json.dumps({k: result[k] for k in ('source_id', 'pages', 'blocks', 'types', 'local_assets')}, ensure_ascii=True))


if __name__ == '__main__':
    main()
