---
kind: external_dependency
name: Ultralytics YOLOv8 object detection
slug: ultralytics-yolo
category: external_dependency
category_hints:
    - sdk_real_api
scope:
    - '**'
source_files:
    - requirements-ai.txt
    - app/services/yolo_service.py
    - docs/yolo_training.md
---

- Role: Equipment detection pipeline; falls back to COCO `yolov8s.pt` when the custom `data/models/equipment_best.pt` is absent.
- Integration: `app/services/yolo_service.py` loads the model path from `.env` (`YOLO_MODEL_PATH`, `YOLO_FALLBACK_MODEL`, `YOLO_CONFIDENCE`, `YOLO_IMAGE_SIZE`, `YOLO_DEVICE`) and runs inference per frame.
- Stable shape: Custom model trained via `scripts/train_yolo.py` → `best.pt` copied to `data/models/equipment_best.pt` → reload through Settings page; class labels must be mapped to the 14 categories in `Jihozlar`.
- Verify exact inference API against ultralytics docs.