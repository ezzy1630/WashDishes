"""Bounded local motion proposals for use-frame ranking, never a use verdict."""
from __future__ import annotations

import re
import subprocess
import time

from . import putdown


def motion_samples(segments, start, end, box, *, deadline):
    """Decode tiny grey crops at 2 fps; encode full photos only for proposals.

    Full-resolution JPEG encoding every half second dominated the earlier dense
    experiment. A 32px crop preserves lid/hand changes with bounded memory.
    """
    import numpy as np
    x,y,w,h = box
    previous = None
    samples = []
    for a,b,path in segments:
        if b < start or a > end:
            continue
        if time.monotonic()>=deadline:
            break
        lo,hi = max(0,start-a),min(b,end)-a
        command = ['nice','-n','15','ffmpeg','-nostdin','-hide_banner','-loglevel','info',
                   '-threads','1','-t',str(hi),'-i',str(path),'-an','-filter_threads','1',
                   '-vf',f'trim=start={lo}:end={hi},fps=2,crop=iw*{w}:ih*{h}:iw*{x}:ih*{y},scale=32:32,format=gray,showinfo',
                   '-threads','1','-f','rawvideo','-']
        try:
            run = subprocess.run(command,capture_output=True,timeout=max(.01,min(10,deadline-time.monotonic())))
        except subprocess.TimeoutExpired:
            break
        times = [float(t) for t in re.findall(rb'pts_time:\s*([0-9.]+)',run.stderr)]
        for index,pts in enumerate(times[:len(run.stdout)//1024]):
            patch = np.frombuffer(run.stdout,np.uint8,1024,index*1024).astype(np.float32)
            # Remove exposure changes across the whole crop. Broad body changes
            # receive less weight than local hand/lid/content changes.
            patch -= patch.mean()
            if previous is not None:
                delta = np.abs(patch-previous)
                fraction = float((delta>12).mean())
                score = float(delta.mean())*min(1,(1-fraction)/.5)
                samples.append({'at':min(b,a+pts),'motion':score,'changed_fraction':fraction})
            previous = patch
    return samples


def proposal_times(samples, start, end, *, count=12):
    """Spread motion peaks across the visit and retain quiet-use controls."""
    chosen = []
    # One peak per temporal quarter prevents a single body crossing from taking
    # all the slots. The remaining peaks can capture brief lid handling.
    for q in range(4):
        pool = [s for s in samples if start+q*(end-start)/4 <= s['at'] <= start+(q+1)*(end-start)/4]
        if pool:
            chosen.append(max(pool,key=lambda s:s['motion'])['at'])
    for s in sorted(samples,key=lambda s:s['motion'],reverse=True):
        if all(abs(s['at']-at)>=3 for at in chosen):
            chosen.append(s['at'])
        if len(chosen)>=count-4:
            break
    for q in (.1,.35,.65,.9):
        at = start+q*(end-start)
        if samples:
            at = min(samples,key=lambda s:abs(s['at']-at))['at']
        if all(abs(at-t)>=1 for t in chosen):
            chosen.append(at)
    return sorted(set(chosen))[:count]


def rank(frame):
    """Contact confidence before visibility/motion; no action classification."""
    wrists = frame.get('pose',{}).get('wrists',[])
    return (bool(wrists), max((w[2] for w in wrists),default=0),
            frame.get('dish_visible',False),frame.get('spot_motion',0))
