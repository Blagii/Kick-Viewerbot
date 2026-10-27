"use client";

import { useEffect, useState } from "react";
import {
  ArrowPathIcon,
  ArrowUpTrayIcon,
  BoltIcon,
  CheckCircleIcon,
  CircleStackIcon,
  ClockIcon,
  CpuChipIcon,
  ExclamationTriangleIcon,
  GlobeAltIcon,
  PlayIcon,
  SignalIcon,
  StopIcon,
  UserGroupIcon,
} from "@heroicons/react/24/outline";
import { useViewerCount } from "@/hooks/useViewerCount";
import { useWebSocketBot } from "@/hooks/useWebSocketBot";

export default function Dashboard() {
  const {
    status,
    isConnected,
    stats,
    error,
    isRunning,
    startBot,
    stopBot,
    reconnect,
  } = useWebSocketBot();
  const [target, setTarget] = useState("");
  const [sessions, setSessions] = useState(1);
  const [timeoutSeconds, setTimeoutSeconds] = useState(15);
  const [proxyType, setProxyType] = useState("all");
  const [proxyFile, setProxyFile] = useState<File | null>(null);
  const [autoReconnect, setAutoReconnect] = useState(true);
  const [pending, setPending] = useState(false);
  const [localError, setLocalError] = useState("");

  // Restore a running configuration when a new tab connects to the backend.
  useEffect(() => {
    if (!stats?.is_running) return;
    if (stats.channel_name) setTarget(stats.channel_name);
    if (stats.config.threads) setSessions(stats.config.threads);
    if (stats.config.timeout) setTimeoutSeconds(stats.config.timeout / 1000);
    if (stats.config.proxy_type) setProxyType(stats.config.proxy_type);
    if (typeof stats.config.reconnect === "boolean")
      setAutoReconnect(stats.config.reconnect);
    // Update only when the run starts, not on every two-second statistics poll.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stats?.is_running]);

  const countTarget = isRunning && stats?.video_id ? stats.video_id : target;
  const { viewerCount, message: viewerMessage } = useViewerCount(countTarget);
  const botStatus = stats?.status;
  const state = botStatus?.state ?? "stopped";
  const stateLabel: Record<string, string> = {
    stopped: "Ready",
    initialized: "Ready",
    starting: "Starting",
    running: "Playing",
    stopping: "Stopping",
    warning: "Needs attention",
    error: "Error",
  };
  const statusMessage =
    botStatus?.message || "Enter a YouTube link to get started.";
  const connectionLabel =
    status === "connected"
      ? "Backend connected"
      : status === "connecting"
        ? "Connecting"
        : "Backend offline";
  const inputDisabled = isRunning || pending;

  async function handleAction() {
    setLocalError("");
    if (!isConnected) return;
    if (!isRunning) {
      if (!target.trim())
        return setLocalError("Enter a YouTube video link or @handle.");
      if (!Number.isInteger(sessions) || sessions < 1 || sessions > 100)
        return setLocalError("Choose between 1 and 100 sessions.");
      if (
        !Number.isInteger(timeoutSeconds) ||
        timeoutSeconds < 1 ||
        timeoutSeconds > 120
      )
        return setLocalError("Timeout must be between 1 and 120 seconds.");
    }
    setPending(true);
    try {
      if (isRunning) await stopBot();
      else
        await startBot({
          channelName: target.trim(),
          threads: sessions,
          timeout: timeoutSeconds * 1000,
          proxyType,
          proxyFile: proxyFile || undefined,
          reconnect: autoReconnect,
        });
    } catch (err) {
      setLocalError(
        err instanceof Error
          ? err.message
          : "Request failed. Please try again.",
      );
    } finally {
      setPending(false);
    }
  }

  const metrics = stats?.system_metrics;
  const warning = localError || error;

  return (
    <div className="yt-app">
      <header className="topbar">
        <div className="topbar-inner">
          <a className="brand" href="/" aria-label="YouTube Viewer Bot home">
            <span className="brand-icon">
              <PlayIcon aria-hidden="true" />
            </span>
            <span className="brand-wordmark">
              <strong>
                YouTube <span>Viewer</span>
              </strong>
              <small>SESSION DASHBOARD</small>
            </span>
          </a>
          <button
            className={`connection-pill ${isConnected ? "online" : "offline"}`}
            onClick={() => !isConnected && reconnect()}
            type="button"
            title={
              isConnected
                ? "Connected to the local backend"
                : "Click to retry the backend connection"
            }
          >
            <span className="connection-dot" />
            {connectionLabel}
            {!isConnected && <ArrowPathIcon aria-hidden="true" />}
          </button>
        </div>
      </header>

      <main className="dashboard">
        <div className="page-heading">
          <div>
            <div className="eyebrow">
              <span /> YOUR STREAM, IN FOCUS
            </div>
            <h1>
              YouTube Live <span>dashboard.</span>
            </h1>
            <p>Configure and monitor your playback sessions in one place.</p>
          </div>
          <div className="platform-badge">
            <span className="platform-dot" /> YOUTUBE LIVE{" "}
            <span className="badge-divider" /> PLAYBACK CONTROL
          </div>
        </div>

        <div className="section-heading">
          <div>
            <span className="section-mark" /> <h2>Overview</h2>
          </div>
          <span className="section-caption">LIVE SESSION METRICS</span>
        </div>
        <section className="metric-grid" aria-label="Session overview">
          <div className="metric-card featured-card">
            <div className="metric-top">
              <span>LIVE VIEWERS</span>
              <span className="metric-icon red">
                <UserGroupIcon />
              </span>
            </div>
            <strong className="metric-value">
              {viewerCount === null ? "—" : viewerCount.toLocaleString()}
            </strong>
            <p className="metric-hint" title={viewerMessage}>
              {viewerMessage}
            </p>
          </div>
          <div className="metric-card">
            <div className="metric-top">
              <span>PLAYING SESSIONS</span>
              <span className="metric-icon blue">
                <SignalIcon />
              </span>
            </div>
            <strong className="metric-value">
              {stats?.active_connections ?? 0}
              <small>
                {" "}
                / {isRunning ? (stats?.config.threads ?? sessions) : sessions}
              </small>
            </strong>
            <p className="metric-hint">Players currently in playback</p>
          </div>
          <div className="metric-card">
            <div className="metric-top">
              <span>PROXIES LOADED</span>
              <span className="metric-icon violet">
                <GlobeAltIcon />
              </span>
            </div>
            <strong className="metric-value">
              {stats?.total_proxies ?? 0}
            </strong>
            <p className="metric-hint">
              {(stats?.total_proxies ?? 0)
                ? "Assigned across sessions"
                : "Direct connection when none provided"}
            </p>
          </div>
          <div className="metric-card">
            <div className="metric-top">
              <span>PLAYBACK ATTEMPTS</span>
              <span className="metric-icon amber">
                <BoltIcon />
              </span>
            </div>
            <strong className="metric-value">
              {stats?.request_count ?? 0}
            </strong>
            <p className="metric-hint">Attempts, not YouTube views</p>
          </div>
        </section>

        <div className="section-heading controls-heading">
          <div>
            <span className="section-mark" /> <h2>Session controls</h2>
          </div>
          <span className="section-caption">CONFIGURE & MONITOR</span>
        </div>
        <div className="workspace-grid">
          <section
            className="panel config-panel"
            aria-labelledby="config-heading"
          >
            <div className="panel-heading">
              <div className="panel-heading-icon">
                <PlayIcon />
              </div>
              <div>
                <h3 id="config-heading">Playback configuration</h3>
                <p>Set up your YouTube session</p>
              </div>
              <span className="panel-index">01</span>
            </div>
            <div className="panel-body">
              <label className="field-label" htmlFor="target">
                YouTube video or channel <span>REQUIRED</span>
              </label>
              <div className="input-with-icon">
                <span className="input-icon">
                  <PlayIcon />
                </span>
                <input
                  id="target"
                  type="text"
                  value={target}
                  onChange={(event) => setTarget(event.target.value)}
                  disabled={inputDisabled}
                  placeholder="https://youtube.com/watch?v=…"
                  autoComplete="off"
                />
              </div>
              <p className="field-hint">
                Use a video link, video ID, or channel @handle.
              </p>

              <div className="field-row">
                <div>
                  <label className="field-label" htmlFor="sessions">
                    Sessions
                  </label>
                  <div className="input-with-icon">
                    <span className="input-icon">
                      <UserGroupIcon />
                    </span>
                    <input
                      id="sessions"
                      type="number"
                      min={1}
                      max={100}
                      step={1}
                      value={sessions || ""}
                      onChange={(event) =>
                        setSessions(Number(event.target.value))
                      }
                      disabled={inputDisabled}
                    />
                  </div>
                  <p className="field-hint">
                    Start small. Browsers use CPU and bandwidth.
                  </p>
                </div>
                <div>
                  <label className="field-label" htmlFor="timeout">
                    Timeout <span>SECONDS</span>
                  </label>
                  <div className="input-with-icon">
                    <span className="input-icon">
                      <ClockIcon />
                    </span>
                    <input
                      id="timeout"
                      type="number"
                      min={1}
                      max={120}
                      step={1}
                      value={timeoutSeconds || ""}
                      onChange={(event) =>
                        setTimeoutSeconds(Number(event.target.value))
                      }
                      disabled={inputDisabled}
                    />
                  </div>
                  <p className="field-hint">
                    Time allowed for a player to load.
                  </p>
                </div>
              </div>

              <div className="field-row proxy-row">
                <div>
                  <label className="field-label" htmlFor="proxy-type">
                    Default proxy type
                  </label>
                  <select
                    id="proxy-type"
                    value={proxyType}
                    onChange={(event) => setProxyType(event.target.value)}
                    disabled={inputDisabled}
                  >
                    <option value="all">Auto / HTTP</option>
                    <option value="http">HTTP</option>
                    <option value="socks4">SOCKS4</option>
                    <option value="socks5">SOCKS5</option>
                  </select>
                  <p className="field-hint">
                    Used when a line has no protocol.
                  </p>
                </div>
                <div>
                  <label className="field-label" htmlFor="proxy-file">
                    Proxy list <span>OPTIONAL</span>
                  </label>
                  <label
                    className={`upload-control ${inputDisabled ? "disabled" : ""}`}
                    htmlFor="proxy-file"
                  >
                    <ArrowUpTrayIcon />
                    <span>
                      {proxyFile ? proxyFile.name : "Choose a .txt file"}
                    </span>
                  </label>
                  <input
                    id="proxy-file"
                    className="visually-hidden"
                    type="file"
                    accept=".txt,text/plain"
                    disabled={inputDisabled}
                    onChange={(event) => {
                      setProxyFile(event.target.files?.[0] || null);
                      event.currentTarget.value = "";
                    }}
                  />
                  <p className="field-hint">
                    {proxyFile ? (
                      <button
                        type="button"
                        className="text-button"
                        onClick={() => setProxyFile(null)}
                        disabled={inputDisabled}
                      >
                        Remove file
                      </button>
                    ) : (
                      "One proxy per line; no file = direct."
                    )}
                  </p>
                </div>
              </div>

              <label className="reconnect-setting" htmlFor="reconnect">
                <span className="reconnect-icon">
                  <ArrowPathIcon />
                </span>
                <span>
                  <strong>Automatic reconnection</strong>
                  <small>Retry when a player disconnects.</small>
                </span>
                <input
                  id="reconnect"
                  type="checkbox"
                  checked={autoReconnect}
                  onChange={(event) => setAutoReconnect(event.target.checked)}
                  disabled={inputDisabled}
                />
                <span className="toggle" aria-hidden="true" />
              </label>

              {warning && (
                <div className="inline-error" role="alert">
                  <ExclamationTriangleIcon />
                  {warning}
                </div>
              )}
              <button
                type="button"
                className={`action-button ${isRunning ? "stop-button" : ""}`}
                disabled={!isConnected || pending || state === "stopping"}
                onClick={handleAction}
              >
                {isRunning ? <StopIcon /> : <PlayIcon />}
                {pending
                  ? "Please wait…"
                  : state === "stopping"
                    ? "Stopping sessions…"
                    : isRunning
                      ? "Stop sessions"
                      : "Start sessions"}
                <span>↗</span>
              </button>
              {!isConnected && (
                <p className="connection-help">
                  Start the Python backend to enable session controls.
                </p>
              )}
            </div>
          </section>

          <aside className="side-column">
            <section
              className="panel status-panel"
              aria-labelledby="status-heading"
            >
              <div className="panel-heading">
                <div className="panel-heading-icon neutral">
                  <SignalIcon />
                </div>
                <div>
                  <h3 id="status-heading">Session status</h3>
                  <p>Updates from your backend</p>
                </div>
                <span className="panel-index">02</span>
              </div>
              <div className="status-content" aria-live="polite">
                <div className="status-line">
                  <span className={`status-symbol ${state}`}>
                    <span />
                  </span>
                  <span className="status-title">
                    {stateLabel[state] || state}
                  </span>
                  <span className="status-tag">
                    {isRunning ? "ACTIVE" : "IDLE"}
                  </span>
                </div>
                <p>{statusMessage}</p>
                {isRunning && (
                  <div className="progress-track">
                    <span
                      style={{
                        width: `${Math.max(0, Math.min(100, botStatus?.startup_progress ?? 0))}%`,
                      }}
                    />
                  </div>
                )}
                <div className="status-footer">
                  <span>ACTIVE PLAYERS</span>
                  <strong>
                    {stats?.active_connections ?? 0}{" "}
                    <small>
                      /{" "}
                      {isRunning
                        ? (stats?.config.threads ?? sessions)
                        : sessions}
                    </small>
                  </strong>
                </div>
              </div>
            </section>
            <section
              className="panel resource-panel"
              aria-labelledby="resource-heading"
            >
              <div className="panel-heading">
                <div className="panel-heading-icon neutral">
                  <CpuChipIcon />
                </div>
                <div>
                  <h3 id="resource-heading">System resources</h3>
                  <p>Host machine usage</p>
                </div>
                <span className="panel-index">03</span>
              </div>
              <div className="resource-grid">
                <Resource
                  label="CPU"
                  value={`${(metrics?.cpu ?? 0).toFixed(1)}%`}
                />
                <Resource
                  label="MEMORY"
                  value={`${(metrics?.memory ?? 0).toFixed(1)}%`}
                />
                <Resource
                  label="DOWNLOAD"
                  value={`${(metrics?.network_down ?? 0).toFixed(2)} MB/s`}
                />
                <Resource
                  label="UPLOAD"
                  value={`${(metrics?.network_up ?? 0).toFixed(2)} MB/s`}
                />
              </div>
            </section>
            <div className="side-note">
              <CircleStackIcon />
              <p>
                <strong>About viewer counts</strong>Playback sessions are not
                guaranteed to count as views. YouTube determines and filters its
                own metrics.
              </p>
            </div>
          </aside>
        </div>
        <footer className="footer">
          <span>
            <CheckCircleIcon /> YOUTUBE PLAYBACK DASHBOARD
          </span>
          <span>Use responsibly and follow YouTube&apos;s policies.</span>
        </footer>
      </main>
    </div>
  );
}

function Resource({ label, value }: { label: string; value: string }) {
  return (
    <div className="resource-item">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
