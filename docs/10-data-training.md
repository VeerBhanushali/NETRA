<!-- status: DRAFT (recovered from interrupted run; not yet adversarially hardened) -->

## Datasets, Training & Evaluation

This section is the answer to one question: **how do 4–6 students with an AMD RX 6500M (4 GB VRAM, no CUDA) and 36 hours get models that actually work on stage?** The answer is a strict split — *anything that needs a GPU gradient step happens on free Colab/Kaggle and is exported to ONNX; everything on the dev machine is inference-only through `DmlExecutionProvider`, plus one tiny CPU-trainable head.*

---

### 1. Training triage — what actually needs training

Most of this system needs **zero** training. Read this table before you download a single byte.

| Component | Train? | Rationale / plan | GPU cost |
|---|---|---|---|
| Vehicle detector (YOLOv8n/s, COCO) | **No** | COCO already has `car(2)`, `motorcycle(3)`, `bus(5)`, `truck(7)`. Filter class IDs. Indian traffic is in-distribution enough. | 0 |
| Plate detector (YOLOv8n, 1 class) | **Yes — short fine-tune** | COCO has no `license_plate` class. 1-class detection is the easiest task in vision; ~8k images and 60 epochs is plenty. | ~60–75 min, free T4 |
| Tracker (ByteTrack) | **No** | Pure IoU + Kalman association, no learned weights. | 0 |
| DeepSORT ReID embedder | **No** | Use the pretrained OSNet/MobileNet ReID checkpoint as-is. Prefer ByteTrack for MVP — no second model in 4 GB VRAM. | 0 |
| Plate OCR (PaddleOCR PP-OCRv4 rec) | **No for MVP** | Latin uppercase + digits is what it was trained on. Gains come from a 36-char whitelist + RTO-grammar coercion, not from gradients. Fine-tune is **stretch**. | 0 (MVP) |
| VAD feature backbone (CLIP ViT-B/32 or VideoMAE) | **No** | Frozen. It is a feature extractor, never fine-tuned in this project. | 0 |
| VAD scoring head (3-layer MLP, MIL) | **Yes — but on CPU** | Trains in 2–5 minutes on precomputed features on your laptop. | 0 |
| Cloned plate / overspeed / loitering / watchlist | **No — rules** | Deterministic SQL/Python over `plate_reads`. No ML. These are 4 of your 6 alert types and they are 100% reliable on stage. | 0 |

**Total GPU budget for the whole project: one Colab session, ~2 hours.** Anyone proposing to "train an anomaly detector end-to-end" has misread the constraint.

---

### 2. Dataset table

> **Verify:** every size below is from memory and is approximate. Check the actual figure on the download page before committing bandwidth. Sizes drift as mirrors re-encode.

| Dataset | Used for | Approx. size | Licence / terms | Where | Download-time warning |
|---|---|---|---|---|---|
| **UCF-Crime** (Sultani et al.) | VAD head training; 13 anomaly classes (Fighting, RoadAccidents, Robbery, Shooting…) + Normal | **Videos ~120 GB.** Precomputed I3D/C3D features **~1–3 GB**. Kaggle extracted-frame mirrors ~10–15 GB | Academic / research use only, non-commercial | UCF CRCV project page; feature mirrors on GitHub (RTFM / MGFN repos) and Kaggle | **Never download the videos.** Take the *features* archive. The raw set is served slowly and will eat an entire night. |
| **ShanghaiTech Campus** | Second VAD benchmark; clean, fixed-camera, good sanity check for frame-level AUC | Frames ~40 GB; features **~1 GB** | Research use only | Original authors' Dropbox/OneDrive; weakly-supervised split by Zhong et al. | Distributed as per-frame JPEG folders → hundreds of thousands of small files. Extraction on Windows/NTFS is slower than the download. Use features. |
| **VIRAT Ground 2.0** | Loitering / dwell / person-vehicle interaction reference; realistic static CCTV geometry | ~70 GB (329 clips, ~8.5 h HD) | Public release, research use | data.kitware.com / official AWS mirror | Multi-part. **Pull 3–5 clips only** via direct file links. Do not clone the release. |
| **CCPD / CCPD2019** | Plate **localisation** pretraining (bbox + 4-corner geometry), tilt/blur/rain/night subsets | ~12 GB (~250–290k images) | Research use (see repo LICENSE) | GitHub `detectRecog/CCPD` | Chinese plate *text* — useless for OCR, excellent for the detector. Grab one subset dir (`ccpd_base`, `ccpd_blur`, `ccpd_tilt`) not the whole thing. |
| **Roboflow Universe — Indian / ANPR plate sets** | **Primary plate-detector training set.** Already YOLOv8-format with a `data.yaml` | ~50 MB – 2 GB depending on set (~1k–25k images) | Usually CC BY 4.0 — **check per-project**, some are CC BY-NC | roboflow.com/universe, search "indian number plate", "ANPR", "license plate" | Fastest path to a working detector. Export as **YOLOv8** + "resize 640 stretch" and download the ZIP directly into Colab with the curl snippet Roboflow gives you. |
| **Kaggle — Indian vehicle number plate sets** | Extra plate crops + real Indian plate *strings* for the OCR held-out set | ~100 MB – 2 GB (~500–5k images) | Kaggle dataset licence varies (CC0 / CC BY-SA / "unknown") — record it | kaggle.com/datasets | Labels are often filename-encoded (`MH12DE1433.jpg`) and dirty. Budget 45 min of a human hand-checking 300 of them for the eval set. |
| **Kaggle — real-time traffic / vehicle detection video** | RTSP replay source for the simulated city; latency + FPS benchmarking | ~1–8 GB | Varies per dataset | kaggle.com/datasets | Only 4–8 clips of 2–5 min are needed. Re-encode to 720p H.264 CFR 25 fps before looping — variable-frame-rate source files break MediaMTX timing and make your speed maths wrong. |
| **Your own phone footage** | Day-zero clip; India-correct plates, guaranteed licence | ~200 MB for 5 min | Yours | A flyover / society gate | **Do this in hour one.** It is the only dataset with zero download time and zero licence ambiguity. |

**DPDP Act 2023 note:** all of the above contain personal data (plates, faces). Keep everything under `data/` and `.gitignore` it — no raw footage in the repo, no re-publishing of downloaded corpora. The *seeded demo data* (§8) is synthetic and therefore carries no personal data, which is the correct thing to say to a judge who asks about privacy.

---

### 3. Staged acquisition — do not download 100 GB first

The single most common way a hackathon team loses day one is a 6-hour download on venue Wi-Fi that fails at 80%.

**Stage 0 — hours 0–3. "Prove the loop." Target: < 500 MB total.**
- 1 traffic clip (your phone, or one Kaggle clip) → looped as RTSP.
- 200–400 plate images from one Roboflow project (~80 MB) — **not for training yet**, for the held-out eval set.
- A community pretrained YOLOv8 licence-plate `.pt` from Roboflow/GitHub, exported straight to ONNX.
- Success criterion: RTSP frame → vehicle box → plate box → OCR string → row in `plate_reads` → pin on the Mapbox map. **No training has happened.** If the loop is not closed by hour 3, everything after this is irrelevant.

