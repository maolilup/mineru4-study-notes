"""Join a MinerU 4 study pack to rendered slides by original page_idx."""
import argparse
import html
import json
import os
from pathlib import Path
from urllib.parse import quote


def embed(pack, screenshots, out):
    pack, screenshots, out = Path(pack).resolve(), Path(screenshots).resolve(), Path(out).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError('Output must be empty')
    manifest = json.loads((pack / 'manifest.json').read_text(encoding='utf-8'))
    blocks = json.loads((pack / 'blocks.json').read_text(encoding='utf-8'))
    slides = json.loads((screenshots / 'slides.json').read_text(encoding='utf-8'))
    assets = json.loads((pack / 'assets.json').read_text(encoding='utf-8'))
    rows = {row['page_idx']: row for row in slides['slides']}
    if len(rows) != len(slides['slides']):
        raise ValueError('Duplicate screenshot page indices')
    missing = set(manifest['page_indices']) - rows.keys()
    if missing:
        raise ValueError(f'No screenshot for pages: {sorted(missing)}')
    markdown, sections, mappings = [], [], []
    for page_idx in manifest['page_indices']:
        row = rows[page_idx]
        image = (screenshots / row['path']).resolve()
        if not image.is_relative_to(screenshots) or not image.is_file() or not image.stat().st_size:
            raise ValueError('Invalid screenshot path')
        relative = quote(os.path.relpath(image, out).replace('\\', '/'), safe='/')
        page_blocks = [b for b in blocks if b['page_idx'] == page_idx]
        # Literal text for a source preview; raw HTML from source is escaped in HTML.
        texts = []
        for block in page_blocks:
            if block['type'] in ('header', 'footer', 'page_number', 'index'):
                continue
            content = block.get('content', '')
            for asset in assets:
                if asset['block_id'] != block['block_id'] or not asset['path'].startswith('assets/'):
                    continue
                local = (pack / asset['path']).resolve()
                if not local.is_relative_to(pack) or not local.is_file():
                    raise ValueError('Invalid source asset path')
                link = quote(os.path.relpath(local, out).replace('\\', '/'), safe='/')
                content = content.replace(asset['original_path'], link)
            texts.append(content)
        text = '\n\n'.join(texts)
        ids = [b['block_id'] for b in page_blocks]
        markdown.append(f'## 第 {page_idx + 1} 页\n\n<!-- source_id={manifest["source_id"]}; page_idx={page_idx} -->\n\n{text}\n\n![第 {page_idx + 1} 页截图](<{relative}>)')
        sections.append(f'<section><h2>第 {page_idx + 1} 页</h2><pre>{html.escape(text)}</pre><img src="{html.escape(relative, quote=True)}" alt="第 {page_idx + 1} 页截图"></section>')
        mappings.append(dict(page_idx=page_idx, block_ids=ids, screenshot=relative))
    out.mkdir(parents=True, exist_ok=True)
    (out / 'notes.md').write_text('\n\n'.join(markdown), encoding='utf-8')
    (out / 'notes.html').write_text('<!doctype html><html lang="zh"><meta charset="utf-8"><title>资料与幻灯片截图</title><style>body{max-width:1100px;margin:30px auto;font:16px sans-serif}pre{white-space:pre-wrap}img{width:100%}section{margin-bottom:60px}</style>' + ''.join(sections) + '</html>', encoding='utf-8')
    (out / 'mapping.json').write_text(json.dumps(dict(source_id=manifest['source_id'], screenshot_source_sha256=slides['source_sha256'], pages=mappings), ensure_ascii=False, indent=2), encoding='utf-8')
    return len(mappings)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pack', required=True)
    parser.add_argument('--screenshots', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    print('Embedded pages:', embed(args.pack, args.screenshots, args.out))
