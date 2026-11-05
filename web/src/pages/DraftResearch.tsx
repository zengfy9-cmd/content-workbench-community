import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { Benchmark, BenchmarkCard, BenchmarkLink, BenchmarkReview } from '../benchmarkTypes'
import { go } from '../App'
import { Empty, useToast } from '../ui'
import { ReferencesPanel } from './References'
import { BenchmarkAnalysisCard, BenchmarkOverview, BenchmarkReader } from './BenchmarkParts'

export function DraftResearch({ draftId, initialSource, initialKey, left, onPosition, onLibrary, onUse, allowLearning, onLearning }: {
  draftId: number; initialSource?: number; initialKey?: string; left: boolean; onPosition: () => void; onLibrary: () => void
  onUse: (card: BenchmarkCard, title: string) => void; allowLearning: boolean; onLearning: (value: boolean) => void
}) {
  const toast = useToast()
  const [links, setLinks] = useState<BenchmarkLink[]>([])
  const [selected, setSelected] = useState<number | null>(initialSource || null)
  const [source, setSource] = useState<Benchmark | null>(null)
  const [mode, setMode] = useState<'originals' | 'structure'>(initialSource ? 'structure' : 'originals')
  const [active, setActive] = useState('')
  const [error, setError] = useState('')
  const reader = useRef<HTMLDivElement>(null)
  const load = useCallback(() => api.get<BenchmarkLink[]>(`/api/drafts/${draftId}/benchmarks`).then(items => {
    setLinks(items)
    setSelected(current => items.some(i => i.benchmark_id === current) ? current : initialSource || items[0]?.benchmark_id || null)
  }).catch(e => setError(e.message)), [draftId, initialSource])
  useEffect(() => { void load() }, [load, mode])
  useEffect(() => { if (initialSource) { setSelected(initialSource); setMode('structure') } }, [initialSource])
  const analysisId = links.find(l => l.benchmark_id === selected)?.analysis_id
  useEffect(() => {
    if (!selected) { setSource(null); return }
    let alive = true
    setSource(null); setError('')
    api.get<Benchmark>(`/api/benchmarks/${selected}${analysisId ? `?analysis_id=${analysisId}` : ''}`).then(item => {
      if (!alive) return
      setSource(item); setActive(initialKey && item.passages.some(p => p.key === initialKey) ? initialKey : item.analysis?.paragraphs[0]?.key || item.passages[0]?.key || '')
    }).catch(e => { if (alive) setError(e.message) })
    return () => { alive = false }
  }, [selected, analysisId, initialKey])
  async function applyCard(card: BenchmarkCard) {
    if (!source?.analysis) return
    try {
      await api.post(`/api/benchmarks/${source.id}/draft`, { draft_id: draftId, analysis_id: source.analysis.id })
      onUse(card, source.title)
      toast('已作为本篇参考，请在正文填写自己的事实与观点')
    } catch (e) { toast((e as Error).message, 'error') }
  }
  function review(value: BenchmarkReview) { setSource(s => s?.analysis?.id === value.analysis_id ? { ...s, reviews: [...s.reviews.filter(r => r.paragraph_key !== value.paragraph_key), value] } : s) }
  return <div className="draft-research">
    <div className="draft-research-tools">
      <div className="row wrap"><div className="segmented"><button aria-pressed={mode === 'originals'} onClick={() => setMode('originals')}>原文并读</button><button aria-pressed={mode === 'structure'} onClick={() => setMode('structure')}>拆解与结构卡</button></div><button className="btn sm" onClick={onLibrary}>打开对标库</button></div>
      {mode === 'structure' && <div className="row"><select className="select grow" aria-label="当前参考对标" value={selected || ''} onChange={e => setSelected(Number(e.target.value))}><option value="" disabled>选择参考文章</option>{links.map(link => <option key={link.benchmark_id} value={link.benchmark_id}>{link.title}</option>)}</select><button className="btn ghost sm" onClick={onPosition}>{left ? '放右边' : '放左边'}</button></div>}
    </div>
    {mode === 'originals' ? <ReferencesPanel draftId={draftId} left={left} onPosition={onPosition} /> : <div className="draft-structure-scroll">
      {error && <div className="callout" role="alert">{error}</div>}
      {!source ? <Empty icon={null} title={links.length ? '正在读取参考…' : '给这篇稿子选一份参考'}><p>到对标库选择原文，点击“关联到当前稿件”。</p><button className="btn" onClick={onLibrary}>选择对标</button></Empty> : <>
        <div className="draft-source-title"><strong>{source.title}</strong><button className="btn ghost sm" onClick={() => go(`benchmarks/${source.id}?draft=${draftId}`)}>查看完整拆解</button></div>
        <div className="small muted">参考结构，写自己的事实与观点。</div>
        <BenchmarkOverview source={source} />
        {source.analysis ? source.analysis.paragraphs.map(card => <BenchmarkAnalysisCard key={`${source.analysis!.id}-${card.key}`} source={source} card={card} index={source.passages.find(p => p.key === card.key)?.index || 1} active={active === card.key} onSelect={() => setActive(card.key)} onUse={() => applyCard(card)} onReview={review} />)
          : <><div className="callout small">这份原文尚未拆解。可以先手工参考，或打开对标库选择拆解。</div><BenchmarkReader source={source} active={active} onSelect={setActive} root={reader} /></>}
        <label className="draft-learning-option"><input type="checkbox" checked={allowLearning} onChange={e => onLearning(e.target.checked)} /><span>允许把本篇改稿用于我的长期写作习惯</span></label>
        <p className="small muted">本篇借鉴的作者写法默认只用于本篇；个人判断与原文分别保留。</p>
      </>}
    </div>}
  </div>
}
