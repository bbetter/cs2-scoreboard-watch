"""Scan a CS2 TAB-scoreboard screenshot and print a per-player anomaly score.

The score is a statistical outlier index relative to the lobby (HS% and K/D
z-scores). It is NOT evidence of cheating or smurfing.
"""
import math
import re
import statistics
import sys
from dataclasses import dataclass, field
from typing import Any, Optional

from rapidocr_onnxruntime import RapidOCR

# Reference layout measured on a 1147px-wide sample screenshot (legacy fallback).
REF_WIDTH = 1147
REF_HEIGHT = 768
COLUMN_X = {
    "kills": 680,
    "deaths": 730,
    "kd": 768,
    "hs": 836,
    "zev": 896,
    "nizh": 946,
    "level": 995,
}
NICK_X_MIN = 370
NUMERIC_X_MIN = 660
ROW_TOL = 8
TABLE_BOX = (250, 380, 1040, 660)
SCALE = 2

# Offsets relative to the K/D column on a 1080p-height CS2 scoreboard.
KD_REL_COLS_1080 = {
    "kills": -85.0,
    "deaths": -37.0,
    "kd": 0.0,
    "hs": 66.0,
    "zev": 129.0,
    "nizh": 180.0,
    "level": 225.0,
}

_ENGINE: Optional[RapidOCR] = None


def get_engine() -> RapidOCR:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = RapidOCR()
    return _ENGINE


@dataclass
class Player:
    nick: str
    kills: int
    deaths: int
    kd: float
    hs: float
    level: int
    score: float = 0.0
    avatar_thumb: Any = field(default=None, repr=False)
    avatar_data_url: Optional[str] = field(default=None, repr=False)


def _num(text: str):
    # OCR sometimes glues neighbouring cells ("17 1512"): take the first number.
    m = re.search(r"-?\d+(?:[.,]\d+)?", text)
    return float(m.group().replace(",", ".")) if m else None


def _cluster_rows(ocr_items, row_tol: float):
    rows = []
    for x, y, text, conf in sorted(ocr_items, key=lambda i: i[1]):
        for row in rows:
            if abs(row["y"] - y) <= row_tol:
                row["cells"].append((x, text, conf))
                break
        else:
            rows.append({"y": y, "cells": [(x, text, conf)]})
    return rows


def _detect_kd_x(ocr_items) -> Optional[float]:
    kd_xs = [
        x
        for x, _, text, _ in ocr_items
        if re.fullmatch(r"\d+[.,]\d{2}", text.strip())
    ]
    if len(kd_xs) >= 2:
        return statistics.median(kd_xs)
    return None