**Stage 1 — hours 3–10. "Make the detector ours."**
- One Roboflow plate dataset, 5–10k images (~500 MB–1.5 GB), downloaded *inside Colab*, never onto the laptop.
- Fine-tune (§4), export ONNX, pull back a ~12 MB file.

**Stage 2 — hours 10–20. "Harden OCR + turn on VAD."**
- Generate 5–10k synthetic Indian plate crops locally (§6) — 0 bytes downloaded.
- Download **UCF-Crime I3D/CLIP features only** (~1–3 GB) or extract your own on Colab (§5).

**Stage 3 — stretch.** CCPD subset for detector robustness, ShanghaiTech features for a second AUC number in the deck, PaddleOCR rec fine-tune.

Rule: **one person owns downloads**, runs them in the background, and posts sizes in the team channel before starting. Nobody else touches the network with a 10 GB request.

---

### 4. Colab workflow — plate detector, end to end

```python
# ── Cell 1: environment + persistent checkpoints ────────────────────────────
!nvidia-smi -L                      # confirm you got a T4 (sometimes it's a K80 — restart runtime)
from google.colab import drive; drive.mount('/content/drive')
!mkdir -p /content/drive/MyDrive/anpr/runs
!pip -q install "ultralytics==8.3.*" onnx onnxsim onnxruntime
# > Verify: exact ultralytics patch version; the training kwargs below are stable across 8.1–8.3.
```

```python
# ── Cell 2: dataset (download INSIDE Colab, never to the laptop) ────────────
!curl -L "<ROBOFLOW_YOLOV8_EXPORT_URL>" -o /content/plates.zip
!unzip -q /content/plates.zip -d /content/plates
```

```yaml
# /content/plates/data.yaml  — overwrite whatever Roboflow shipped, paths must be absolute
path: /content/plates
train: train/images
val: valid/images
test: test/images
names:
  0: plate
```

```python
# ── Cell 3: train ───────────────────────────────────────────────────────────
from ultralytics import YOLO
model = YOLO("yolov8n.pt")           # nano: 4 GB VRAM at inference time is the binding constraint
model.train(
    data="/content/plates/data.yaml",
    epochs=60, imgsz=640, batch=32, device=0, workers=4, seed=0,
    optimizer="AdamW", lr0=0.002, lrf=0.01, warmup_epochs=3, cos_lr=True,
    patience=15,                     # early stop; 1-class converges fast
    # --- augmentation tuned for CCTV plates ---
    hsv_h=0.015, hsv_s=0.6, hsv_v=0.5,
    degrees=5.0, translate=0.10, scale=0.45, shear=2.0, perspective=0.0005,
    fliplr=0.0,                      # NEVER mirror: the IND strip is left-anchored and mirrored
                                     # crops teach the downstream OCR crop pipeline garbage
    mosaic=1.0, close_mosaic=10, mixup=0.0, copy_paste=0.0,
    # --- checkpointing: Colab WILL disconnect ---
    project="/content/drive/MyDrive/anpr/runs", name="plate_y8n_640",
    exist_ok=True, save_period=5,
)
```

**Realistic cost on a free T4:** yolov8n @ 640, batch 32, ~8k images ≈ **55–75 s/epoch** → 60 epochs ≈ **60–75 minutes**, usually early-stopping around epoch 35–45. yolov8s is ~2× that and is *not* worth it — you cannot afford its inference cost on DirectML anyway.

**Colab session reality:** free tier gives ~12 h/day of quota but disconnects after ~90 min idle and can preempt at any time. Consequences: (a) `save_period=5` writes `last.pt` to Drive every 5 epochs; (b) keep the browser tab focused; (c) resume with

```python
YOLO("/content/drive/MyDrive/anpr/runs/plate_y8n_640/weights/last.pt").train(resume=True)
```

```python
# ── Cell 4: validate, then export ONNX ──────────────────────────────────────
best = YOLO("/content/drive/MyDrive/anpr/runs/plate_y8n_640/weights/best.pt")
print(best.val(data="/content/plates/data.yaml", split="test").box.map50)   # record this number
best.export(format="onnx", imgsz=640, opset=17, simplify=True,
            dynamic=False,   # DirectML strongly prefers static shapes — dynamic axes cost 2-3x
            half=False,      # keep fp32 master; make an fp16 copy separately and benchmark both
            nms=False)       # decode + NMS stays in Python so it is EP-portable
```

Output tensor for a 1-class model is `(1, 5, 8400)` = `[cx, cy, w, h, conf]` per anchor; you transpose and NMS in the worker. Also export the untouched vehicle detector once: `YOLO("yolov8n.pt").export(format="onnx", imgsz=640, opset=17, simplify=True, dynamic=False)`.

Then download `best.onnx` (~12 MB) and place it as `models/yolov8n_plate.onnx`. **Weights are not committed to git.** Publish them as a GitHub Release asset and pull with `scripts/fetch_models.py`, which validates against `models/manifest.json`:

```json
{
  "schema_version": 1,
  "models": {
    "vehicle_detector": {"file": "yolov8n_vehicle.onnx", "sha256": "<hex>", "input": [1,3,640,640],
                         "opset": 17, "classes": {"2":"car","3":"motorcycle","5":"bus","7":"truck"},
                         "trained_on": "COCO (pretrained, no fine-tune)"},
  "plate_detector":  {"file": "yolov8n_plate.onnx",   "sha256": "<hex>", "input": [1,3,640,640],
                         "opset": 17, "classes": {"0":"plate"}, "map50": 0.0,
                         "trained_on": "roboflow:<project>@<version>"},
    "ocr_rec":         {"dir": "plate_ocr/rec", "charset": "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"},
    "vad_features":    {"file": "clip_vitb32_visual.onnx", "dim": 512},
    "vad_head":        {"file": "vad_head.onnx", "input": [1,512], "threshold": 0.65}
  }
}
```

**Expected local inference, `DmlExecutionProvider` on RX 6500M / 4 GB, 640×640 fp32** — these are the numbers the eval harness must confirm, not marketing claims:

| Model | Per-call | Note |
|---|---|---|
| yolov8n (vehicle **or** plate) | ~18–30 ms | ~35–55 FPS single stream |
| Both detectors, same frame | ~45–60 ms | 4 GB VRAM holds two nano graphs; a third model will thrash |
| PaddleOCR PP-OCRv4 rec, 48×320 crop, **CPU** | ~8–15 ms | Leave OCR on CPU — it keeps VRAM free and is fast enough |
| CLIP ViT-B/32 image embed, DML | ~10–15 ms | Run at 2 fps per camera, not 25 |

Practical envelope: **3–4 simulated cameras at 8–12 analysed FPS each** (process every 2nd–3rd frame, track through the gaps). Say this on stage; do not claim 25 FPS × 8 cameras.

---

### 5. VAD: extract features on Colab, train the head on your laptop

