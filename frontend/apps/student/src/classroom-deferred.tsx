import {lazy,Suspense,type ComponentProps} from 'react';
import {Loading} from '@wenjin/ui';
const Player=lazy(()=>import('./classroom-player').then(module=>({default:module.ClassroomPlayer})));
const MathText=lazy(()=>import('./math-formula').then(module=>({default:module.Formula})));
export function ClassroomPlayer(props:ComponentProps<typeof Player>){return <Suspense fallback={<Loading text="加载课堂播放器…"/>}><Player {...props}/></Suspense>;}
export function Formula(props:ComponentProps<typeof MathText>){return <Suspense fallback={<span>加载公式…</span>}><MathText {...props}/></Suspense>;}
