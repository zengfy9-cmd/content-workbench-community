import type { Idea } from './api'
export const READINESS: Record<string,string>={unreviewed:'待判断',ready:'可以写',needs_material:'补材料',later:'暂不做'}
export const BRIEF_FIELDS=[['audience','写给谁','哪类人，在做什么具体任务？',1500],['problem','为什么写','读者卡在哪里，希望得到什么结果？',2000],['thesis','我的观点','我准备表达什么判断？没想好可以留空。',3000],['available_materials','已有材料','已有什么对标、实操记录、截图或案例？',3000],['missing_materials','还缺什么','还需要补哪些事实、测试或自己的判断？',3000]] as const
export type Brief=Record<typeof BRIEF_FIELDS[number][0],string>
export function briefOf(idea:Idea):Brief{return Object.fromEntries(BRIEF_FIELDS.map(([key])=>[key,idea[key]||''])) as Brief}
