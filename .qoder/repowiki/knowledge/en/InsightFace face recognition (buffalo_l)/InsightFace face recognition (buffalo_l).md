---
kind: external_dependency
name: InsightFace face recognition (buffalo_l)
slug: insightface
category: external_dependency
category_hints:
    - sdk_real_api
scope:
    - '**'
source_files:
    - requirements-ai.txt
    - app/services/face_service.py
---

### InsightFace + ONNX Runtime
- Role: Employee face enrollment and matching; optional — app starts without it and reports face recognition as unavailable.
- Integration: `app/services/face_service.py` uses the `buffalo_l` model (downloaded to `~/.insightface` on first run); cosine similarity threshold (`FACE_SIMILARITY_THRESHOLD`) and runner-up margin (`FACE_MATCH_MARGIN`) gate matches; CUDA acceleration via `onnxruntime-gpu` + `FACE_PROVIDERS=CUDAExecutionProvider`.
- Stable shape: Enrollment stores multiple reference images per employee; unknown person handling produces `UNKNOWN_PERSON_*` events marked `PENDING` for operator review.
- Verify exact InsightFace API calls against official docs.