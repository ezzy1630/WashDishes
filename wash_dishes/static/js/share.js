// Send a proof picture the way a phone sends a photo: the share sheet opens with the JPEG attached,
// so on an iPhone it goes straight into Messages. Browsers that cannot share files download it.
import { h, icon, toast } from './core.js';

const probe = typeof File === 'function' ? new File([new Uint8Array(1)], 'probe.jpg', { type: 'image/jpeg' }) : null;
export const canSendFiles = Boolean(probe && navigator.canShare?.({ files: [probe] }));

// `preload` fetches the picture into memory once it has been shown. iOS opens the share sheet only
// straight from a tap; downloading first would use that tap up, so the file has to be ready.
export function sendButton(url, filename, { label = 'Send', kind = 'primary' } = {}) {
  if (!canSendFiles) {
    const link = h('a', { class: `btn ${kind}`, href: url, download: filename }, icon('download', 16), h('span', {}, 'Download'));
    return { el: link, preload() {} };
  }
  let file = null, loading = null;
  const preload = () => loading ||= fetch(url, { credentials: 'same-origin' })
    .then(r => { if (!r.ok) throw new Error('The picture could not be loaded. Try again.'); return r.blob(); })
    .then(blob => { file = new File([blob], filename, { type: 'image/jpeg' }); })
    .catch(e => { loading = null; throw e; });
  const button = h('button', { type: 'button', class: `btn ${kind}` }, icon('share', 16), h('span', {}, label));
  button.addEventListener('click', async () => {
    try {
      if (!file) await preload();
      await navigator.share({ files: [file] });
    } catch (e) {
      if (e.name === 'AbortError') return;  // the share sheet was closed
      // Waiting for the download used up the tap; the picture is ready now, so the next tap works.
      toast(e.name === 'NotAllowedError' ? 'Ready. Tap Send again.' : e.message || 'Could not open sharing.', { error: e.name !== 'NotAllowedError' });
    }
  });
  return { el: button, preload: () => preload().catch(() => {}) };
}