**The split, and why it is the right call.** For weakly-supervised VAD the backbone is frozen — training only touches a ~1 M-parameter MLP over pooled clip features. So:

1. **Colab (GPU, once):** run the frozen backbone over every video, emit one `(32, D)` array per video (32 temporal segments, `D=512` for CLIP ViT-B/32, `768` for VideoMAE-base, `2048` for I3D). Save as `.npy`. UCF-Crime → ~1900 files, **~50–250 MB total**.
2. **Laptop (CPU, repeatedly):** train the MIL head on those arrays. **2–5 minutes**, no GPU.

Why this matters more than it looks: at hour 30, when you discover the alert threshold is firing on every passing bus, you can retrain the head on your own demo clips **in under a minute, offline, at the venue, with no internet**. If you had coupled the head to the backbone you would be dead.

```python
# colab/extract_vad_features.py  (Colab)
import torch, numpy as np, cv2, clip
from pathlib import Path
dev = "cuda"
model, _ = clip.load("ViT-B/32", device=dev)          # > Verify: openai/CLIP vs open_clip API
model.eval()

def video_feature(path, n_seg=32, per_seg=4):
    cap = cv2.VideoCapture(str(path))
    frames = []
    while True:
        ok, f = cap.read()
        if not ok: break
        frames.append(f)
    cap.release()
    if len(frames) < n_seg: return None
    idx = np.linspace(0, len(frames)-1, n_seg*per_seg).astype(int)
    batch = torch.stack([preprocess_bgr(frames[i]) for i in idx]).to(dev)   # (128,3,224,224)
    with torch.no_grad():
        emb = model.encode_image(batch).float()                             # (128,512)
    emb = torch.nn.functional.normalize(emb, dim=-1)
    return emb.reshape(n_seg, per_seg, -1).mean(1).cpu().numpy()            # (32,512)

for v in Path("/content/ucf").rglob("*.mp4"):
    f = video_feature(v)
    if f is not None:
        np.save(f"/content/drive/MyDrive/anpr/feats/{v.stem}.npy", f.astype(np.float32))
```

```python
# ai/vad/train_head.py  (LOCAL, CPU, ~3 minutes)
import torch, torch.nn as nn, numpy as np, glob, os
D = 512

class VADHead(nn.Module):                      # Sultani et al. 3-layer FC
    def __init__(s, d=D):
        super().__init__()
        s.net = nn.Sequential(nn.Linear(d,512), nn.ReLU(), nn.Dropout(0.6),
                              nn.Linear(512,32), nn.ReLU(), nn.Dropout(0.6),
                              nn.Linear(32,1),  nn.Sigmoid())
    def forward(s,x): return s.net(x)

def mil_loss(sa, sn, l_sparse=8e-5, l_smooth=8e-5):
    """sa,sn: (B,32,1) anomalous / normal bag scores."""
    rank = torch.relu(1.0 - sa.max(1).values + sn.max(1).values).mean()
    sparse = sa.sum(dim=1).mean()
    smooth = ((sa[:,1:] - sa[:,:-1])**2).sum(dim=1).mean()
    return rank + l_sparse*sparse + l_smooth*smooth

head = VADHead(); opt = torch.optim.Adagrad(head.parameters(), lr=1e-3, weight_decay=1e-3)
for ep in range(2000):                          # ~2-4 min on CPU with B=30
    a, n = sample_bag(anom_files, 30), sample_bag(norm_files, 30)   # -> (30,32,512) tensors
    loss = mil_loss(head(a), head(n)); opt.zero_grad(); loss.backward(); opt.step()

torch.onnx.export(head, torch.randn(1, D), "models/vad_head.onnx",
                  input_names=["feat"], output_names=["score"], opset_version=17)
```

Inference-time smoothing (a threshold alone flickers): raise an alert only when **5 of the last 8 clip scores ≥ `VAD_ALERT_THRESHOLD = 0.65`**. 0.65 is chosen because MIL-trained sigmoid scores on normal urban traffic cluster around 0.15–0.35; 0.65 sits well above that band while staying below the 0.8+ that only clean, in-distribution fight footage reaches. Tune it on your own clips with `eval/run_eval.py --suite vad` and record the chosen value in `manifest.json`.

---

### 6. Synthetic Indian plates — cheap OCR hardening

Real annotated Indian plates are scarce; the character *renderer* is free. Generate 5–10k crops locally in ~3 minutes and use them for **OCR evaluation stress-testing** and, if you attempt the rec fine-tune, as ≤30% of the training mix. **Above ~30% synthetic the recogniser learns the renderer, not the road.**

```python
# tools/synth_plates.py
import random, csv, argparse
from pathlib import Path
import numpy as np, cv2
from PIL import Image, ImageDraw, ImageFont
import albumentations as A

STATES = ["MH","DL","KA","TN","UP","GJ","RJ","HR","WB","AP","TS","KL","PB","MP",
          "BR","OD","JH","CG","AS","GA","UK","HP","JK","CH","PY"]
ALPHA, DIGIT = "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "0123456789"

def random_plate(rng):
    if rng.random() < 0.05:                                   # BH series
        return f"{rng.randint(20,26):02d}BH{rng.randint(0,9999):04d}{''.join(rng.choices(ALPHA,k=rng.choice([1,2])))}"
    return (rng.choice(STATES) + f"{rng.randint(1,99):02d}"
            + "".join(rng.choices(ALPHA, k=rng.choice([1,2,2,3])))
            + f"{rng.randint(0,9999):04d}")

def render(text, font_path, rng, two_row=False):
    """Private = white bg/black ink; commercial = yellow bg/black ink. 500x120mm -> 4.17:1."""
    commercial = rng.random() < 0.18
    bg = (255,214,0) if commercial else (252,252,250)
    W,H = (200,100) if two_row else (500,120)
    img = Image.new("RGB",(W,H),bg); d = ImageDraw.Draw(img)
    if two_row:
        f = ImageFont.truetype(font_path, 42)
        d.text((W//2, 26), text[:4], font=f, fill=(15,15,15), anchor="mm")
        d.text((W//2, 72), text[4:], font=f, fill=(15,15,15), anchor="mm")
    else:
        f = ImageFont.truetype(font_path, 78)
        d.text((W//2+14, H//2), text, font=f, fill=(15,15,15), anchor="mm")
        d.rectangle([6,6,34,H-6], fill=(0,51,153))            # IND blue strip
        d.text((20,H-22), "IND", font=ImageFont.truetype(font_path,16), fill=(255,255,255), anchor="mm")
    d.rectangle([2,2,W-3,H-3], outline=(20,20,20), width=3)
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)

# The two augmentations that matter most are Downscale and ImageCompression: real city-CCTV
# plates are 60-120 px wide and re-encoded H.264 at 2-4 Mbps. Everything else is secondary.
AUG = A.Compose([
    A.Perspective(scale=(0.02,0.09), p=0.8),
    A.Affine(rotate=(-7,7), shear={"x":(-8,8)}, scale=(0.9,1.1), p=0.7),
    A.OneOf([A.MotionBlur(blur_limit=(3,11)), A.Defocus(radius=(2,5)),
             A.GaussianBlur(blur_limit=(3,7))], p=0.85),
    A.RandomBrightnessContrast(brightness_limit=0.35, contrast_limit=0.35, p=0.9),
    A.RandomGamma(gamma_limit=(60,150), p=0.4),
    A.OneOf([A.RandomSunFlare(src_radius=90), A.RandomShadow(),
             A.RandomRain(blur_value=2), A.RandomFog()], p=0.45),   # glare / rain / haze
    A.CoarseDropout(max_holes=5, max_height=22, max_width=40,
                    fill_value=(70,58,42), p=0.35),                  # mud / sticker occlusion
    A.ISONoise(p=0.5), A.GaussNoise(var_limit=(5,45), p=0.5),
    A.Downscale(scale_min=0.25, scale_max=0.60, p=0.8),
    A.ImageCompression(quality_lower=28, quality_upper=72, p=0.9),
])
# > Verify: albumentations >=1.4 renamed several args (ImageCompression -> quality_range,
# > CoarseDropout -> num_holes_range/hole_height_range). Pin `albumentations==1.4.*` and adjust.

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=8000)
    ap.add_argument("--font", default="assets/fonts/UKNumberPlate.ttf")
    ap.add_argument("--out", default="data/synth_plates")
    a = ap.parse_args(); rng = random.Random(1337)
    out = Path(a.out); (out/"img").mkdir(parents=True, exist_ok=True)
    with open(out/"labels.csv","w",newline="") as fh:
        w = csv.writer(fh); w.writerow(["image_path","plate_text","synthetic"])
        for i in range(a.n):
            t = random_plate(rng)
            img = AUG(image=render(t, a.font, rng, two_row=rng.random()<0.25))["image"]
            p = out/"img"/f"{i:06d}.jpg"
            cv2.imwrite(str(p), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
            w.writerow([str(p), t, 1])
```

