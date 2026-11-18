import { copyPromptText } from './promptBlock'
import { useToast } from './ui'

const tasks = {
  write: { label: '长文写作', skill: 'xilo-write', action: '梳理事实和材料，按我已确认的长文风格起草或按指定范围改稿。交草稿和待核实项。' },
  script: { label: '写口播脚本', skill: 'xilo-script', action: '把来源整理为独立口播稿，分开写应读的话和画面备注。已有对应成稿和配音时，先复用，别改稿重录。' },
  talk: { label: '剪真人口播', skill: 'xilo-talk', action: '用我指定的真人录制素材和口播稿做粗剪检查，保留原片，先给我审核工程。' },
  film: { label: '做不露脸视频', skill: 'xilo-film', action: '先找视频工作区已有的稿件、旁白和画面方案；有旁白就做原音对齐和分镜，先交可编辑预览，按当前项目规则制作。' },
  review: { label: '发布前审校', skill: 'xilo-guoshen', action: '按接下来指定的平台和发布场景，区分规则风险、事实缺口和可替换改法；有成片时另核画面、字幕、实播和标签。只给预检报告。' },
}

export function XiloHandoff({ scope, draftId, platform }: { scope: 'draft' | 'final' | 'video'; draftId?: number; platform?: string }) {
  const toast = useToast()
  const keys: (keyof typeof tasks)[] = scope === 'video' ? ['talk', 'film', 'review']
    : scope === 'final' ? ['script', 'talk', 'film', 'review']
    : platform === 'douyin' ? ['script', 'review'] : ['write', 'script', 'review']

  return <details>
    <summary>用本机 Codex 继续制作</summary>
    <p className="small muted">选一步，复制到装有 Xilo Skill 的本机 Codex。你可以先补充本次要求。</p>
    <div className="row wrap">
      {keys.map(key => {
        const {label, skill, action} = tasks[key]
        const source = draftId === undefined ? '先接续我指定的本机视频项目。'
          : `内容工作台稿件 #${draftId}。若有明确确认的成稿，优先从成稿箱只读取这篇；涉及改稿时按我指定的版本处理。`
        return <button key={key} type="button" className="btn" onClick={async () => {
          try {
            await copyPromptText(`用 ${skill} 做「${label}」。${source}\n${action}\n未经本次确认别覆盖原稿、调用额外付费服务、导出视频或推送发布。`)
            toast('任务已复制，粘贴到本机 Codex 开始')
          } catch { toast('没能复制任务，请检查剪贴板权限后重试', 'error') }
        }}>{label}</button>
      })}
    </div>
  </details>
}
