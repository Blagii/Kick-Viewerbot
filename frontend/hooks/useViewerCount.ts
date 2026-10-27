import { useEffect, useState } from "react";

interface ViewerCountResult {
  viewerCount: number | null;
  message: string;
}

export function useViewerCount(target: string) {
  const [result, setResult] = useState<ViewerCountResult>({
    viewerCount: null,
    message: "Enter a YouTube video or live link.",
  });

  useEffect(() => {
    if (!target.trim()) {
      setResult({
        viewerCount: null,
        message: "Enter a YouTube video or live link.",
      });
      return;
    }
    const controller = new AbortController();
    setResult({
      viewerCount: null,
      message: "Checking YouTube viewer count...",
    });
    const fetchCount = async () => {
      try {
        const response = await fetch(
          `/api/youtube/viewers?target=${encodeURIComponent(target.trim())}`,
          { signal: controller.signal, cache: "no-store" },
        );
        const data = await response.json();
        if (!controller.signal.aborted)
          setResult({
            viewerCount:
              typeof data.viewer_count === "number" ? data.viewer_count : null,
            message: data.message || "Viewer count unavailable.",
          });
      } catch {
        if (!controller.signal.aborted)
          setResult({
            viewerCount: null,
            message: "Could not reach the backend for viewer statistics.",
          });
      }
    };
    // Debounce typing and keep quota use modest.
    const initial = setTimeout(fetchCount, 900);
    const interval = setInterval(fetchCount, 60000);
    return () => {
      controller.abort();
      clearTimeout(initial);
      clearInterval(interval);
    };
  }, [target]);

  return result;
}