> **Verify:** the font. Indian HSRP plates use a typeface very close to Charles Wright; a free clone such as `UKNumberPlate.ttf` is visually adequate and redistributable. Confirm the licence of whatever TTF you ship in `assets/fonts/`. Fall back to `DejaVuSans-Bold.ttf` if unsure — slightly wrong glyph shapes, still useful.

**Free accuracy without any training** (do this before considering a fine-tune):
- Character whitelist: restrict the recogniser dictionary to `0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ` (`rec_char_dict_path` → a 36-line custom dict, `use_space_char=False`). Kills every punctuation and lowercase hallucination.
- **RTO-grammar coercion**: positions carry type information. Chars 0–1 must be letters, the final 4 must be digits. So map `0→O, 1→I` in the head and `O/D/Q→0, I/L→1, Z→2, S→5, B→8, G→6` in the tail. This is worth several points of exact-match for zero cost, and the eval harness reports accuracy with and without it so you can prove the lift.

---

### 7. The evaluation harness

Build this by **hour 12**, not hour 34. Without it you cannot tell whether a change helped, and you have no numbers for the deck.

```text
eval/
  __init__.py
  run_eval.py            # CLI entrypoint
  metrics.py             # cer, exact_match, coerce_to_grammar, auc, latency percentiles
  suites/
    ocr.py detector.py vad.py latency.py
  labels/
    plates_holdout.csv   # image_path,plate_text,source,difficulty   (>=300 rows, hand-checked)
    det_holdout/         # images/ + labels/  (YOLO txt, class 0 = plate)
    vad_holdout.csv      # video_path,frame_start,frame_end,label  (0 normal / 1 anomalous)
  results/
    eval_20260905_1830.json
    eval_20260905_1830.md
```

```python
# eval/metrics.py  (the parts that are easy to get wrong)
import re
PLATE_RE = re.compile(r"^(?:[A-Z]{2}[0-9]{1,2}[A-Z]{0,3}[0-9]{4}|[0-9]{2}BH[0-9]{4}[A-Z]{1,2})$")
_TO_DIGIT = {"O":"0","D":"0","Q":"0","I":"1","L":"1","Z":"2","S":"5","B":"8","G":"6","A":"4","T":"7"}
_TO_ALPHA = {v:k for k,v in reversed(list(_TO_DIGIT.items()))}

def normalize_plate(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())

def coerce_to_grammar(s: str) -> str:
    """Heuristic: AA <1-2 digits> <0-3 letters> NNNN. Measured, not assumed — run_eval reports
    exact-match with and without this so its real lift is visible."""
    s = normalize_plate(s)
    if not (8 <= len(s) <= 11): return s
    head = "".join(_TO_ALPHA.get(c, c) for c in s[:2])
    tail = "".join(_TO_DIGIT.get(c, c) for c in s[-4:])
    mid  = s[2:-4]
    k = min(2 if len(mid) >= 3 else 1, len(mid))
    return head + "".join(_TO_DIGIT.get(c,c) for c in mid[:k]) \
                + "".join(_TO_ALPHA.get(c,c) for c in mid[k:]) + tail

def cer(gt: str, pred: str) -> float:
    gt, pred = normalize_plate(gt), normalize_plate(pred)
    if not gt: return 1.0
    prev = list(range(len(pred)+1))
    for i, g in enumerate(gt, 1):
        cur = [i]
        for j, p in enumerate(pred, 1):
            cur.append(min(prev[j]+1, cur[j-1]+1, prev[j-1] + (g != p)))
        prev = cur
    return prev[-1] / len(gt)
```

```bash
python -m eval.run_eval --suite all --models-dir models \
       --labels eval/labels --provider DmlExecutionProvider --out eval/results
```

**Metrics, targets, and why each one exists**

| Metric | Definition | MVP target | Why |
|---|---|---|---|
| `plate_exact_match` | Whole-string match, normalised | ≥ 0.75 clean / ≥ 0.55 overall | The headline number. Report both splits; a single blended figure hides everything. |
| `plate_exact_match_coerced` | Same, after `coerce_to_grammar` | ≥ 0.82 clean | Proves the free grammar trick paid for itself. |
| `cer` | Mean Levenshtein / len(gt) | ≤ 0.08 | Shows *near* misses. 1 wrong char out of 10 is a very different system from garbage. |
| `auto_accept_precision` | Exact-match rate among reads with `plate_conf ≥ 0.90` **and** `PLATE_RE` valid | **≥ 0.98** | The only metric with legal weight. A system that names the wrong car is worse than one that stays silent; DPDP purpose-limitation argues against persisting confident-but-wrong identities. |
| `coverage` | Fraction of ground-truth plate instances yielding any auto-accepted read | ≥ 0.60 | Precision alone is trivially gamed by accepting nothing. |
| `precision_coverage_curve` | Sweep `OCR_AUTO_ACCEPT_CONF` from 0.50→0.99 | — | **Put this chart in the deck.** It shows you understand the operating point, which is what separates a demo from a system. |
| `det_map50` | mAP@0.50 on `det_holdout`, class `plate` | ≥ 0.85 | 1-class detection; below 0.85 means the dataset or export is broken. |
| `vad_auc_frame` | Frame-level ROC-AUC on `vad_holdout` | 0.70–0.82 | The standard UCF-Crime protocol (Sultani C3D baseline ≈ 0.75; RTFM-class methods ≈ 0.84). Do **not** claim 0.9x. |
| `e2e_latency_p50/p95_ms` | Frame decode → row committed to `plate_reads` | p95 < 1500 ms | Judges ask "is it real time?". Give a percentile with a definition, not a vibe. |
| `fps_per_stream` | Analysed frames/s per camera, all models loaded | ≥ 8 | Must be measured with the same EP you demo on. |
| `mota` / `idf1` (**stretch**) | `motmetrics` on one annotated clip | — | Only if time remains. |

