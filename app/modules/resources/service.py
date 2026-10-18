import html
import io
import json
import zipfile
from html.parser import HTMLParser
from pydantic import Field
from app.modules.learning.contracts import Contract
from app.modules.errors import DomainError


class Card(Contract):
    text: str = Field(min_length=1,max_length=4000)


class ResourceIn(Contract):
    title: str = Field(min_length=2,max_length=120)
    cards: list[Card] = Field(min_length=1,max_length=30)
    generator: str = Field(default='manual_import',max_length=100)


def import_classroom(data):
    """Read an OpenMAIC formatVersion=1 export without extracting files or code.

    Only plain scene text is retained; actions, HTML, audio, external URLs and
    self-reported scores never become executable resources or learning facts.
    """
    if len(data)>5_000_000: raise DomainError(422,'resource_too_large','资源压缩包过大')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries=archive.infolist()
            if len(entries)>500 or sum(i.file_size for i in entries)>20_000_000:
                raise ValueError('archive_bounds')
            for entry in entries:
                path=entry.filename.replace('\\','/')
                if path.startswith('/') or ':' in path or '..' in path.split('/') or entry.flag_bits&1 or (entry.external_attr>>16)&0o170000==0o120000:
                    raise ValueError('unsafe_archive_path')
            manifest=archive.read('manifest.json')
            if len(manifest)>2_000_000: raise ValueError('manifest_bounds')
            value=json.loads(manifest)
        if not isinstance(value,dict): raise ValueError('manifest_shape')
        if value.get('formatVersion')!=1: raise ValueError('unsupported_format')
        scenes=value.get('scenes',[])
        if not isinstance(scenes,list) or not 1<=len(scenes)<=30: raise ValueError('scene_bounds')
        cards=[]
        for scene in scenes:
            if not isinstance(scene,dict): raise ValueError('scene_shape')
            # Export conversion deliberately does not run the original scene.
            content=scene.get('content',{})
            text=scene.get('title','')+'\n'+plain_text(content)
            cards.append(Card(text=text[:4000]))
        return ResourceIn(title=str(value['stage']['name'])[:120],cards=cards,generator='OpenMAIC.v1.2.0.classroom-export').model_dump()
    except (ValueError,KeyError,TypeError,AttributeError,zipfile.BadZipFile,RuntimeError):
        raise DomainError(422,'invalid_resource','无法安全导入此课堂导出，请检查格式、路径及大小') from None


def plain_text(value,depth=0):
    if depth>12: return ''
    if isinstance(value,str): return value[:4000]
    if isinstance(value,list): return '\n'.join(plain_text(v,depth+1) for v in value[:100])[:12000]
    if isinstance(value,dict):
        if isinstance(value.get('canvas'),dict):
            return canvas_text(value['canvas'])
        return '\n'.join(plain_text(v,depth+1) for k,v in value.items() if k in {'title','text','content','elements','slides','description','items','body'})[:12000]
    return ''


def canvas_text(canvas):
    """OpenMAIC v1.2 slide text/LaTeX only; discard actions, media and markup."""
    class Text(HTMLParser):
        def __init__(self):super().__init__(convert_charrefs=True);self.parts=[];self.blocked=0
        def handle_starttag(self,tag,attrs):
            if tag in {'script','style'}:self.blocked+=1
            if not self.blocked and tag in {'p','br','div','li'}:self.parts.append('\n')
        def handle_endtag(self,tag):
            if tag in {'script','style'}:self.blocked=max(0,self.blocked-1)
        def handle_data(self,data):
            if not self.blocked:self.parts.append(data)
    elements=canvas.get('elements',[])
    if not isinstance(elements,list):return ''
    texts=[]
    for element in elements[:100]:
        if not isinstance(element,dict):continue
        if element.get('type')=='text' and isinstance(element.get('content'),str):
            parser=Text();parser.feed(element['content'][:40000]);texts.append(''.join(parser.parts).strip())
        elif element.get('type')=='latex' and isinstance(element.get('latex'),str):
            texts.append('公式（纯文本）：'+element['latex'][:2000])
    return '\n'.join(t for t in texts if t)[:12000]


def render(body):
    # No untrusted markup or JS. Even imported HTML-looking text is escaped.
    cards=''.join('<section><p>'+html.escape(c['text']).replace('\n','<br>')+'</p></section>' for c in body['cards'])
    return '<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; style-src &#39;unsafe-inline&#39;; form-action &#39;none&#39;; base-uri &#39;none&#39;"><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{font:16px/1.8 sans-serif;color:#254338;background:#f7f8f3;margin:20px;overflow-wrap:anywhere}section{background:white;border:1px solid #d9e5db;border-radius:12px;padding:20px;margin:16px 0}p{white-space:normal}</style></head><body><h1>'+html.escape(body['title'])+'</h1>'+cards+'<small>资源仅供学习参考；不向平台上报评分或执行账号操作。</small></body></html>'
