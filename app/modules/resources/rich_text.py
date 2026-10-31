"""Parse a tiny style allowlist into data, never render imported HTML."""
import re
from html.parser import HTMLParser


class RichText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.runs=[]; self.stack=[{}]; self.blocked=0

    def handle_starttag(self, tag, attrs):
        if tag in {'script','style','iframe','object','svg','math'}:self.blocked+=1
        if self.blocked:return
        style=dict(self.stack[-1])
        if tag in {'p','div','li','br'} and self.runs:self.runs.append({'text':'\n'})
        if tag in {'b','strong'}:style['bold']=True
        if tag in {'i','em'}:style['italic']=True
        css=dict(re.findall(r'([\w-]+)\s*:\s*([^;]+)',dict(attrs).get('style','') or ''))
        if css.get('text-align') in {'left','center','right'}:style['align']=css['text-align']
        if re.fullmatch(r'#[0-9a-fA-F]{3,8}',css.get('color','')):style['color']=css['color']
        size=re.fullmatch(r'(\d+(?:\.\d+)?)px',css.get('font-size',''))
        if size:style['size']=min(72,max(12,float(size[1])))
        if css.get('font-weight') in {'bold','600','700','800','900'}:style['bold']=True
        if tag not in {'br','img','hr','input'}:self.stack.append(style)

    def handle_endtag(self,tag):
        if tag in {'script','style','iframe','object','svg','math'}:
            self.blocked=max(0,self.blocked-1);return
        if not self.blocked and len(self.stack)>1:self.stack.pop()

    def handle_data(self,text):
        if not self.blocked and len(self.runs)<500:self.runs.append({'text':text[:8000],**self.stack[-1]})


def runs(value):
    parser=RichText();parser.feed(str(value)[:40000]);return parser.runs


def layout_issues(scene):
    issues=[]
    for e in scene['elements']:
        if e['left']<0 or e['top']<0 or e['left']+e['width']>scene['width']+2 or e['top']+e['height']>scene['height']+2:
            issues.append({'element_id':e['id'],'code':'outside_canvas'})
        if '\ufffd' in e.get('text',''):issues.append({'element_id':e['id'],'code':'broken_text'})
        if e['kind']=='text':
            size=e.get('font_size',20)
            estimate=sum(max(1,len(line)*size/e['width']) for line in e.get('text','').splitlines())*size*1.35
            if estimate>e['height']*1.15:issues.append({'element_id':e['id'],'code':'possible_text_overflow'})
    return issues
