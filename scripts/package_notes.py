"""Copy referenced images beside notes and optionally create a checked portable ZIP."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
from urllib.parse import quote, unquote, urlsplit
import zipfile

from check_note_coverage import LINK, digest, mask_code, target

HTML_URL = re.compile(r'<(?P<tag>img|a)\b[^>]*?\b(?P<attr>src|href)=["\'](?P<url>[^"\']+)["\']', re.I)
SOURCE_METADATA = re.compile(
    r'(?m)^[ \t]*<!--[ \t]*(?:block_id=[^\s>]+|page_idx=\d+)[ \t]*-->[ \t]*(?:\r?\n|$)'
)


def strip_source_metadata(text):
    """Remove standalone audit markers while preserving source code examples."""
    masked = mask_code(text)
    matches = [m for m in SOURCE_METADATA.finditer(text)
               if masked[m.start():m.end()].strip().startswith('<!--')]
    for match in reversed(matches):
        text = text[:match.start()] + text[match.end():]
    return text, len(matches)


def references(text):
    """Return URL spans so surrounding Markdown/HTML and captions stay untouched."""
    masked = mask_code(text)
    if re.search(r'(?m)^\s*\[[^\]\n]+\]:\s*\S', masked):
        raise ValueError('Reference-style links require conversion to inline links before packaging')
    if re.search(r'!\[[^\]]*\](?!\()', masked):
        raise ValueError('Reference-style images require conversion to inline images before packaging')
    for match in LINK.finditer(masked):
        group = 2 if match[2] is not None else 3
        yield match.start(group), match.end(group), bool(match[1]), match[group]
    for match in HTML_URL.finditer(masked):
        tag, attr = match['tag'].lower(), match['attr'].lower()
        if (tag, attr) in {('img', 'src'), ('a', 'href')}:
            yield match.start('url'), match.end('url'), tag == 'img', match['url']


def with_suffix(url, replacement):
    parsed = urlsplit(url)
    return replacement + ('?' + parsed.query if parsed.query else '') + ('#' + parsed.fragment if parsed.fragment else '')


def source_identities(sections, hashes):
    if sections is None:
        return None, {}, []
    root = Path(sections).resolve()
    index = json.loads((root / 'sections.json').read_text(encoding='utf-8'))
    source = (root / index['source_pack']).resolve()
    identity, unavailable = {}, []
    resources = json.loads((root / 'resources.json').read_text(encoding='utf-8'))
    for resource in resources:
        for asset in resource['assets']:
            if urlsplit(asset['path']).scheme or asset['path'].startswith('//'):
                continue
            path = (source / asset['path']).resolve()
            if not path.is_relative_to(source):
                raise ValueError(f'Unsafe section asset: {asset["path"]}')
            if not path.is_file():
                unavailable.append({'block_id': resource['block_id'], 'path': str(path)})
                continue
            record = {'block_id': resource['block_id'], 'path': str(path)}
            group = identity.setdefault(digest(path, hashes), [])
            if record not in group:
                group.append(record)
    return index['source_id'], identity, unavailable


def verify_zip(path):
    """Resolve supported local links inside the archive, without extracting files."""
    checked = 0
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise ValueError('ZIP CRC verification failed')
        names = set(archive.namelist())
        for name in sorted(names):
            if not name.lower().endswith('.md'):
                continue
            text = archive.read(name).decode('utf-8')
            for _, _, _, url in references(text):
                parsed = urlsplit(url)
                if parsed.scheme or url.startswith('//') or not parsed.path:
                    continue
                relative = unquote(parsed.path)
                if relative.startswith('/') or '\\' in relative:
                    raise ValueError(f'Nonportable ZIP link in {name}: {url}')
                pieces = list(PurePosixPath(name).parent.parts)
                for part in PurePosixPath(relative).parts:
                    if part == '..':
                        if not pieces:
                            raise ValueError(f'ZIP link escapes package in {name}: {url}')
                        pieces.pop()
                    elif part != '.':
                        pieces.append(part)
                resolved = '/'.join(pieces)
                if resolved not in names:
                    raise ValueError(f'Broken ZIP link in {name}: {url}')
                checked += 1
    return checked


def package(notes, out, manifest, archive=None, sections=None, reader_copy=False):
    files = list(dict.fromkeys(Path(n).resolve() for n in notes))
    out, manifest = Path(out).resolve(), Path(manifest).resolve()
    archive = Path(archive).resolve() if archive else None
    if not files or any(not p.is_file() or p.suffix.lower() != '.md' for p in files):
        raise ValueError('Provide existing Markdown note files in reading order')
    if len({p.name.casefold() for p in files}) != len(files):
        raise ValueError('Note filenames must be unique for a flat portable directory')
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ValueError('Output directory must be empty or nonexistent')
    if manifest.exists() or manifest.is_relative_to(out):
        raise ValueError('Manifest must be a new file outside the delivered notes directory')
    if archive and (archive.exists() or archive.is_relative_to(out) or archive == manifest or archive in files):
        raise ValueError('ZIP must be a new file outside the delivered notes directory')
    if sections:
        section_root = Path(sections).resolve()
        index = json.loads((section_root / 'sections.json').read_text(encoding='utf-8'))
        source_root = (section_root / index['source_pack']).resolve()
        if out.is_relative_to(source_root) or manifest.is_relative_to(source_root) or (archive and archive.is_relative_to(source_root)):
            raise ValueError('Packaging outputs must stay outside the immutable source pack')
    hashes = {}
    source_id, identities, unavailable = source_identities(sections, hashes)
    destinations = {p: out / p.name for p in files}
    images, rendered, external_images, note_records = {}, {}, [], []
    for path in files:
        original_bytes = path.read_bytes()
        original = original_bytes.decode('utf-8')
        edits = []
        for start, stop, is_image, url in references(original):
            if re.match(r'^[A-Za-z]:[\\/]|^file:', url, re.I) or '\\' in url:
                raise ValueError(f'Convert absolute Windows/file URLs or backslashes before packaging: {url}')
            resolved = target(path, url)
            if resolved is None:
                if is_image and urlsplit(url).path:
                    external_images.append({'file': path.name, 'url': url})
                continue
            if not resolved.is_file():
                raise ValueError(f'Missing local link in {path.name}: {url}')
            if resolved in destinations:
                replacement = quote(destinations[resolved].name, safe='')
            elif is_image or resolved.suffix.lower() in {'.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.bmp', '.tif', '.tiff', '.avif'}:
                sha = digest(resolved, hashes)
                if sha not in images:
                    suffix = resolved.suffix.lower()
                    if not re.fullmatch(r'\.[a-z0-9]{1,10}', suffix):
                        raise ValueError(f'Image needs a usable filename extension: {resolved}')
                    images[sha] = {'path': f'images/{len(images) + 1:03d}{suffix}', 'sha256': sha,
                                   'bytes': resolved.stat().st_size, 'referenced_from': [],
                                   'original_paths': [], 'source_assets': identities.get(sha, [])}
                image = images[sha]
                origin = str(resolved)
                if origin not in image['original_paths']:
                    image['original_paths'].append(origin)
                image['referenced_from'].append({'file': path.name, 'original_url': url})
                replacement = image['path']
            else:
                raise ValueError(f'Local attachment is outside the note/image package: {url}; handle it explicitly')
            edits.append((start, stop, with_suffix(url, replacement)))
        result = original
        ordered_edits = sorted(edits)
        if any(left[1] > right[0] for left, right in zip(ordered_edits, ordered_edits[1:])):
            raise ValueError(f'Overlapping link syntax needs manual conversion: {path}')
        for start, stop, replacement in sorted(edits, reverse=True):
            result = result[:start] + replacement + result[stop:]
        removed = 0
        if reader_copy:
            result, removed = strip_source_metadata(result)
        rendered[path.name] = result
        note_records.append({'input': str(path), 'output': path.name,
                             'input_sha256': hashlib.sha256(original_bytes).hexdigest(),
                             'output_sha256': hashlib.sha256(result.encode('utf-8')).hexdigest(),
                             'source_metadata_removed': removed})
    # All link planning succeeds before creating files; inputs and source assets are untouched.
    for record in note_records:
        if digest(Path(record['input']), {}) != record['input_sha256']:
            raise ValueError(f'Note changed during packaging: {record["input"]}')
    out.mkdir(parents=True, exist_ok=True)
    for record in images.values():
        destination = out / record['path']
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(record['original_paths'][0], destination)
        if digest(destination, {}) != record['sha256']:
            raise ValueError(f'Image copy verification failed: {destination}')
    for name, text in rendered.items():
        destination = out / name
        destination.write_bytes(text.encode('utf-8'))
        for _, _, _, url in references(text):
            resolved = target(destination, url)
            if resolved is not None and (not resolved.is_relative_to(out) or not resolved.is_file()):
                raise ValueError(f'Nonportable output link: {name}: {url}')
    result = {'format': 'mineru4-portable-notes/1', 'source_id': source_id, 'output_directory': str(out),
              'notes': note_records, 'images': list(images.values()), 'external_images': external_images,
              'self_contained_images': not external_images, 'unavailable_source_assets': unavailable,
              'local_links_checked': True, 'reader_copy': reader_copy,
              'limits': 'Copied bytes and supported links only; no translation, visual or teaching certification.'}
    if archive:
        archive.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as output:
            for name in rendered:
                output.write(out / name, name)
            for image in images.values():
                output.write(out / image['path'], image['path'])
        result['zip'] = {'path': str(archive), 'sha256': digest(archive, {}),
                         'bytes': archive.stat().st_size, 'checked_local_links': verify_zip(archive)}
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with manifest.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--notes', nargs='+', required=True, help='Explicit Markdown note paths in reading order')
    parser.add_argument('--out', required=True, help='New portable directory')
    parser.add_argument('--manifest', required=True, help='New provenance JSON outside the notes directory')
    parser.add_argument('--zip', dest='archive', help='Optional new ZIP outside the notes directory')
    parser.add_argument('--sections', help='Optional section pack for source block/image identity')
    parser.add_argument('--reader-copy', action='store_true',
                        help='Remove standalone block_id/page_idx comments from delivered notes; keep input audit notes')
    args = parser.parse_args()
    result = package(args.notes, args.out, args.manifest, args.archive, args.sections, args.reader_copy)
    print(json.dumps({'notes': len(result['notes']), 'images': len(result['images']),
                      'self_contained_images': result['self_contained_images'],
                      'zip': result.get('zip')}, ensure_ascii=False, indent=2))
