from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / 'source'

def build():
    css = (SOURCE / 'theme.css').read_text(encoding='utf-8')
    js = (SOURCE / 'prototype.js').read_text(encoding='utf-8')
    for theme in ('a', 'b'):
        for role in ('student', 'admin'):
            config = {'theme': theme, 'role': role}
            title = ('A · 问津书院' if theme == 'a' else 'B · 问津学习站') + (' · 学生端' if role == 'student' else ' · 管理端')
            html = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title><style>{css}</style></head>
<body class="theme-{theme} role-{role}"><a class="skip" href="#main">跳到主要内容</a><div id="app"></div><div id="overlay"></div><div id="toasts" role="status" aria-live="polite"></div><script>const CONFIG={json.dumps(config)};\n{js}</script></body></html>'''
            (ROOT / f'{theme}-{role}.html').write_text(html, encoding='utf-8')

if __name__ == '__main__':
    build()
