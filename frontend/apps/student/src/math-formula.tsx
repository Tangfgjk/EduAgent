import katex from 'katex';
import 'katex/dist/katex.min.css';
export function Formula({ text }: { text: string }) {
  const html = katex.renderToString(text.slice(0,2000), { throwOnError: false, trust: false, strict: 'warn', output: 'htmlAndMathml', maxExpand: 100, maxSize: 20 });
  return <span className="classroom-formula" dangerouslySetInnerHTML={{ __html: html }}/>;
}
