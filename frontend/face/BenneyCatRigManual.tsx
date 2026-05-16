import "./benney-cat-rig-manual.css";

export type BenneyManualState =
  | "idle"
  | "wake"
  | "greeting"
  | "listening"
  | "speaking"
  | "thinking"
  | "pondering"
  | "idea"
  | "confused"
  | "reassuring"
  | "amazed"
  | "determined"
  | "searching"
  | "planning"
  | "booking"
  | "confirming"
  | "success"
  | "happy"
  | "celebrating"
  | "curious"
  | "surprised"
  | "delighted"
  | "shy"
  | "apologizing"
  | "concerned"
  | "error"
  | "lowEnergy"
  | "sleeping"
  | "dreaming"
  | "proud"
  | "playful"
  | "excited"
  | "focused"
  | "pullTop"
  | "pullLeft"
  | "pullRight"
  | "stretch"
  | "pounce"
  | "tailSwish"
  | "processing";

export type BenneyManualRigSection =
  | "all"
  | "head"
  | "torso"
  | "leftEar"
  | "rightEar"
  | "leftArm"
  | "rightArm"
  | "leftLeg"
  | "rightLeg"
  | "tail"
  | "face";

type BenneyCatRigManualProps = {
  state?: BenneyManualState;
  debugSection?: BenneyManualRigSection;
  className?: string;
  ariaLabel?: string;
};

const originalMainShape =
  "m67.8 9c0.3-2 0.7-5.1-0.1-7.6-0.1-0.4-0.4-0.5-0.7-0.5-1.9-0.3-5.4 1.9-8 4.1-2.4-0.5-4.9-1-9-1-3 0-5.9 0.3-8.5 0.8-1.8-1.4-5.1-3.8-6.9-3.8-1.7 0-2.7 3.3-2.3 7.9l0.1-0.1c-0.1 0.7-0.2 1.1-0.4 1.4-1.7 3.6-3.2 6.4-3.2 11 0 4.1 1.5 7.6 0.7 12.1-0.7 4-3.4 8-5.1 11.7-1.8 3.8-3.4 8.1-4.5 12.6-0.7 3-1.7 8.3 1.3 9.8 0.7 0.2 1.5 0 2.3-0.7l2.1-0.2c0 4.1 0.6 10.4 2.1 15.1 1.3 3.9 2.9 7.6 4.1 10.3 1.6 3.5 1.1 2.4-0.2 4.8-1 2.4 1.1 3 4 3 1.5 0 4.8-0.3 5.8-1.4 1.6-1.4 1.1-5.9 2.9-10.1 1.8 0.3 3.7 0.4 5.6 0.4 2 0 3.9-0.1 5.7-0.5 2 4.3 1.9 8.3 3.7 10.2 1.1 0.9 4.5 1.4 6.2 1.4 1.8 0 4-0.2 3.1-2.8-1.2-2.4-2.1-2.1-0.6-4.8 2.6-6 6.1-13 6.2-26.6 0.8 0.4 3.9 2.6 5.3 1.4 1.9-1.4 1-6.4 0.1-9.5-1.5-5.9-4.5-12.8-8.2-20.3-2.5-6 0-11 0-16 0-5.5-2.8-9.5-3.5-12l-0.1-0.1z";

