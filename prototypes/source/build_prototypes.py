from pathlib import Path
import re

root = Path(__file__).resolve().parent.parent
student = root / '桂子问津_前端演示.html'
base = student.read_text(encoding='utf-8').split('<!-- WENJIN EXTENSION START -->')[0]
base = re.sub(r"const APP_ROLE='(?:student|admin)';\s*", '', base)
base = base.replace("const state={role:'student'", "const state={role:APP_ROLE")
base = re.sub(r'<div class="mode" aria-label="演示端切换">.*?</div>', '<div class="role-mark">学生学习空间 · 独立演示入口</div>', base)
base = base.replace("$('#studentMode').classList.toggle('on',state.role==='student');$('#adminMode').classList.toggle('on',state.role==='admin');", '')
base = base.replace("原型角色切换仅用于演示。正式产品需服务端权限控制。", "这是独立管理端演示。正式产品需独立账户与服务端权限控制。")
base = re.sub(r'\nrender\(\);\s*\n</script>\s*(?:</body></html>)?\s*$', '\n</script>\n', base)
base = re.sub(r'</body></html>\s*$', '', base)
if 'rel="icon"' not in base:
    base=base.replace('<head>', '<head>\n<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22/%3E">',1)
css = (root / 'source/workspaces.css').read_text(encoding='utf-8')
js = (root / 'source/workspaces.js').read_text(encoding='utf-8')
for role, filename in [('student', '桂子问津_前端演示.html'), ('admin', '桂子问津_管理端演示.html')]:
    html = base.replace("const $=", f"const APP_ROLE='{role}';\nconst $=", 1)
    if role == 'admin':
        html = html.replace('学生学习空间 · 独立演示入口', '内容与试点治理 · 独立管理入口')
    html += '<!-- WENJIN EXTENSION START -->\n<style>' + css + '</style>\n<script>' + js + '</script>\n</body></html>\n'
    (root / filename).write_text(html, encoding='utf-8')
    print(filename)
