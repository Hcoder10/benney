# Frontend components

Drop these into a Vite + React app. Two routes by query string:

- `?trip=1` → `TripPlannerLive` (talks to `:7878`)
- `?staff=1` → `StaffBoardLive` (talks to `:7879`)

Move the `.tsx` and `.css` files into your project's `src/sides/` (or wherever
your components live), import them in `App.tsx`, and route on
`new URLSearchParams(window.location.search)`.

Configure API base URLs with env vars:

```
VITE_BENNEY_API=http://127.0.0.1:7878   # trip planner
VITE_STAFF_API=http://127.0.0.1:7879    # staff assistant
```

These are read at build time via `import.meta.env.VITE_*`.

## Minimal `App.tsx` example

```tsx
import TripPlannerLive from "./sides/TripPlannerLive";
import StaffBoardLive from "./sides/StaffBoardLive";

export default function App() {
  const params = new URLSearchParams(window.location.search);
  if (params.has("trip"))  return <TripPlannerLive />;
  if (params.has("staff")) return <StaffBoardLive />;
  return <div>Benney Prism — append ?trip=1 or ?staff=1 to the URL</div>;
}
```
