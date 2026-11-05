export const PLATFORMS = { wechat: '公众号', xiaohongshu: '小红书', douyin: '抖音', x: '推特' } as const
export type ContentPlatform = keyof typeof PLATFORMS
export function PlatformFilter({value,onChange}:{value:string;onChange:(v:ContentPlatform|'all')=>void}) {
 return <div className="segmented platform-filter" role="group" aria-label="目标平台">{(['all',...Object.keys(PLATFORMS)] as (ContentPlatform|'all')[]).map(p=><button key={p} aria-pressed={value===p} onClick={()=>onChange(p)}>{p==='all'?'全部平台':PLATFORMS[p]}</button>)}</div>
}