Deck table format — one row per configuration, hardware always stated:

```markdown
| Config | Exact | Exact+grammar | CER | Auto-accept P | Coverage | mAP@50 | VAD AUC | p95 ms | FPS |
|---|---|---|---|---|---|---|---|---|---|
| Baseline (pretrained plate det + PaddleOCR) | 0.61 | 0.68 | 0.14 | 0.94 | 0.48 | 0.79 | — | 1180 | 11 |
| + fine-tuned detector | 0.71 | 0.79 | 0.10 | 0.97 | 0.57 | 0.88 | — | 1150 | 11 |
| + whitelist + grammar coercion | 0.74 | 0.84 | 0.07 | 0.98 | 0.62 | 0.88 | 0.77 | 1140 | 11 |

Measured on: Win11, AMD RX 6500M 4 GB, ONNX Runtime DmlExecutionProvider (fp32),
720p25 RTSP replay, 3 concurrent cameras. Held-out set: 312 hand-verified Indian plates.
```

(Numbers above are the **shape** of the table — replace every cell with measured values. Shipping invented numbers in a hackathon deck is how teams lose credibility in Q&A.)

---

### 8. `scripts/seed_demo_data.py` — the demo's insurance policy

Live inference will stumble at least once on stage: a stream drops, DirectML throws, the plate is muddy. The seeded dataset guarantees that **every alert type, every map layer, every chart and every table in the UI has real data behind it**, independent of the inference worker.

**Design rules (non-negotiable):**
1. **Deterministic.** `random.Random(1337)` everywhere. The same run produces the same plates, the same timestamps, the same alerts. You will rehearse against it 20 times.
2. **Labelled, never disguised.** Every seeded row carries `source = 'seed'`; live rows carry `source = 'live'`. The UI renders a hairline `SEEDED` micro-label chip on seeded rows. Say this out loud to judges: *"the historical day is synthetic; the alerts you are watching fire now are live."* Honesty here is a scoring advantage, not a weakness — and it is DPDP-clean, since synthetic plates are not personal data.
3. **`--reset` deletes only `source='seed'`.** It must never touch live inference output.
4. **Plausible, not uniform.** Sightings follow a bimodal rush-hour mixture (08:00–10:30, 18:00–20:30) so the timeline histogram looks like a city, not a random number generator.

**Planted scenarios** (distances verified by haversine over the real demo camera coordinates):

| Scenario | Plate | Setup | Fires |
|---|---|---|---|
| **Cloned plate** | `MH12DE1433` | CAM_03 (University Circle) → CAM_07 (Hadapsar), **10.98 km in 240 s ⇒ 165 km/h**; different `vehicle_class`/`vehicle_color` on the two reads | `cloned_plate`, severity 4 |
| **Overspeed** | `MH14GT7788` | CAM_01 → CAM_02, **2.145 km in 49 s ⇒ 158 km/h** vs `speed_limit_kmph = 60` | `overspeed`, severity 3 |
| **Loitering / casing** | `MH12AB0451` | 9 reads at CAM_05 across 22 min, min gap 70 s | `loitering`, severity 2 |
| **Watchlist hit** | `HR26DK8337` | Row in `watchlist` (`reason='STOLEN'`, `fir_number='0142/2026'`); appears at CAM_04 at 19:14 | `watchlist_hit`, severity 4 |
| **Behavioural anomaly** | — | One `vad_events` row, `score=0.81`, `label='fight'`, `clip_url` → a real 6 s clip in the `evidence` bucket | `anomaly_behaviour`, severity 3 |
| **Unreadable plate** | `null` | 12 reads with `plate_conf` 0.42–0.71 and grammar-invalid text | Feeds the manual-review queue so that screen is not empty |

**Rule thresholds this generator is calibrated against** (the rules engine must use exactly these):

- `IMPOSSIBLE_SPEED_KMPH = 150` — a legitimate single vehicle cannot average 150 km/h between two urban cameras; the margin also absorbs ±10 s of clock skew between edge workers.
- `OVERSPEED_MARGIN_KMPH = 15` over the posted limit — absolute margin rather than a percentage, so short baselines with ±1 s timestamp error do not generate false tickets.
- `MIN_SPEED_BASELINE_M = 500` — below 500 m, timestamp error dominates the speed estimate (at 100 m a ±1 s error is ±17%); pairs closer than this are ignored for speed.
- `LOITER_WINDOW_MIN = 20`, `LOITER_MIN_SIGHTINGS = 5` at a single camera — 5 passes of the same camera in 20 minutes is not a commute.