export function BenneyCatRigManual({
  state = "idle",
  debugSection,
  className = "",
  ariaLabel = "original Benney cat assistant, rigged from the source SVG",
}: BenneyCatRigManualProps) {
  const classes = className ? `benney-manual-rig ${className}` : "benney-manual-rig";

  return (
    <div
      className={classes}
      data-face-state={state}
      data-rig-debug={debugSection ?? ""}
      role="img"
      aria-label={ariaLabel}
    >
      <span className="manual-stage-aura" aria-hidden="true" />
      <div className="manual-screen-props" aria-hidden="true">
        <span className="manual-pull-screen manual-top-screen">
          <span className="manual-screen-chip">voice panel</span>
          <span className="manual-screen-line wide" />
          <span className="manual-screen-line" />
          <span className="manual-screen-line short" />
          <span className="manual-screen-dots" />
        </span>
        <span className="manual-screen-grip manual-top-grip" />
        <span className="manual-screen-paw manual-top-paw-left" />
        <span className="manual-screen-paw manual-top-paw-right" />

        <span className="manual-pull-screen manual-side-screen manual-side-screen-left">
          <span className="manual-screen-chip">travel</span>
          <span className="manual-side-map" />
          <span className="manual-screen-line wide" />
          <span className="manual-screen-line short" />
        </span>
        <span className="manual-screen-grip manual-left-grip" />
        <span className="manual-screen-paw manual-left-paw-grab" />

        <span className="manual-pull-screen manual-side-screen manual-side-screen-right">
          <span className="manual-screen-chip">plans</span>
          <span className="manual-side-calendar" />
          <span className="manual-screen-line wide" />
          <span className="manual-screen-line short" />
        </span>
        <span className="manual-screen-grip manual-right-grip" />
        <span className="manual-screen-paw manual-right-paw-grab" />

        <span className="manual-scratch-trails">
          <span />
          <span />
          <span />
        </span>
      </div>
      <svg className="manual-rig-svg" viewBox="0 0 100 100" aria-hidden="true" focusable="false">
        <defs>
          <path id="manual-original-main-shape" d={originalMainShape} />

          <filter id="manual-soft-glow" x="-28%" y="-28%" width="156%" height="156%">
            <feGaussianBlur stdDeviation="1.1" result="blur" />
            <feColorMatrix
              in="blur"
              type="matrix"
              values="1 0 0 0 0.95 0 1 0 0 0.62 0 0 1 0 0.35 0 0 0 0.46 0"
              result="warmGlow"
            />
            <feMerge>
              <feMergeNode in="warmGlow" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>

          <clipPath id="manual-clip-left-ear">
            <path d="M29 -1 H45 V13 H29 Z" />
          </clipPath>
          <clipPath id="manual-clip-right-ear">
            <path d="M55 -1 H71 V13 H55 Z" />
          </clipPath>
          <clipPath id="manual-clip-head">
            <path d="M28 7.2 C32.5 4.4 39.4 4 50 4 C60.6 4 67.5 4.4 72 7.2 V36 C66.4 41.3 58.6 43.8 50 43.8 C41.4 43.8 33.6 41.3 28 36 Z M40.5 2 H59.5 V12.5 H40.5 Z" />
          </clipPath>
          <clipPath id="manual-clip-left-arm">
            <path d="M19.1 48.6 C23.4 44.1 30.2 47.4 32.4 55.9 C31.1 62.5 28.3 69.5 24.5 72.2 C20.3 74.2 17 69.8 17.8 63.1 C18.4 58.4 17.3 52.2 19.1 48.6 Z" />
          </clipPath>
          <clipPath id="manual-clip-right-arm">
            <path d="M80.9 48.6 C76.6 44.1 69.8 47.4 67.6 55.9 C68.9 62.5 71.7 69.5 75.5 72.2 C79.7 74.2 83 69.8 82.2 63.1 C81.6 58.4 82.7 52.2 80.9 48.6 Z" />
          </clipPath>
          <clipPath id="manual-clip-torso">
            <path d="M25.3 31.4 C31.8 35.7 39.2 38 50 38 C60.8 38 68.2 35.7 74.7 31.4 C79.5 45.1 78.6 66.3 74.3 75.8 C72.1 81.1 70.2 85.3 68.8 89.8 C62.5 88.8 57.8 88.4 55.4 87.8 C53.2 88.4 46.8 88.4 44.6 87.8 C42.2 88.4 37.5 88.8 31.2 89.8 C29.8 85.3 27.9 81.1 25.7 75.8 C21.4 66.3 20.5 45.1 25.3 31.4 Z" />
          </clipPath>
          <clipPath id="manual-clip-left-leg">
            <path d="M25.1 75.3 C31.4 78.9 38.5 79.5 44.5 77.2 C43.4 85.5 42 93.9 37.5 97.1 C33.1 100 27.5 98.8 27.4 95.6 C30.1 91.2 31.2 83.8 25.1 75.3 Z" />
          </clipPath>
          <clipPath id="manual-clip-right-leg">
            <path d="M55.5 77.2 C61.5 79.5 68.6 78.9 74.9 75.3 C68.8 83.8 69.9 91.2 72.6 95.6 C72.5 98.8 66.9 100 62.5 97.1 C58 93.9 56.6 85.5 55.5 77.2 Z" />
          </clipPath>
        </defs>

        <ellipse className="manual-floor" cx="50" cy="95" rx="35" ry="5" />

        <g className="manual-rig-root" filter="url(#manual-soft-glow)">
          <g className="manual-section manual-tail" data-section="tail">
            <path className="manual-fill-tail" d="m25.9 72.8c-4.9 1.3-5.6 7.6-1.7 9.9 1.2 0.8 2.5 1.1 4.3 1l0.8-0.8-2.2-9.9-1.2-0.2z" />
          </g>

          <g className="manual-section manual-left-leg" data-section="leftLeg">
            <use className="manual-fill-main" href="#manual-original-main-shape" clipPath="url(#manual-clip-left-leg)" />
          </g>

          <g className="manual-section manual-right-leg" data-section="rightLeg">
            <use className="manual-fill-main" href="#manual-original-main-shape" clipPath="url(#manual-clip-right-leg)" />
          </g>

          <g className="manual-section manual-left-arm" data-section="leftArm">
            <use className="manual-fill-main" href="#manual-original-main-shape" clipPath="url(#manual-clip-left-arm)" />
            <path className="manual-left-arm-shadow" d="m23.4 66.6c-0.5-0.2-0.4-0.6 0-0.8 1-0.7 2.1-2.2 2.5-4.2 1.1-5.2 4.7-12.5 5.5-13.1 0.2-0.1 0.5 0.1 0.2 0.9-1 2.7-3 8.1-4 12.7-0.5 1.5-1.6 3.5-2.2 4.3-1 0.1-1.5 0.2-2 0.2z" />
          </g>

          <g className="manual-section manual-right-arm" data-section="rightArm">
            <use className="manual-fill-main" href="#manual-original-main-shape" clipPath="url(#manual-clip-right-arm)" />
            <path className="manual-right-arm-shadow" d="m68.3 48.6c0.2-0.2 0.3-0.5 0.8 0.2 1.5 2.8 3.8 7.2 5.2 13.2 0.7 3.4 1.8 4.4 3.1 4.9 0.2 0 0.6-0.5-0.5-1.3-0.8-0.6-1.8-2-2.4-4.1-1.5-6.5-3.9-10.6-5.4-13.1" />
          </g>

          <g className="manual-section manual-torso" data-section="torso">
            <use className="manual-fill-main" href="#manual-original-main-shape" clipPath="url(#manual-clip-torso)" />
            <path className="manual-belly-line" d="m38 84.6c0.4-0.6 3.5 1.4 6.5 2s7 0.8 11.1 0c3.3-0.6 6-2.2 6.6-2.1 0.7 0.1-0.6 1.3-1.7 1.7-1.6 0.8-3.9 1.8-9.4 2.3-4 0-7-0.3-9.1-1.1-2.6-1-4.1-2-4.1-2.6l0.1-0.2z" />
          </g>

          <g className="manual-section manual-head" data-section="head">
            <use className="manual-fill-main" href="#manual-original-main-shape" clipPath="url(#manual-clip-head)" />
          </g>

          <g className="manual-section manual-face" data-section="face">
            <path className="manual-left-eye" d="m39.5 15.9c0.9-0.3 1.9 0.6 1.8 1.5-0.2 2-2.8 1.6-2.9 0 0-0.8 0.5-1.3 1.1-1.5z" />
            <path className="manual-right-eye" d="m60.1 15.8c0.8 0 1.7 0.7 1.5 1.7-0.2 1.9-2.7 1.5-2.7-0.4 0.1-0.6 0.5-1.2 1.2-1.3z" />
            <path
              className="manual-mouth"
              d="m49.1 18.4h1.8c0.6 0 1.3 0.6 0.6 1.2l-0.9 0.9v0.7l1.3 1.2c0.7 0.6 0.5 1.1-0.4 0.6l-1.5-1.2-1.5 1.3c-0.6 0.4-1.1 0-0.9-0.5l1.5-1.4 0.1-0.6-0.8-0.8c-0.9-0.8-0.2-1.4 0.7-1.4z"
            />
            <g className="manual-cheek-marks">
              <path d="m57.6 22.9 4.9 0.2-0.1 1.4h-4.4l-0.4-1.6z" />
              <path d="m57.9 26h1.6l-0.1 1.6h-1.5v-1.6z" />
            </g>
          </g>

          <g className="manual-section manual-left-ear" data-section="leftEar">
            <path className="manual-fill-main" d="m41.4 4.4c-1.4-1.1-5-3.7-7-3.5-2 0-2.9 4.1-2.3 8" />
            <path className="manual-inner-left-ear" d="m33.5 7.6c-0.1-1.1-0.4-5.1 0.6-5.4 1-0.4 4.8 1.4 4.9 2.8 0 0.8-2.1 1.4-4 2.9-0.5 0.3-1.4 0.3-1.5-0.3z" />
          </g>

          <g className="manual-section manual-right-ear" data-section="rightEar">
            <path className="manual-fill-main" d="m58.9 4.6c1.5-1.2 5-4 7.5-3.7 0.7 0 1.2-0.2 1.5 0.6 0.6 1.9 0.2 5.9 0 7.4" />
            <path className="manual-inner-right-ear" d="m66.5 2.5c0.4 1.9 0.1 4 0 5.3-0.6 0.3-1-0.2-1.9-0.8-2-1.2-3.7-1.4-3.3-2.2 0.3-0.9 4.3-3.3 5.2-2.3z" />
          </g>

          <g className="manual-fx manual-voice-waves" aria-hidden="true">
            <path d="M31 15.5 C28.6 18.2 28.6 21.2 31 24" />
            <path d="M69 15.5 C71.4 18.2 71.4 21.2 69 24" />
            <path d="M27.4 12.4 C23.3 17.1 23.3 22.3 27.4 27.1" />
            <path d="M72.6 12.4 C76.7 17.1 76.7 22.3 72.6 27.1" />
          </g>

          <g className="manual-fx manual-orbit" aria-hidden="true">
            <circle cx="50" cy="50" r="42" />
            <circle className="manual-orbit-dot one" cx="50" cy="8" r="1.1" />
            <circle className="manual-orbit-dot two" cx="91" cy="50" r="1.1" />
            <circle className="manual-orbit-dot three" cx="50" cy="92" r="1.1" />
          </g>

          <g className="manual-fx manual-sparkles" aria-hidden="true">
            <path d="M23 18 L25 15 L27 18 L25 21 Z" />
            <path d="M77 25 L79 22 L81 25 L79 28 Z" />
            <path d="M18 72 L20 69 L22 72 L20 75 Z" />
          </g>

          <g className="manual-fx manual-sleep-z" aria-hidden="true">
            <text x="70" y="16">Z</text>
            <text x="76" y="10">z</text>
            <text x="82" y="5">z</text>
          </g>

          <g className="manual-fx manual-thought-bubbles" aria-hidden="true">
            <circle cx="60.8" cy="13.8" r="1.2" />
            <circle cx="65.1" cy="9.8" r="1.7" />
            <circle cx="70.5" cy="5.6" r="2.25" />
          </g>

          <g className="manual-fx manual-idea-pop" aria-hidden="true">
            <path d="M50 5.8 C46.7 5.8 44.3 8 44.3 10.9 C44.3 12.7 45.2 14.1 46.6 15.3 C47.3 15.9 47.5 16.7 47.5 17.5 H52.5 C52.5 16.7 52.8 15.9 53.5 15.3 C54.8 14.1 55.7 12.7 55.7 10.9 C55.7 8 53.3 5.8 50 5.8 Z" />
            <path d="M47.9 19 H52.1" />
            <path d="M48.5 21 H51.5" />
            <path d="M50 1.8 V4" />
            <path d="M42.2 5.1 L44.1 6.5" />
            <path d="M57.8 5.1 L55.9 6.5" />
          </g>

          <g className="manual-fx manual-question-mark" aria-hidden="true">
            <text x="67" y="15">?</text>
          </g>

          <g className="manual-fx manual-heart-pop" aria-hidden="true">
            <path d="M70 10.4 C69.1 9.1 67.1 10 67.3 11.8 C67.4 13.2 68.9 14.2 70 15.3 C71.1 14.2 72.6 13.2 72.7 11.8 C72.9 10 70.9 9.1 70 10.4 Z" />
          </g>
        </g>
      </svg>
    </div>
  );
}

export default BenneyCatRigManual;
