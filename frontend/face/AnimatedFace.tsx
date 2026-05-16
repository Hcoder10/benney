import { useEffect, useRef } from "react";
import { emotionLoop, type Emotion } from "./data";

type FaceShape = {
  eyeOpen: number;
  eyeSmile: number;
  pupilX: number;
  pupilY: number;
  browLift: number;
  browTilt: number;
  browPinch: number;
  mouthSmile: number;
  mouthOpen: number;
  mouthWidth: number;
  blush: number;
};

const shapes: Record<Emotion, FaceShape> = {
  idle: {
    eyeOpen: 1,
    eyeSmile: 0,
    pupilX: 0,
    pupilY: 0,
    browLift: 0,
    browTilt: 0,
    browPinch: 0,
    mouthSmile: 0.66,
    mouthOpen: 0,
    mouthWidth: 1,
    blush: 0.9,
  },
  listening: {
    eyeOpen: 1.08,
    eyeSmile: 0,
    pupilX: 0,
    pupilY: 0.05,
    browLift: 0.14,
    browTilt: 0.04,
    browPinch: 0,
    mouthSmile: 0.18,
    mouthOpen: 0.22,
    mouthWidth: 0.72,
    blush: 1,
  },
  thinking: {
    eyeOpen: 0.92,
    eyeSmile: 0,
    pupilX: 0.18,
    pupilY: -0.16,
    browLift: 0.04,
    browTilt: 0.34,
    browPinch: 0.12,
    mouthSmile: -0.08,
    mouthOpen: 0.04,
    mouthWidth: 0.62,
    blush: 0.72,
  },
  speaking: {
    eyeOpen: 1,
    eyeSmile: 0,
    pupilX: 0,
    pupilY: 0,
    browLift: 0.08,
    browTilt: 0.02,
    browPinch: 0,
    mouthSmile: 0.25,
    mouthOpen: 0.74,
    mouthWidth: 0.86,
    blush: 0.94,
  },
  happy: {
    eyeOpen: 0.48,
    eyeSmile: 0.9,
    pupilX: 0,
    pupilY: 0.08,
    browLift: 0.22,
    browTilt: 0,
    browPinch: 0,
    mouthSmile: 1,
    mouthOpen: 0.62,
    mouthWidth: 1.08,
    blush: 1.15,
  },
  concerned: {
    eyeOpen: 0.86,
    eyeSmile: 0,
    pupilX: 0,
    pupilY: 0.12,
    browLift: -0.08,
    browTilt: -0.3,
    browPinch: 0.34,
    mouthSmile: -0.72,
    mouthOpen: 0.08,
    mouthWidth: 0.72,
    blush: 0.68,
  },
  curious: {
    eyeOpen: 1.12,
    eyeSmile: 0,
    pupilX: 0.12,
    pupilY: -0.08,
    browLift: 0.24,
    browTilt: 0.42,
    browPinch: -0.12,
    mouthSmile: 0.44,
    mouthOpen: 0.12,
    mouthWidth: 0.74,
    blush: 0.9,
  },
  sleeping: {
    eyeOpen: 0.12,
    eyeSmile: 1,
    pupilX: 0,
    pupilY: 0,
    browLift: -0.02,
    browTilt: 0,
    browPinch: 0,
    mouthSmile: 0.16,
    mouthOpen: 0.14,
    mouthWidth: 0.52,
    blush: 0.68,
  },
};

const loop: Emotion[] = [...emotionLoop, "sleeping"];

function lerp(from: number, to: number, amount: number) {
  return from + (to - from) * amount;
}

function lerpShape(current: FaceShape, target: FaceShape, amount: number) {
  for (const key of Object.keys(current) as (keyof FaceShape)[]) {
    current[key] = lerp(current[key], target[key], amount);
  }
}

