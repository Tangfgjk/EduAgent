// Local TeX -> SVG -> PNG. No browser, CDN or arbitrary generated code.
import { mathjax } from '@mathjax/src/js/mathjax.js'
import { TeX } from '@mathjax/src/js/input/tex.js'
import { SVG } from '@mathjax/src/js/output/svg.js'
import { liteAdaptor } from '@mathjax/src/js/adaptors/liteAdaptor.js'
import { RegisterHTMLHandler } from '@mathjax/src/js/handlers/html.js'
import '@mathjax/src/js/util/asyncLoad/esm.js'
import '@mathjax/src/js/input/tex/base/BaseConfiguration.js'
import '@mathjax/src/js/input/tex/ams/AmsConfiguration.js'
import '@mathjax/src/js/input/tex/newcommand/NewcommandConfiguration.js'
import '@mathjax/src/js/input/tex/textmacros/TextMacrosConfiguration.js'
import { MathJaxNewcmFont } from '@mathjax/mathjax-newcm-font/js/svg.js'
import sharp from 'sharp'

try {
  let input = ''
  for await (const chunk of process.stdin) {
    input += chunk
    if (input.length > 8192) throw new Error('formula_too_long')
  }
  const { formula } = JSON.parse(input)
  if (typeof formula !== 'string' || !formula.trim() || formula.length > 2000)
    throw new Error('invalid_formula')
  const adaptor = liteAdaptor()
  RegisterHTMLHandler(adaptor)
  const tex = new TeX({
    packages: ['base', 'ams', 'newcommand', 'textmacros'],
    maxBuffer: 4096, maxMacros: 1000,
    formatError(_jax, error) { throw new Error('invalid_tex: ' + error.message) },
  })
  const output = new SVG({ font: new MathJaxNewcmFont(), fontCache: 'none' })
  const document = mathjax.document('', { InputJax: tex, OutputJax: output })
  const node = await document.convertPromise(formula, { display: true, em: 70, ex: 35, containerWidth: 3000 })
  const svg = adaptor.firstChild(node)
  const box = adaptor.getAttribute(svg, 'viewBox').split(/\s+/).map(Number)
  const width = Math.ceil(box[2] * 0.07), height = Math.ceil(box[3] * 0.07)
  if (!width || !height || width > 8000 || height > 3000) throw new Error('formula_dimensions')
  adaptor.setAttribute(svg, 'width', String(width))
  adaptor.setAttribute(svg, 'height', String(height))
  adaptor.setAttribute(svg, 'color', '#174d3a')
  // MathJax emits CJK text as SVG text; select the installed Chinese fallback.
  const source = adaptor.outerHTML(svg).replace(/font-family="[^"]*"/g,
    'font-family="Microsoft YaHei, Noto Sans CJK SC, WenQuanYi Micro Hei, sans-serif"')
  const png = await sharp(Buffer.from(source)).extend({
    top: 5, bottom: 5, left: 5, right: 5, background: { r: 255, g: 255, b: 255, alpha: 0 },
  }).png().toBuffer()
  process.stdout.write(JSON.stringify({ data: png.toString('base64'), width, height }))
} catch (error) {
  process.stderr.write(String(error.message).slice(0, 500))
  process.exitCode = 1
}
