// True once React has hydrated in the browser. Buttons drawn by the server are dead until then,
// so a click there is silently lost; callers disable them until this is true.
import { useSyncExternalStore } from "react";

const noop = () => () => {};

export function useHydrated(): boolean {
  return useSyncExternalStore(
    noop,
    () => true,
    () => false,
  );
}
