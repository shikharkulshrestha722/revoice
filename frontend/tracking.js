// ============================================================
// ReVoice — Tracking helpers
// ============================================================
// This file has no camera or UI code. It only does the math:
//   1. OneEuroFilter   -> smooths a shaky pointer
//   2. PointerTracker  -> smoothing + ignores brief tracking dropouts
//   3. detectLaserBlob -> finds a laser dot in an image
//   4. DwellSelector   -> decides when a cell has really been "selected"
// ============================================================


// ---------- 1. ONE EURO FILTER ----------
// A standard filter for pointers/cursors.
// When your hand is still, it smooths a lot (no jitter).
// When your hand moves fast, it smooths less (no lag).
class LowPass {
  constructor() { this.y = null; }
  filter(x, alpha) {
    this.y = this.y === null ? x : alpha * x + (1 - alpha) * this.y;
    return this.y;
  }
  reset() { this.y = null; }
}

export class OneEuroFilter {
  // minCutoff: lower = smoother when still (but slightly more lag)
  // beta:      higher = more responsive when moving fast
  constructor(minCutoff = 1.0, beta = 0.01, dCutoff = 1.0) {
    this.minCutoff = minCutoff;
    this.beta = beta;
    this.dCutoff = dCutoff;
    this.xFilter = new LowPass();
    this.dxFilter = new LowPass();
    this.lastTime = null;
  }

  alpha(cutoff, dt) {
    const tau = 1 / (2 * Math.PI * cutoff);
    return 1 / (1 + tau / dt);
  }

  filter(x, timeMs) {
    if (this.lastTime === null) {
      this.lastTime = timeMs;
      this.dxFilter.filter(0, 1);
      return this.xFilter.filter(x, 1);
    }
    const dt = Math.max((timeMs - this.lastTime) / 1000, 1e-3);
    this.lastTime = timeMs;

    const prev = this.xFilter.y;
    const dx = (x - prev) / dt;
    const smoothDx = this.dxFilter.filter(dx, this.alpha(this.dCutoff, dt));
    const cutoff = this.minCutoff + this.beta * Math.abs(smoothDx);
    return this.xFilter.filter(x, this.alpha(cutoff, dt));
  }

  reset() {
    this.xFilter.reset();
    this.dxFilter.reset();
    this.lastTime = null;
  }
}


// ---------- 2. POINTER TRACKER ----------
// Smooths x and y separately, and keeps the last position for a few
// frames if tracking briefly drops out (so the pointer doesn't flicker).
export class PointerTracker {
  constructor({ minCutoff = 0.8, beta = 0.015, maxLostFrames = 10 } = {}) {
    this.fx = new OneEuroFilter(minCutoff, beta);
    this.fy = new OneEuroFilter(minCutoff, beta);
    this.maxLostFrames = maxLostFrames;
    this.lostFrames = 0;
    this.point = null;
  }

  update(rawPoint, timeMs) {
    if (rawPoint) {
      this.lostFrames = 0;
      this.point = {
        x: this.fx.filter(rawPoint.x, timeMs),
        y: this.fy.filter(rawPoint.y, timeMs),
      };
    } else {
      this.lostFrames++;
      if (this.lostFrames > this.maxLostFrames) this.reset();
    }
    return this.point;
  }

  reset() {
    this.fx.reset();
    this.fy.reset();
    this.point = null;
    this.lostFrames = 0;
  }
}


// ---------- 3. LASER DOT DETECTION ----------
// Instead of trusting one single pixel, this:
//   a) finds every separate patch of bright red pixels
//   b) throws away patches that are too tiny (noise) or too big (a red shirt,
//      a red wall) — a laser dot is always a SMALL patch
//   c) picks the brightest remaining patch and returns its centre
export function detectLaserBlob(data, width, height, opts = {}) {
  const {
    minBrightness = 220,    // red channel must be at least this bright
    minRedDominance = 60,   // red must beat green/blue by this much
    minPixels = 2,          // smaller patches are treated as noise
    maxPixels = 120,        // bigger patches are treated as "not a laser dot"
  } = opts;

  const total = width * height;
  const redScore = new Int16Array(total);   // 0 = not red
  for (let p = 0; p < total; p++) {
    const i = p * 4;
    const r = data[i], g = data[i + 1], b = data[i + 2];
    const dom = r - Math.max(g, b);
    if (r >= minBrightness && dom >= minRedDominance) redScore[p] = dom;
  }

  const visited = new Uint8Array(total);
  const stack = [];
  let best = null;

  for (let start = 0; start < total; start++) {
    if (!redScore[start] || visited[start]) continue;

    // flood-fill this patch
    let count = 0, sumX = 0, sumY = 0, sumW = 0, peak = 0;
    stack.length = 0;
    stack.push(start);
    visited[start] = 1;

    while (stack.length) {
      const p = stack.pop();
      const x = p % width, y = (p / width) | 0;
      const w = redScore[p];
      count++; sumX += x * w; sumY += y * w; sumW += w;
      if (w > peak) peak = w;

      for (let dy = -1; dy <= 1; dy++) {
        const ny = y + dy;
        if (ny < 0 || ny >= height) continue;
        for (let dx = -1; dx <= 1; dx++) {
          const nx = x + dx;
          if (nx < 0 || nx >= width) continue;
          const q = ny * width + nx;
          if (redScore[q] && !visited[q]) { visited[q] = 1; stack.push(q); }
        }
      }
    }

    if (count < minPixels || count > maxPixels) continue;   // noise or big red object
    if (!best || peak > best.peak) {
      best = { x: sumX / sumW, y: sumY / sumW, count, peak };
    }
  }

  return best ? { x: best.x, y: best.y, count: best.count } : null;
}


// ---------- 4. DWELL SELECTOR ----------
// Decides when a cell is really selected:
//   - the pointer must be in a NEW cell for a few frames before we switch
//     (so shaking on a border doesn't flip between two letters)
//   - then it must stay there for dwellMs to select
//   - to type the same letter again, you wait a little longer
export class DwellSelector {
  constructor({ dwellMs = 1500, switchFrames = 4, repeatExtraMs = 800 } = {}) {
    this.dwellMs = dwellMs;
    this.switchFrames = switchFrames;
    this.repeatExtraMs = repeatExtraMs;
    this.reset();
  }

  reset() {
    this.currentCell = -1;
    this.pendingCell = null;
    this.pendingCount = 0;
    this.dwellStart = 0;
    this.required = this.dwellMs;
  }

  // cell = the cell under the pointer this frame (-1 if no pointer)
  update(cell, timeMs) {
    let switching = false;

    if (cell === this.currentCell) {
      this.pendingCell = null;
      this.pendingCount = 0;
    } else {
      if (cell === this.pendingCell) this.pendingCount++;
      else { this.pendingCell = cell; this.pendingCount = 1; }

      if (this.pendingCount >= this.switchFrames) {
        this.currentCell = cell;
        this.dwellStart = timeMs;
        this.required = this.dwellMs;
        this.pendingCell = null;
        this.pendingCount = 0;
      } else {
        switching = true;
      }
    }

    let progress = 0;
    let selected = null;
    if (this.currentCell >= 0) {
      const elapsed = timeMs - this.dwellStart;
      progress = Math.min(elapsed / this.required, 1);
      if (!switching && elapsed >= this.required) {
        selected = this.currentCell;
        this.dwellStart = timeMs;
        this.required = this.dwellMs + this.repeatExtraMs;
        progress = 0;
      }
    }

    return { cell: this.currentCell, progress, selected };
  }
}