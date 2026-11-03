// Undo cancels an unsent answer. Saved names still follow the server's dispute rules.
// Serialize writes so a slow response cannot replace newer weekly totals.
/** @typedef {import('./types.js').Visit} Visit */
/** @typedef {{visit:Visit,action:'name'|'verdict',body:unknown,message:string,saving:boolean,timer:ReturnType<typeof setTimeout>|null}} Answer */
/** @param {{send:(entry:Answer)=>Promise<void>,changed:()=>void,saved:(entry:Answer)=>Promise<void>,failed:(entry:Answer,error:unknown)=>void,delay?:number}} options */
export function pendingAnswers({ send, changed, saved, failed, delay = 5000 }) {
  /** @type {Map<string|number,Answer>} */
  const entries = new Map();
  let writes = Promise.resolve();
  /** @param {Answer} entry */
  function commit(entry) {
    if (entry.saving || !entries.has(entry.visit.id)) return;
    if (entry.timer !== null) clearTimeout(entry.timer);
    entry.saving = true;
    changed();
    writes = writes.then(async () => {
      if (entries.get(entry.visit.id) !== entry) return;
      try {
        await send(entry);
        if (entries.get(entry.visit.id) !== entry) return;
        entries.delete(entry.visit.id);
        await saved(entry);
      } catch (error) {
        if (entries.get(entry.visit.id) !== entry) return;
        entries.delete(entry.visit.id);
        failed(entry, error);
      }
      changed();
    });
  }
  return {
    entries,
    /** @param {Visit} visit @param {"name"|"verdict"} action @param {unknown} body @param {string} message */
    add(visit, action, body, message) {
      if (entries.has(visit.id)) return;
      /** @type {Answer} */
      const entry = {
        visit,
        action,
        body,
        message,
        saving: false,
        timer: null,
      };
      entry.timer = setTimeout(() => commit(entry), delay);
      entries.set(visit.id, entry);
      changed();
    },
    /** @param {string|number} id */
    undo(id) {
      const entry = entries.get(id);
      if (!entry || entry.saving) return;
      if (entry.timer !== null) clearTimeout(entry.timer);
      entries.delete(id);
      changed();
    },
    cancel() {
      for (const entry of entries.values()) {
        if (entry.timer !== null) clearTimeout(entry.timer);
      }
      entries.clear();
    },
    flush() {
      for (const entry of entries.values()) commit(entry);
      return writes;
    },
  };
}
