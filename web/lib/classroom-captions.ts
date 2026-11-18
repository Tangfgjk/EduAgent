export function parseCaptions(text: string) {
  const seconds = (value: string) => {
    const parts = value.trim().split(':').map(Number)
    return parts.length === 3 ? parts[0] * 3600 + parts[1] * 60 + parts[2] : parts[0] * 60 + parts[1]
  }
  return text.replace(/\r/g, '').trim().split(/\n\s*\n/).flatMap(block => {
    const lines = block.split('\n'), index = lines.findIndex(line => line.includes(' --> '))
    if (index < 0) return []
    const [start, end] = lines[index].split(' --> ')
    return [{ start: seconds(start), end: seconds(end.split(' ')[0]), text: lines.slice(index + 1).join(' ') }]
  })
}
