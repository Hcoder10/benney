import { useEffect, useMemo, useState } from "react";
import type { CSSProperties } from "react";
import type { SideId } from "./data";
import { projectionSides } from "./data";
import { ProjectionMappingStage } from "./ProjectionMappingStage";
import { ProjectionSide } from "./ProjectionSide";
import { WallProjectionStage } from "./WallProjectionStage";
import TripPlannerLive from "./sides/TripPlannerLive";
import StaffBoardLive from "./sides/StaffBoardLive";
import LandingPageLive from "./sides/LandingPageLive";
import BenneyHomeLive from "./sides/BenneyHomeLive";
import FamiliesNetworkLive from "./sides/FamiliesNetworkLive";

const sideOrder: SideId[] = ["left", "center", "right"];
const PANEL_WIDTH = 941;
const PANEL_HEIGHT = 1672;

function getBoardScale() {
  const horizontalChrome = 96;
  const verticalChrome = 72;
  const sideGap = 18 * (sideOrder.length - 1);
  const widthScale = (window.innerWidth - horizontalChrome - sideGap) / (PANEL_WIDTH * sideOrder.length);
  const heightScale = (window.innerHeight - verticalChrome) / PANEL_HEIGHT;

  return Math.max(0.22, Math.min(0.62, widthScale, heightScale));
}

function getInitialSide(): SideId | "all" {
  const side = new URLSearchParams(window.location.search).get("side");
  if (side === "left" || side === "center" || side === "right") {
    return side;
  }
  return "all";
}

function getShowDebugControls() {
  const params = new URLSearchParams(window.location.search);
  return params.has("debug") || params.get("controls") === "1";
}

function getInitialReferenceMode() {
  const params = new URLSearchParams(window.location.search);
  return params.has("reference") || params.get("reference") === "1";
}

function getProjectionMappingMode() {
  const params = new URLSearchParams(window.location.search);
  const raw = params.get("mapping") ?? params.get("projection");
  return params.has("mapping") || raw === "1" || raw === "true";
}

export default function App() {
  const params = new URLSearchParams(window.location.search);
  if (params.has("wall") || params.get("screen") === "wall" || params.get("projection") === "wall") {
    return <WallProjectionStage />;
  }

  if (
    params.has("home") ||
    params.has("benney") ||
    params.get("screen") === "benney" ||
    params.get("agent") === "benney"
  ) {
    return <BenneyHomeLive />;
  }

  if (
    params.has("landing") ||
    params.get("screen") === "landing" ||
    params.get("agent") === "landing"
  ) {
    return <LandingPageLive />;
  }

  // Live trip-planner demo: hits the FastAPI cohort server on :7878 and
  // builds the chain slot-by-slot from probability bars.
  if (
    params.has("trip") ||
    params.has("planner") ||
    params.get("screen") === "trip" ||
    params.get("agent") === "trip"
  ) {
    return <TripPlannerLive />;
  }

  if (
    params.has("staff") ||
    params.get("screen") === "staff" ||
    params.get("agent") === "staff"
  ) {
    return <StaffBoardLive />;
  }

  if (
    params.has("families") ||
    params.get("screen") === "families" ||
    params.get("agent") === "families"
  ) {
    return <FamiliesNetworkLive />;
  }

  if (getProjectionMappingMode()) {
    return <ProjectionMappingStage />;
  }

  const [view, setView] = useState<SideId | "all">(getInitialSide);
  const [showReference, setShowReference] = useState(getInitialReferenceMode);
  const [boardScale, setBoardScale] = useState(getBoardScale);
  const isSingle = view !== "all";
  const controlView: SideId | "all" = view;
  const showDebugControls = getShowDebugControls();
  const showReferenceOverlay = showDebugControls && showReference;
  const shellStyle = !isSingle
    ? ({ "--board-scale": boardScale.toFixed(4) } as CSSProperties)
    : undefined;

  useEffect(() => {
    const syncBoardScale = () => setBoardScale(getBoardScale());

    syncBoardScale();
    window.addEventListener("resize", syncBoardScale);
    return () => window.removeEventListener("resize", syncBoardScale);
  }, []);

  const renderedSides = useMemo(
    () => (isSingle ? [view] : sideOrder),
    [isSingle, view],
  );

  return (
    <main className={isSingle ? "app-shell single" : "app-shell board"} style={shellStyle}>
      <div className={isSingle ? "single-stage" : "prism-stage"}>
        {renderedSides.map((side) => (
          <ProjectionSide
            key={side}
            side={side}
            showReference={showReferenceOverlay}
          />
        ))}
      </div>

      {showDebugControls && !isSingle && (
        <nav className="control-deck" aria-label="projection view controls">
          <button
            className={view === "all" ? "active" : ""}
            onClick={() => setView("all")}
          >
            All Sides
          </button>
          {sideOrder.map((side) => (
            <button
              key={side}
              className={controlView === side ? "active" : ""}
              onClick={() => setView(side)}
            >
              {projectionSides[side].name}
            </button>
          ))}
          <button
            className={showReference ? "active" : ""}
            onClick={() => setShowReference((value) => !value)}
          >
            Reference
          </button>
        </nav>
      )}
    </main>
  );
}
