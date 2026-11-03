/** @typedef {Awaited<ReturnType<import('./api.js').getWeek>>} Week */
/** @typedef {Week['visits'][number]} Visit */
/** @typedef {Awaited<ReturnType<import('./api.js').getMe>>} Me */
/** @typedef {{me:Me|null,data:Week|null,offline:boolean,lastLoaded:number,selected:string,skipped:Set<string|number>,showWeak:boolean,working:boolean,loading:boolean}} State */
/** @typedef {{root:HTMLDivElement,state:State,href:(path:string)=>string,navigate:(path:string)=>void,render:()=>void,refresh:(initial?:boolean)=>Promise<void>,focusHeading:()=>void,mutate:(visit:Visit,action:'name'|'dispute'|'settle'|'verdict',body:unknown,message:string)=>Promise<void>,enter:()=>Promise<void>}} ScreenContext */
export {};