```python
#!/usr/bin/env python
"""scripts/seed_demo_data.py — deterministic demo data for the Pune corridor.
Usage:
  python scripts/seed_demo_data.py --date 2026-09-05 --vehicles 120
  python scripts/seed_demo_data.py --reset          # deletes ONLY source='seed'
"""
from __future__ import annotations
import argparse, math, os, random, uuid
from datetime import datetime, timedelta, timezone
from supabase import create_client

SEED = 1337
IST = timezone(timedelta(hours=5, minutes=30))
CAMERAS = [  # camera_id, name, lat, lon, heading, road, speed_limit
    ("CAM_01","Pashan Junction",       18.5590, 73.7869,  95, "Baner Road",        60),
    ("CAM_02","Aundh ITI Road",        18.5620, 73.8070, 120, "Aundh Road",        60),
    ("CAM_03","University Circle",     18.5510, 73.8290, 145, "Ganeshkhind Road",  50),
    ("CAM_04","Shivajinagar FC Road",  18.5230, 73.8420, 160, "FC Road",           40),
    ("CAM_05","Swargate Chowk",        18.5010, 73.8580,  70, "Satara Road",       40),
    ("CAM_06","Koregaon Park North",   18.5370, 73.8930,  90, "North Main Road",   50),
    ("CAM_07","Hadapsar Magarpatta",   18.5150, 73.9260,  75, "Solapur Road",      60),
    ("CAM_08","Kharadi Bypass",        18.5510, 73.9410,  20, "Nagar Road",        70),
]
STATES = ["MH","MH","MH","MH","KA","GJ","DL","RJ","UP","TS","HR","MP"]  # MH-weighted: it's Pune
ALPHA = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
CLASSES = [("car",0.58),("motorcycle",0.28),("truck",0.06),("bus",0.04),("auto",0.04)]
COLORS  = ["white","silver","grey","black","red","blue","brown"]

def haversine_km(a, b):
    R = 6371.0088
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = math.radians(b[0]-a[0]), math.radians(b[1]-a[1])
    h = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*R*math.asin(math.sqrt(h))

def plate(rng):
    return (rng.choice(STATES) + f"{rng.randint(1,48):02d}"
            + "".join(rng.choices(ALPHA, k=rng.choice([2,2,2,1,3])))
            + f"{rng.randint(1,9999):04d}")

def rush_hour_time(rng, day):
    """Bimodal mixture so the timeline histogram looks like a real city."""
    peak = rng.choice([9.25, 9.25, 19.0, 19.0, 14.0])
    h = min(23.99, max(0.0, rng.gauss(peak, 1.3 if peak != 14.0 else 3.2)))
    return day.replace(hour=int(h), minute=int(h % 1 * 60),
                       second=rng.randint(0, 59), microsecond=0)

def read_row(cam, ts, txt, conf, cls, color, rng, note=None):
    return {
        "read_id": str(uuid.uuid4()), "camera_id": cam, "track_id": str(uuid.uuid4()),
        "ts": ts.astimezone(timezone.utc).isoformat(),
        "plate_text": txt, "plate_conf": round(conf, 3),
        "char_confidences": [round(min(0.999, conf + rng.uniform(-0.06, 0.05)), 3)
                             for _ in range(len(txt or ""))],
        "vehicle_class": cls, "vehicle_color": color,
        "bbox": [rng.randint(400,900), rng.randint(300,600), rng.randint(60,150), rng.randint(20,55)],
        "crop_url": f"evidence/seed/{txt or 'unknown'}_{int(ts.timestamp())}.jpg",
        "source": "seed", "note": note,
    }

def build(day, n_vehicles, rng):
    reads, alerts, watch, vad = [], [], [], []
    cams = {c[0]: (c[2], c[3]) for c in CAMERAS}

    # --- background traffic: each vehicle walks a 2-4 camera route at a sane speed ---
    for _ in range(n_vehicles):
        txt = plate(rng)
        cls = rng.choices([c for c,_ in CLASSES], [w for _,w in CLASSES])[0]
        col = rng.choice(COLORS)
        route = rng.sample([c[0] for c in CAMERAS], rng.randint(2, 4))
        t = rush_hour_time(rng, day)
        for i, cam in enumerate(route):
            if i:
                d = haversine_km(cams[route[i-1]], cams[cam])
                t += timedelta(seconds=d / rng.uniform(22, 46) * 3600)   # 22-46 km/h urban
            reads.append(read_row(cam, t, txt, rng.uniform(0.88, 0.99), cls, col, rng))

    # --- PLANT 1: cloned plate. 10.98 km in 240 s => ~165 km/h => impossible travel ---
    t0 = day.replace(hour=13, minute=6, second=12, microsecond=0)
    reads.append(read_row("CAM_03", t0,                        "MH12DE1433", 0.972, "car",        "white", rng, "clone-A"))
    reads.append(read_row("CAM_07", t0+timedelta(seconds=240), "MH12DE1433", 0.968, "motorcycle", "black", rng, "clone-B"))
    alerts.append({"alert_id": str(uuid.uuid4()), "alert_type": "cloned_plate", "severity": 4,
                   "ts": (t0+timedelta(seconds=240)).astimezone(timezone.utc).isoformat(),
                   "plate_text": "MH12DE1433", "camera_ids": ["CAM_03","CAM_07"],
                   "evidence": {"distance_km": 10.98, "elapsed_s": 240, "implied_kmph": 164.7,
                                "class_mismatch": ["car","motorcycle"]},
                   "status": "open", "source": "seed"})

    # --- PLANT 2: overspeed. 2.145 km in 49 s => ~158 km/h vs 60 limit ---
    t1 = day.replace(hour=20, minute=41, second=3, microsecond=0)
    reads.append(read_row("CAM_01", t1,                       "MH14GT7788", 0.981, "car", "red", rng))
    reads.append(read_row("CAM_02", t1+timedelta(seconds=49), "MH14GT7788", 0.977, "car", "red", rng))
    alerts.append({"alert_id": str(uuid.uuid4()), "alert_type": "overspeed", "severity": 3,
                   "ts": (t1+timedelta(seconds=49)).astimezone(timezone.utc).isoformat(),
                   "plate_text": "MH14GT7788", "camera_ids": ["CAM_01","CAM_02"],
                   "evidence": {"distance_km": 2.145, "elapsed_s": 49,
                                "measured_kmph": 157.6, "speed_limit_kmph": 60},
                   "status": "open", "source": "seed"})

    # --- PLANT 3: loitering. 9 hits on CAM_05 in 22 min ---
    t2 = day.replace(hour=2, minute=12, second=0, microsecond=0)
    for k in range(9):
        reads.append(read_row("CAM_05", t2 + timedelta(seconds=k*int(rng.uniform(70,165))),
                              "MH12AB0451", rng.uniform(0.90, 0.98), "car", "grey", rng))
    alerts.append({"alert_id": str(uuid.uuid4()), "alert_type": "loitering", "severity": 2,
                   "ts": (t2+timedelta(minutes=22)).astimezone(timezone.utc).isoformat(),
                   "plate_text": "MH12AB0451", "camera_ids": ["CAM_05"],
                   "evidence": {"sightings": 9, "window_min": 22, "hour_of_day": 2},
                   "status": "open", "source": "seed"})

    # --- PLANT 4: watchlist hit ---
    watch.append({"plate_text": "HR26DK8337", "reason": "STOLEN VEHICLE",
                  "fir_number": "0142/2026", "added_by": "seed", "active": True, "source": "seed"})
    t3 = day.replace(hour=19, minute=14, second=37, microsecond=0)
    reads.append(read_row("CAM_04", t3, "HR26DK8337", 0.964, "car", "silver", rng))
    alerts.append({"alert_id": str(uuid.uuid4()), "alert_type": "watchlist_hit", "severity": 4,
                   "ts": t3.astimezone(timezone.utc).isoformat(), "plate_text": "HR26DK8337",
                   "camera_ids": ["CAM_04"], "evidence": {"fir_number": "0142/2026"},
                   "status": "open", "source": "seed"})

    # --- PLANT 5: behavioural anomaly (VAD) ---
    t4 = day.replace(hour=21, minute=58, second=0, microsecond=0)
    vad.append({"event_id": str(uuid.uuid4()), "camera_id": "CAM_06",
                "ts_start": t4.astimezone(timezone.utc).isoformat(),
                "ts_end": (t4+timedelta(seconds=6)).astimezone(timezone.utc).isoformat(),
                "score": 0.81, "label": "fight",
                "clip_url": "evidence/seed/CAM_06_fight_2158.mp4", "source": "seed"})

    # --- PLANT 6: low-confidence reads to populate the manual-review queue ---
    for _ in range(12):
        c = rng.choice(CAMERAS)[0]
        reads.append(read_row(c, rush_hour_time(rng, day),
                              plate(rng)[:rng.randint(6,8)], rng.uniform(0.42, 0.71),
                              rng.choice(["car","motorcycle"]), rng.choice(COLORS), rng,
                              "low-confidence"))
    return reads, alerts, watch, vad

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now(IST).date().isoformat())
    ap.add_argument("--vehicles", type=int, default=120)
    ap.add_argument("--reset", action="store_true")
    a = ap.parse_args()
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

    for t in ("alerts", "vad_events", "plate_reads", "watchlist"):
        sb.table(t).delete().eq("source", "seed").execute()   # NEVER touches source='live'
    if a.reset:
        print("seed rows removed"); return

    sb.table("cameras").upsert([
        {"camera_id": c[0], "name": c[1], "lat": c[2], "lon": c[3], "heading_deg": c[4],
         "road_name": c[5], "speed_limit_kmph": c[6], "is_active": True} for c in CAMERAS
    ]).execute()

    day = datetime.fromisoformat(a.date).replace(tzinfo=IST)
    reads, alerts, watch, vad = build(day, a.vehicles, random.Random(SEED))
    for table, rows in (("plate_reads", reads), ("alerts", alerts),
                        ("watchlist", watch), ("vad_events", vad)):
        for i in range(0, len(rows), 500):
            sb.table(table).insert(rows[i:i+500]).execute()
        print(f"{table}: {len(rows)}")

if __name__ == "__main__":
    main()
```

