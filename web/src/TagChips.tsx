// Stable across pages and reloads; never tied to array position or randomness.
function tagTone(tag: string): number {
  let hash = 0
  for (const char of tag.trim().normalize('NFKC').toLocaleLowerCase('zh-CN')) {
    hash = (Math.imul(hash, 31) + char.codePointAt(0)!) | 0
  }
  return (hash >>> 0) % 6
}

export function TagChips({ tags }: { tags: string[] }) {
  if (!tags.length) return null
  return <span className="tag-chips" aria-label="文章标签">{tags.map(tag => (
    <span key={tag} className={`tag-chip tag-tone-${tagTone(tag)}`}>{tag}</span>
  ))}</span>
}
