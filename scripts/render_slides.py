"""Render a local PowerPoint deck to PNG and a source-linked embedding preview."""
import argparse
import hashlib
import html
import json
from pathlib import Path
import struct
import time


def png_size(path):
    header = path.read_bytes()[:24]
    if len(header) != 24 or header[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError(f'Invalid PNG: {path}')
    return struct.unpack('>II', header[16:24])


def run(source, output, dpi=120):
    import pythoncom
    import win32com.client
    source, output = Path(source).resolve(), Path(output).resolve()
    if not 36 <= dpi <= 600:
        raise ValueError('DPI must be between 36 and 600')
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use an empty output directory; screenshots are not reused')
    fingerprint = hashlib.sha256(source.read_bytes()).hexdigest()
    pythoncom.CoInitialize()
    app = presentation = None
    try:
        # Isolated automation instance; never close the user\'s existing presentation.
        app = win32com.client.DispatchEx('PowerPoint.Application')
        presentation = app.Presentations.Open(str(source), ReadOnly=True, WithWindow=False)
        total = presentation.Slides.Count
        if total < 1:
            raise ValueError('Presentation contains no slides')
        width = int(presentation.PageSetup.SlideWidth * dpi / 72)
        height = int(presentation.PageSetup.SlideHeight * dpi / 72)
        (output / 'slides').mkdir(parents=True, exist_ok=True)
        rows = []
        for index in range(1, total + 1):
            slide = presentation.Slides(index)
            path = output / 'slides' / f'slide_{index:03d}.png'
            for attempt in range(2):
                try:
                    slide.Export(str(path), 'PNG', width, height)
                    actual = png_size(path)
                    if actual != (width, height):
                        raise ValueError(f'Unexpected PNG dimensions: {actual}')
                    break
                except Exception:
                    if attempt:
                        raise
                    pythoncom.PumpWaitingMessages()
                    time.sleep(0.3)
            texts = []
            for shape in slide.Shapes:
                if shape.HasTextFrame and shape.TextFrame.HasText:
                    texts.append(shape.TextFrame.TextRange.Text.replace('\r', '\n'))
            rows.append(dict(page_idx=index - 1, slide_number=index, path=path.relative_to(output).as_posix(),
                             width=width, height=height, text='\n'.join(texts)))
            pythoncom.PumpWaitingMessages()
        manifest = dict(source=str(source), source_sha256=fingerprint, dpi=dpi,
                        renderer='Microsoft PowerPoint COM', count=total, slides=rows)
        (output / 'slides.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        markdown, sections = [], []
        for row in rows:
            number, path, text = row['slide_number'], row['path'], row['text']
            markdown.append(f'## 第 {number} 页\n\n{text}\n\n![第 {number} 页]({path})')
            sections.append(f'<section><h2>第 {number} 页</h2><p>{html.escape(text)}</p><img src="{path}" alt="第 {number} 页"></section>')
        (output / 'embedded-notes.md').write_text('\n\n'.join(markdown), encoding='utf-8')
        (output / 'embedded-notes.html').write_text(
            '<!doctype html><html lang="zh"><meta charset="utf-8"><title>幻灯片截图预览</title>'
            '<style>body{max-width:1100px;margin:30px auto;font:16px sans-serif}section{margin-bottom:60px}p{white-space:pre-wrap}img{max-width:100%;border:1px solid #ddd}</style>'
            + ''.join(sections) + '</html>', encoding='utf-8')
        return manifest
    finally:
        try:
            if presentation is not None:
                presentation.Close()
        finally:
            try:
                if app is not None:
                    app.Quit()
            finally:
                pythoncom.CoUninitialize()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--src', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--dpi', type=int, default=120)
    args = parser.parse_args()
    result = run(args.src, args.out, args.dpi)
    print(json.dumps(dict(count=result['count'], dpi=result['dpi']), ensure_ascii=True))


if __name__ == '__main__':
    main()