> **Verify:** `supabase-py` v2 method chaining (`.table(...).insert(...).execute()`) and whether your `cameras` table needs an explicit `geom` value or fills it from `lat`/`lon` via a trigger/generated column. If there is no trigger, add `"geom": f"SRID=4326;POINT({lon} {lat})"` to the upsert payload.

Companion one-liner for the rehearsal script: `make seed` → `python scripts/seed_demo_data.py --date 2026-09-05` and `make seed-reset`. Run `make seed-reset && make seed` immediately before the pitch so the timeline is anchored to the demo day.

---

## Appendix — Interface Contracts Declared by This Section

- `file: models/manifest.json (schema_version int, models{vehicle_detector,plate_detector,ocr_rec,vad_features,vad_head})`
- `file: models/yolov8n_vehicle.onnx (COCO pretrained, opset 17, static input [1,3,640,640], output (1,84,8400), classes 2=car 3=motorcycle 5=bus 7=truck)`
- `file: models/yolov8n_plate.onnx (fine-tuned 1 class, opset 17, static input [1,3,640,640], output (1,5,8400) = cx,cy,w,h,conf)`
- `dir: models/plate_ocr/rec (PaddleOCR PP-OCRv4 recognition model dir)`
- `file: models/plate_ocr/charset.txt (36 chars: 0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ, one per line)`
- `file: models/clip_vitb32_visual.onnx (VAD feature extractor, output dim 512)`
- `file: models/vad_head.onnx (input name 'feat' shape [1,512], output name 'score' shape [1,1], sigmoid)`
- `script: scripts/fetch_models.py (downloads weights from GitHub Release, verifies sha256 against models/manifest.json)`
- `script: scripts/seed_demo_data.py (flags: --date, --vehicles, --reset)`
- `script: tools/synth_plates.py (flags: --n, --font, --out; emits <out>/labels.csv with columns image_path,plate_text,synthetic)`
- `script: colab/extract_vad_features.py (emits one <video_stem>.npy of shape (32, D) float32 per video)`
- `script: ai/vad/train_head.py (trains VADHead on .npy features, exports models/vad_head.onnx)`
- `module: eval/run_eval.py (CLI: --suite {ocr,detector,vad,latency,all} --models-dir --labels --provider --out)`
- `module: eval/metrics.py (functions: normalize_plate(str)->str, coerce_to_grammar(str)->str, cer(gt,pred)->float; constant PLATE_RE)`
- `file: eval/labels/plates_holdout.csv (columns: image_path,plate_text,source,difficulty)`
- `dir: eval/labels/det_holdout/{images,labels} (YOLO txt format, class 0 = plate)`
- `file: eval/labels/vad_holdout.csv (columns: video_path,frame_start,frame_end,label)`
- `dir: eval/results/ (artifacts eval_YYYYMMDD_HHMM.json and eval_YYYYMMDD_HHMM.md)`
- `metric keys in eval JSON: plate_exact_match, plate_exact_match_coerced, cer, auto_accept_precision, coverage, precision_coverage_curve, det_map50, vad_auc_frame, e2e_latency_p50_ms, e2e_latency_p95_ms, fps_per_stream`
- `regex: PLATE_RE = ^(?:[A-Z]{2}[0-9]{1,2}[A-Z]{0,3}[0-9]{4}|[0-9]{2}BH[0-9]{4}[A-Z]{1,2})$`
- `threshold: OCR_AUTO_ACCEPT_CONF = 0.90`
- `threshold: PLATE_DET_CONF = 0.35`
- `threshold: VAD_ALERT_THRESHOLD = 0.65 with 5-of-8 consecutive-clip smoothing`
- `threshold: IMPOSSIBLE_SPEED_KMPH = 150 (cloned_plate trigger)`
- `threshold: OVERSPEED_MARGIN_KMPH = 15 (absolute, over posted speed_limit_kmph)`
- `threshold: MIN_SPEED_BASELINE_M = 500 (minimum camera-pair distance for a speed estimate)`
- `threshold: LOITER_WINDOW_MIN = 20, LOITER_MIN_SIGHTINGS = 5 (same camera)`
- `table: cameras(camera_id text pk, name text, lat double precision, lon double precision, geom geography(Point,4326), heading_deg smallint, road_name text, speed_limit_kmph smallint, is_active boolean)`
- `table: plate_reads(read_id uuid pk, camera_id text fk->cameras, track_id uuid, ts timestamptz, plate_text text null, plate_conf real, char_confidences real[], vehicle_class text, vehicle_color text, bbox int[], crop_url text, source text default 'live', note text)`
- `table: alerts(alert_id uuid pk, alert_type text, severity smallint 1-4, ts timestamptz, plate_text text, camera_ids text[], evidence jsonb, status text, source text)`
- `table: watchlist(plate_text text pk, reason text, fir_number text, added_by text, active boolean, source text)`
- `table: vad_events(event_id uuid pk, camera_id text fk->cameras, ts_start timestamptz, ts_end timestamptz, score real, label text, clip_url text, source text)`
- `enum values for alerts.alert_type: cloned_plate | overspeed | loitering | watchlist_hit | anomaly_behaviour | hit_and_run | unreadable_plate`
- `column convention: every table carries source text IN ('live','seed'); seed_demo_data.py --reset deletes only source='seed'`
- `storage bucket: evidence (paths evidence/seed/<plate>_<epoch>.jpg and evidence/seed/<camera>_<label>_<hhmm>.mp4)`
- `env: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY (seed script), ANPR_MODELS_DIR (default ./models), ANPR_ORT_PROVIDER (default DmlExecutionProvider)`
- `demo camera ids: CAM_01..CAM_08 with fixed Pune coordinates; CAM_01->CAM_02 = 2.145 km, CAM_03->CAM_07 = 10.982 km`
- `planted demo plates: MH12DE1433 (clone), MH14GT7788 (overspeed), MH12AB0451 (loiter), HR26DK8337 (watchlist/STOLEN, FIR 0142/2026)`
- `random seed constant: SEED = 1337 (seed_demo_data.py and tools/synth_plates.py)`
- `gitignored data root: data/ (raw datasets, synthetic crops, downloaded corpora never committed)`

