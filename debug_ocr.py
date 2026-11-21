"""Run YOLO + EasyOCR on manually extracted frames to find bib numbers."""
import sys, os, glob
sys.path.insert(0, ".")

import cv2
import numpy as np
import easyocr
import re
from pathlib import Path

IMAGES = sorted(glob.glob("videos/vlcsnap*.png") + glob.glob("videos/vlcsnap*.jpg"))
if not IMAGES:
    print("No vlcsnap images found in videos/")
    sys.exit(1)

print(f"Found {len(IMAGES)} images: {[os.path.basename(p) for p in IMAGES]}")

reader = easyocr.Reader(["en"], gpu=False, verbose=False)

def normalize_ocr(text):
    """Substitute common OCR errors in digit strings."""
    subs = {"S": "5", "s": "5", "O": "0", "o": "0", "I": "1", "l": "1",
            "Z": "2", "B": "8", "G": "6", "g": "9", "q": "9"}
    return "".join(subs.get(c, c) for c in text)

def is_bib(text):
    normalized = normalize_ocr(text)
    d = re.sub(r"[^0-9]", "", normalized)
    return d if 1 <= len(d) <= 4 else None

def preprocess(crop):
    """Multiple preprocessing variants for OCR."""
    variants = {}
    # Original resized
    h, w = crop.shape[:2]
    scale = max(1, 200 // min(h, w))
    big = cv2.resize(crop, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
    variants["orig"] = big
    # Grayscale + CLAHE
    gray = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(4, 4))
    enhanced = clahe.apply(gray)
    variants["clahe"] = cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)
    # Threshold
    _, thresh = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    variants["thresh"] = cv2.cvtColor(thresh, cv2.COLOR_GRAY2BGR)
    return variants

def ocr_image(img, label=""):
    """Try OCR on image with multiple preprocessing passes."""
    results = []
    variants = preprocess(img)
    seen = set()
    for vname, vimg in variants.items():
        for (_, text, conf) in reader.readtext(vimg):
            bib = is_bib(text)
            key = text.strip()
            if key not in seen:
                seen.add(key)
                results.append((bib, text, conf, vname))
    return results


try:
    from ultralytics import YOLO
    model = YOLO("yolov8n.pt")
    use_yolo = True
except Exception:
    use_yolo = False
    print("YOLO not available, running OCR on full image only")

OUT_DIR = "data/outputs/debug_vlcsnap"
os.makedirs(OUT_DIR, exist_ok=True)

for img_path in IMAGES:
    img = cv2.imread(img_path)
    if img is None:
        print(f"Could not read {img_path}")
        continue

    h, w = img.shape[:2]
    fname = Path(img_path).stem
    print(f"\n{'='*60}")
    print(f"Image: {fname}  ({w}x{h})")

    crops_to_try = []

    if use_yolo:
        results = model(img, conf=0.25, classes=[0, 1], verbose=False)  # person + bicycle
        boxes = results[0].boxes if results and results[0].boxes is not None else []
        persons = []
        for box in boxes:
            x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
            cls = int(box.cls[0])
            conf = float(box.conf[0])
            bw, bh = x2 - x1, y2 - y1
            if cls == 0 and bh > 40:  # person
                persons.append((x1, y1, x2, y2, conf))

        print(f"  YOLO: {len(persons)} persons detected")

        for i, (x1, y1, x2, y2, det_conf) in enumerate(persons):
            bw, bh = x2 - x1, y2 - y1

            # Bib is typically on the front chest/torso
            # Try chest: top 20-55% of bbox
            chest_y1 = int(y1 + bh * 0.10)
            chest_y2 = int(y1 + bh * 0.55)
            chest_x1 = int(x1 + bw * 0.05)
            chest_x2 = int(x2 - bw * 0.05)

            # Full torso: top 60%
            torso_x1, torso_y1 = int(x1), int(y1)
            torso_x2, torso_y2 = int(x2), int(y1 + bh * 0.65)

            # Lower torso / belly: 40-75% height
            belly_y1 = int(y1 + bh * 0.35)
            belly_y2 = int(y1 + bh * 0.75)

            crops_to_try.extend([
                (f"p{i}_chest", img[chest_y1:chest_y2, chest_x1:chest_x2]),
                (f"p{i}_torso", img[torso_y1:torso_y2, torso_x1:torso_x2]),
                (f"p{i}_belly", img[belly_y1:belly_y2, int(x1):int(x2)]),
                (f"p{i}_full",  img[int(y1):int(y2), int(x1):int(x2)]),
            ])

            # Annotate detection on image copy
            cv2.rectangle(img, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 0), 2)
            cv2.rectangle(img, (chest_x1, chest_y1), (chest_x2, chest_y2), (0, 0, 255), 2)
    else:
        # No YOLO: try upper, middle, lower bands
        crops_to_try = [
            ("full", img),
            ("top_third", img[:h // 3, :]),
            ("mid_third", img[h // 3: 2 * h // 3, :]),
            ("bot_third", img[2 * h // 3:, :]),
        ]

    # Run OCR on every crop
    found_bibs = []
    for crop_label, crop in crops_to_try:
        if crop is None or crop.size == 0:
            continue
        if crop.shape[0] < 10 or crop.shape[1] < 10:
            continue
        # Save crop for visual inspection
        crop_out = os.path.join(OUT_DIR, f"{fname}_{crop_label}.jpg")
        cv2.imwrite(crop_out, crop)
        hits = ocr_image(crop, crop_label)
        for bib, text, conf, variant in hits:
            marker = "*** BIB ***" if bib else ""
            print(f"  {crop_label}/{variant}: '{text}' conf={conf:.2f} → bib={bib} {marker}")
            if bib:
                found_bibs.append((bib, conf, crop_label))

    if found_bibs:
        print(f"\n  >>> BIBS FOUND: {found_bibs}")
    else:
        print(f"  >>> No bib numbers detected")

    # Save annotated image
    out_path = os.path.join(OUT_DIR, f"{fname}_annotated.jpg")
    cv2.imwrite(out_path, img)

print(f"\nAnnotated images saved to {OUT_DIR}/")