def parse(ocr_items, img_width: int = REF_WIDTH, img_height: int = REF_HEIGHT, img=None):
    if img_width == REF_WIDTH:
        row_tol = float(ROW_TOL)
        nick_x_min = float(NICK_X_MIN)
        numeric_x_min = float(NUMERIC_X_MIN)
        col_x = dict(COLUMN_X)
        max_col_dist = 30.0
        kd_x = None
        s = 1.0
    else:
        s = img_height / 1080.0
        row_tol = max(7.0, 11.0 * s)
        kd_x = _detect_kd_x(ocr_items)
        if kd_x is None:
            kd_x = img_width * 0.5 + 135.0 * s
        col_x = {k: kd_x + dx * s for k, dx in KD_REL_COLS_1080.items()}
        nick_x_min = kd_x - 420.0 * s
        numeric_x_min = kd_x - 105.0 * s
        max_col_dist = 24.0 * s

    rows = _cluster_rows(ocr_items, row_tol)

    players = []
    for row in rows:
        cells = sorted(row["cells"])
        nick = " ".join(
            t for x, t, _ in cells if nick_x_min <= x < numeric_x_min
        ).strip()
        nick = re.sub(r"^[？?•·|]+\s*", "", nick).strip()
        if not nick or nick.startswith("[MOUSE"):
            continue
        nums = {}
        for x, t, _ in cells:
            if x < numeric_x_min:
                continue
            col = min(col_x, key=lambda k: abs(col_x[k] - x))
            v = _num(t)
            if v is not None and abs(col_x[col] - x) < max_col_dist:
                nums[col] = v

        if "kd" not in nums:
            continue
        if "kills" not in nums and "deaths" in nums:
            nums["kills"] = round(nums["deaths"] * nums["kd"])
        if "deaths" not in nums and "kills" in nums and nums["kd"] > 0:
            nums["deaths"] = round(nums["kills"] / nums["kd"])
        if "kills" not in nums or "deaths" not in nums:
            continue

        avatar_thumb = None
        avatar_data_url = None
        if img is not None and kd_x is not None:
            import base64
            import io
            import numpy as np
            from PIL import Image

            ax0 = max(0, int(kd_x - 415.0 * s))
            ax1 = min(img_width, int(kd_x - 393.0 * s))
            ay0 = max(0, int(row["y"] - 3.0 * s))
            ay1 = min(img_height, int(row["y"] + 19.0 * s))
            if ax1 - ax0 >= 8 and ay1 - ay0 >= 8:
                resample = Image.Resampling.BILINEAR if hasattr(Image, "Resampling") else 2
                av_crop = img.crop((ax0, ay0, ax1, ay1))
                av = av_crop.resize((16, 16), resample)
                avatar_thumb = np.asarray(av, dtype=np.float32) / 255.0
                buf = io.BytesIO()
                av_crop.resize((32, 32), Image.Resampling.NEAREST if hasattr(Image, "Resampling") else 0).save(buf, format="PNG")
                avatar_data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")

        players.append(
            Player(
                nick=nick,
                kills=int(nums["kills"]),
                deaths=int(nums["deaths"]),
                kd=float(nums["kd"]),
                hs=float(nums.get("hs", 0.0)),
                level=int(nums.get("level", -1)),
                avatar_thumb=avatar_thumb,
                avatar_data_url=avatar_data_url,
            )
        )
    return players


def score(players):
    if len(players) < 3:
        for p in players:
            p.score = float("nan")
        return players
    hs = [p.hs for p in players]
    kd = [p.kd for p in players]
    hs_mu, hs_sd = statistics.mean(hs), statistics.pstdev(hs) or 1.0
    kd_mu, kd_sd = statistics.mean(kd), statistics.pstdev(kd) or 1.0
    for p in players:
        z = 0.8 * (p.hs - hs_mu) / hs_sd + 0.8 * (p.kd - kd_mu) / kd_sd
        p.score = 100 / (1 + math.exp(-(z - 1.5)))
    return players


def _run_ocr_on_crop(img, box, scale: int):
    from PIL import Image

    engine = get_engine()
    x0, y0, x1, y1 = box
    crop = img.crop((x0, y0, x1, y1))
    if scale > 1:
        resample = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else 1
        crop = crop.resize((crop.width * scale, crop.height * scale), resample)
    result, _ = engine(crop)
    items = []
    for bbox, text, conf in result or []:
        x = min(pt[0] for pt in bbox) / scale + x0
        y = min(pt[1] for pt in bbox) / scale + y0
        items.append((x, y, text, conf))
    return items


def scan(path: str):
    from PIL import Image

    img = Image.open(path).convert("RGB")
    w, h = img.size

    if w == REF_WIDTH:
        box = TABLE_BOX
    else:
        box = (
            max(0, int(w * 0.5 - 0.36 * h)),
            max(0, int(h * 0.36)),
            min(w, int(w * 0.5 + 0.40 * h)),
            min(h, int(h * 0.78)),
        )

    items = _run_ocr_on_crop(img, box, SCALE)
    players = parse(items, img_width=w, img_height=h, img=img)
    return score(players)


if __name__ == "__main__":
    players = scan(sys.argv[1])
    print(f"{'nick':24} {'K':>3} {'D':>3} {'K/D':>5} {'HS%':>5} {'lvl':>4}  anomaly")
    for p in sorted(players, key=lambda p: -p.score if p.score == p.score else 0):
        lvl = str(p.level) if p.level >= 0 else "?"
        s = "n/a" if p.score != p.score else f"{p.score:5.1f}%"
        print(f"{p.nick:24} {p.kills:3d} {p.deaths:3d} {p.kd:5.2f} {p.hs:5.0f} {lvl:>4}  {s}")