function drawGlow(ctx: CanvasRenderingContext2D, x: number, y: number, radius: number) {
  const glow = ctx.createRadialGradient(x, y, radius * 0.08, x, y, radius);
  glow.addColorStop(0, "rgba(255, 232, 190, 0.16)");
  glow.addColorStop(0.5, "rgba(255, 116, 104, 0.035)");
  glow.addColorStop(1, "rgba(255, 116, 104, 0)");
  ctx.fillStyle = glow;
  ctx.beginPath();
  ctx.arc(x, y, radius, 0, Math.PI * 2);
  ctx.fill();
}

function strokeArc(
  ctx: CanvasRenderingContext2D,
  x1: number,
  y1: number,
  x2: number,
  y2: number,
  cx: number,
  cy: number,
  width: number,
  color = "#fff0cf",
) {
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.lineCap = "round";
  ctx.shadowColor = "rgba(255, 232, 190, 0.72)";
  ctx.shadowBlur = width * 1.5;
  ctx.beginPath();
  ctx.moveTo(x1, y1);
  ctx.quadraticCurveTo(cx, cy, x2, y2);
  ctx.stroke();
  ctx.restore();
}

function drawEye(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  side: -1 | 1,
  scale: number,
  shape: FaceShape,
  blink: number,
  breath: number,
) {
  const eyeOpen = Math.max(0.08, shape.eyeOpen * blink);
  const rx = 62 * scale * (1 + breath * 0.012);
  const ry = 62 * scale * (0.72 + eyeOpen * 0.28);
  const smile = shape.eyeSmile;

  if (smile > 0.62 || eyeOpen < 0.2) {
    strokeArc(
      ctx,
      x - 43 * scale,
      y + 4 * scale,
      x + 43 * scale,
      y + 4 * scale,
      x,
      y - 48 * scale,
      13 * scale,
    );
    return;
  }

  ctx.save();
  ctx.shadowColor = "rgba(255, 232, 190, 0.8)";
  ctx.shadowBlur = 18 * scale;
  ctx.fillStyle = "#fff0cf";
  ctx.beginPath();
  ctx.ellipse(x, y, rx, ry, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();

  const px = x + (shape.pupilX * 30 * side + Math.sin(breath * 0.8) * 2) * scale;
  const py = y + shape.pupilY * 34 * scale;

  const iris = ctx.createRadialGradient(
    px - 11 * scale,
    py - 16 * scale,
    6 * scale,
    px,
    py,
    42 * scale,
  );
  iris.addColorStop(0, "#b7783d");
  iris.addColorStop(0.48, "#603611");
  iris.addColorStop(1, "#17100b");
  ctx.fillStyle = iris;
  ctx.beginPath();
  ctx.ellipse(px, py, 35 * scale, 45 * scale, 0, 0, Math.PI * 2);
  ctx.fill();

  ctx.fillStyle = "rgba(255, 255, 246, 0.95)";
  ctx.beginPath();
  ctx.ellipse(px - 16 * scale, py - 19 * scale, 13 * scale, 20 * scale, 0.38, 0, Math.PI * 2);
  ctx.fill();

  ctx.fillStyle = "rgba(123, 235, 210, 0.76)";
  ctx.beginPath();
  ctx.ellipse(px - 4 * scale, py + 24 * scale, 7 * scale, 4 * scale, -0.28, 0, Math.PI * 2);
  ctx.fill();
}

function drawBrow(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  side: -1 | 1,
  scale: number,
  shape: FaceShape,
  breath: number,
) {
  const lift = shape.browLift * 46 * scale + Math.sin(breath) * 2 * scale;
  const tilt = shape.browTilt * side * 36 * scale;
  const pinch = shape.browPinch * side * 18 * scale;
  strokeArc(
    ctx,
    x - 48 * scale,
    y + tilt - lift + pinch,
    x + 48 * scale,
    y - tilt - lift - pinch,
    x,
    y - 22 * scale - lift,
    15 * scale,
  );
}

function drawMouth(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  scale: number,
  shape: FaceShape,
  now: number,
) {
  const width = 64 * scale * shape.mouthWidth;
  const smile = shape.mouthSmile;
  const speaking = shape.mouthOpen > 0.5;
  const open = (shape.mouthOpen + (speaking ? Math.max(0, Math.sin(now * 0.019)) * 0.38 : 0)) * 42 * scale;

  if (open > 18 * scale) {
    ctx.save();
    ctx.shadowColor = "rgba(255, 232, 190, 0.7)";
    ctx.shadowBlur = 13 * scale;
    ctx.fillStyle = "#1a0f0a";
    ctx.strokeStyle = "#fff0cf";
    ctx.lineWidth = 11 * scale;
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.ellipse(x, y + open * 0.17, width * 0.54, open * 0.5, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = "rgba(133, 231, 211, 0.78)";
    ctx.beginPath();
    ctx.ellipse(x, y + open * 0.42, width * 0.36, open * 0.14, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
    return;
  }

  const curve = y + 44 * scale * smile;
  strokeArc(
    ctx,
    x - width,
    y,
    x + width,
    y,
    x,
    curve,
    12 * scale,
  );
}

function drawBlush(ctx: CanvasRenderingContext2D, x: number, y: number, scale: number, alpha: number) {
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.shadowColor = "rgba(255, 116, 104, 0.78)";
  ctx.shadowBlur = 22 * scale;
  ctx.fillStyle = "#ff8f78";
  ctx.beginPath();
  ctx.ellipse(x, y, 31 * scale, 17 * scale, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

export function AnimatedFace() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;

    const ctx = canvas.getContext("2d", { alpha: true });
    if (!ctx) return undefined;

    let raf = 0;
    let last = performance.now();
    let emotionIndex = 0;
    let lastEmotionChange = performance.now();
    const current: FaceShape = { ...shapes.idle };
    let target: FaceShape = shapes.idle;

    const render = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;

      if (now - lastEmotionChange > 2300) {
        emotionIndex = (emotionIndex + 1) % loop.length;
        target = shapes[loop[emotionIndex] ?? "idle"];
        lastEmotionChange = now;
      }

      const rect = canvas.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2.5);
      const nextWidth = Math.max(1, Math.round(rect.width * dpr));
      const nextHeight = Math.max(1, Math.round(rect.height * dpr));
      if (canvas.width !== nextWidth || canvas.height !== nextHeight) {
        canvas.width = nextWidth;
        canvas.height = nextHeight;
      }

      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const w = rect.width;
      const h = rect.height;
      ctx.clearRect(0, 0, w, h);

      const amount = 1 - Math.exp(-dt * 9.5);
      lerpShape(current, target, amount);

      const base = Math.min(w / 941, h / 1672);
      const ox = (w - 941 * base) / 2;
      const oy = (h - 1672 * base) / 2;
      const x = (value: number) => ox + value * base;
      const y = (value: number) => oy + value * base;
      const breath = Math.sin(now * 0.0024);
      const blink = Math.min(
        1,
        0.08 + Math.abs(Math.sin(now * 0.0017 + 0.8)) * 3.1,
      );

      drawBrow(ctx, x(293), y(615), -1, base, current, breath);
      drawBrow(ctx, x(648), y(615), 1, base, current, breath);
      drawEye(ctx, x(300), y(750), -1, base, current, blink, breath);
      drawEye(ctx, x(646), y(750), 1, base, current, blink, breath);
      drawBlush(ctx, x(238), y(882), base, current.blush);
      drawBlush(ctx, x(704), y(882), base, current.blush);
      drawMouth(ctx, x(471), y(904), base, current, now);

      raf = window.requestAnimationFrame(render);
    };

    raf = window.requestAnimationFrame(render);
    return () => window.cancelAnimationFrame(raf);
  }, []);

  return (
    <div className="face-stage" aria-label="animated 60fps assistant face">
      <canvas ref={canvasRef} className="face-canvas" />
    </div>
  );
}
