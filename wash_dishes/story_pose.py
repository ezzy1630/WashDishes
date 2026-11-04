"""Optional offline CPU wrists for ranking, never a use/ownership classifier."""
from __future__ import annotations

from pathlib import Path

from .putdown import box_iou


class PoseRanker:
    def __init__(self, model_path: Path, settings: Path):
        self.path, self.settings = model_path, settings
        self.model = None

    def __call__(self, jpeg, box, person_boxes):
        if not self.path.is_file():
            return {'available': False, 'wrists': [], 'person_boxes': []}
        import cv2
        import numpy as np

        from .vision import LocalVision, _import_yolo_offline
        if self.model is None:
            YOLO = _import_yolo_offline(self.settings)
            self.model = YOLO(str(self.path.resolve()), task='pose')
            self.model.add_callback('on_predict_start', LocalVision._limit_inference_threads)
        image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        height,width = image.shape[:2]
        bx,by,bw,bh = box
        # A distant seated person's torso is missed at full-frame 640px. Focus
        # on this work surface and the space above it, retaining head and arms.
        left,top = int(max(0,bx-.22)*width),int(max(0,by-.45)*height)
        right,bottom = int(min(1,bx+bw+.22)*width),int(min(1,by+bh+.18)*height)
        focused = image[top:bottom,left:right]
        result = self.model.predict(source=focused, device='cpu', imgsz=640, conf=.3, max_det=12, verbose=False)[0]
        if result.keypoints is None or result.boxes is None:
            return {'available': True, 'wrists': [], 'person_boxes': []}
        h, w = focused.shape[:2]
        bodies, wrists = [], []
        for rect, points in zip(result.boxes.xyxy.cpu().tolist(), result.keypoints.data.cpu().tolist()):
            a,b,c,d = rect
            person = [(a+left)/width,(b+top)/height,(c-a)/width,(d-b)/height]
            bodies.append(person)
            if person_boxes and not any(box_iou(person,p) >= .15 for p in person_boxes):
                continue
            for x,y,score in points[9:11]:
                x,y = (x+left)/width,(y+top)/height
                bx,by,bw,bh = box
                if score >= .35 and bx-.15*bw <= x <= bx+1.15*bw and by-.15*bh <= y <= by+1.15*bh:
                    wrists.append([x,y,score])
        return {'available': True, 'wrists': wrists, 'person_boxes': bodies}