## Appendix — MVP vs Stretch

- MVP: Stage-0 dataset only (<500 MB): 1 traffic clip + ~300 plate images + a community pretrained plate YOLOv8 weight exported to ONNX, closing the RTSP->detect->OCR->Supabase->map loop by hour 3 with zero training.
- MVP: One Colab fine-tune of yolov8n on a 5-10k-image Roboflow Indian plate dataset (60 epochs, ~60-75 min on a free T4), exported to models/yolov8n_plate.onnx with opset 17, static shapes, nms=False.
- MVP: models/manifest.json + scripts/fetch_models.py so weights are never committed to git and every teammate gets byte-identical models.
- MVP: PaddleOCR used zero-shot with a 36-character whitelist plus RTO-grammar coercion (coerce_to_grammar). No OCR training at all.
- MVP: eval/ harness built by hour 12 with a >=300-row hand-verified plates_holdout.csv, reporting plate_exact_match, cer, auto_accept_precision, coverage and det_map50.
- MVP: scripts/seed_demo_data.py with all six planted scenarios, deterministic at SEED=1337, every row tagged source='seed', and --reset that deletes only seeded rows.
- MVP: latency + FPS measured on the actual RX 6500M with DmlExecutionProvider and stated with the hardware line in the deck table.
- STRETCH: VAD end to end - CLIP/VideoMAE feature extraction on Colab, MIL head trained locally on CPU, vad_head.onnx, frame-level AUC on a held-out split.
- STRETCH: tools/synth_plates.py synthetic Indian plate generation (8k crops) used as <=30% of an OCR fine-tune mix, plus the precision/coverage sweep chart.
- STRETCH: PaddleOCR PP-OCRv4 recognition fine-tune on Colab (budget 30 min just for the PaddlePaddle GPU install before any training starts).
- STRETCH: CCPD subset added to the detector training mix for tilt/blur/night robustness; ShanghaiTech features for a second published AUC number.
- STRETCH: tracking metrics (MOTA/IDF1 via motmetrics) on one hand-annotated clip.
- STRETCH: INT8 quantisation of the plate detector via onnxruntime.quantization and an fp16 variant, benchmarked against fp32 on DirectML.

## Appendix — Risks

- Bandwidth sink: a teammate starts a 120 GB UCF-Crime video download on venue Wi-Fi and saturates the link for hours. Mitigation: one named download owner, sizes posted before starting, and a hard rule that only precomputed I3D/CLIP feature archives (1-3 GB) are ever pulled for VAD.
- Colab preemption mid-training loses all progress. Mitigation: save_period=5 writing last.pt to mounted Drive, resume=True restart path documented, and the fallback that the Stage-0 community pretrained plate weight already works so the fine-tune is an upgrade, not a dependency.
- ONNX export mismatch: dynamic axes or nms=True produce a graph that is 2-3x slower or unsupported on DmlExecutionProvider. Mitigation: export with dynamic=False, half=False, nms=False, opset 17, and run eval/run_eval.py --suite latency immediately after every export before trusting the file.
- Held-out labels are wrong: Kaggle Indian plate sets encode ground truth in filenames that are frequently mistyped, so exact-match is measured against garbage. Mitigation: one person hand-verifies 300 rows into eval/labels/plates_holdout.csv; that file is the only accuracy source of truth.
- Synthetic overfit: mixing too much rendered data teaches the recogniser the renderer. Mitigation: cap synthetic at 30% of any training mix and always report metrics on the real hand-verified split only.
- Grammar coercion silently corrupts legitimate plates (older no-series formats, BH series). Mitigation: run_eval reports exact match with AND without coercion; if coerced is not strictly better on the held-out set, ship it disabled.
- VAD AUC is overclaimed in the deck. Mitigation: state the protocol (frame-level ROC-AUC, UCF-Crime split) and cite the realistic 0.70-0.82 band; do not quote paper numbers as if they were measured here.
- Seed data mistaken for live output during judging, or --reset wiping live rows. Mitigation: mandatory source column on every table, a SEEDED chip in the UI, an explicit verbal disclosure in the demo script, and a delete filtered strictly on source='seed'.
- Seeded thresholds drift from the rules engine (e.g. rules use 130 km/h while the seed plants 158 km/h at a 60 limit and the clone at 165 km/h). Mitigation: the four threshold constants are contracts in this section and must be imported from one shared config module, not re-typed per service.
- PaddlePaddle GPU install on Colab burns 30+ minutes and can fail outright. Mitigation: the OCR fine-tune is explicitly stretch; the whitelist + grammar path requires no PaddlePaddle training stack at all.
- Variable-frame-rate source clips break RTSP replay timing, which silently corrupts every cross-camera speed calculation. Mitigation: re-encode all demo clips to 720p H.264 constant 25 fps before looping, and assert frame timing in the latency suite.

## Appendix — Open Questions

- Which Roboflow Universe plate project is the team actually going to use? The licence (CC BY 4.0 vs CC BY-NC) must be recorded in models/manifest.json under trained_on before the deck claims anything about reusability.
- Does the cameras table populate geom from lat/lon via a generated column or trigger, or must the seed script send an EWKT string? This changes the upsert payload in scripts/seed_demo_data.py.
- Is the shared threshold config a Python module (ai/config.py), a Supabase table, or environment variables? All four alert thresholds must come from exactly one place shared by the edge worker, the FastAPI rules engine and the seed script.
- Which VAD backbone wins on this hardware: CLIP ViT-B/32 (512-d, ~10-15 ms/frame on DML, cheap, weaker temporal modelling) or VideoMAE-base (768-d, clip-level, stronger but ~150-300 ms per 16-frame clip)? Decide by benchmarking both in the latency suite before committing feature extraction GPU time.
- Is a redistributable Charles-Wright-alike TTF available for assets/fonts/, or does the synthetic generator ship with DejaVuSans-Bold and a note about glyph fidelity?
- Does the UI's manual-review queue read low-confidence rows from plate_reads directly (filtering plate_conf < OCR_AUTO_ACCEPT_CONF) or from a separate review_queue table? The seed script currently assumes the former.
- What is the ground-truth source for e2e latency measurement - does the edge worker stamp a capture_ts on each frame that survives into plate_reads, or is latency measured only from decode? Without a capture timestamp the p95 number is not defensible.
