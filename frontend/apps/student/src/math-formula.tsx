import katex from 'katex';
import 'katex/dist/katex.min.css';
export function Formula({ text }: { text: string }) {
  try {
    const html=katex.renderToString(text.slice(0,2000), {throwOnError:true,trust:false,strict:'warn',output:'htmlAndMathml',maxExpand:100,maxSize:20});
    return <span className="classroom-formula" dangerouslySetInnerHTML={{__html:html}}/>;
  } catch {
    return <span role="status">公式暂无法显示，请参考文字讲解。<details><summary>查看公式原文</summary><code>{text}</code></details></span>;
  }
}


export function MathText({text}:{text:string}) {
  // Render only explicitly delimited formula spans; prose stays escaped React text.
  const pieces=text.split(/(\$\$[\s\S]+?\$\$|\$[^$\n]+?\$|\\\([\s\S]+?\\\)|\\\[[\s\S]+?\\\])/g);
  return <>{pieces.map((piece,i)=>piece.startsWith('$$')?<Formula key={i} text={piece.slice(2,-2)}/>:piece.startsWith('$')?<Formula key={i} text={piece.slice(1,-1)}/>:piece.startsWith('\\(')||piece.startsWith('\\[')?<Formula key={i} text={piece.slice(2,-2)}/>:<span key={i}>{piece}</span>)}</>;
}
