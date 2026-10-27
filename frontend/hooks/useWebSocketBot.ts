"use client";
import { useEffect, useState, useCallback, useRef } from "react";
import WebSocketService, {
  BotStats,
  BotConfig,
  ConnectionStatus,
} from "@/services/WebSocketService";

export function useWebSocketBot() {
  const [status, setStatus] = useState<ConnectionStatus>("disconnected");
  const [stats, setStats] = useState<BotStats | null>(null);
  const [error, setError] = useState("");
  const wsRef = useRef<WebSocketService | null>(null);

  useEffect(() => {
    const ws = new WebSocketService({
      onConnect: () => {
        setError("");
        ws.getStats();
      },
      onStatsUpdate: setStats,
      onBotStarted: () => ws.getStats(),
      onBotStopped: () => ws.getStats(),
      onBotError: setError,
      onStatusChange: setStatus,
    });
    wsRef.current = ws;
    ws.connect();
    const interval = setInterval(() => ws.getStats(), 2000);
    return () => {
      clearInterval(interval);
      ws.disconnect();
      wsRef.current = null;
    };
  }, []);

  const startBot = useCallback(async (config: BotConfig) => {
    if (!wsRef.current) throw new Error("Backend is not connected.");
    setError("");
    await wsRef.current.startBot(config);
    wsRef.current.getStats();
  }, []);
  const stopBot = useCallback(async () => {
    if (!wsRef.current) throw new Error("Backend is not connected.");
    setError("");
    await wsRef.current.stopBot();
    wsRef.current.getStats();
  }, []);
  const reconnect = useCallback(() => wsRef.current?.connect(), []);

  return {
    isConnected: status === "connected",
    status,
    stats,
    error,
    isRunning: stats?.is_running ?? false,
    startBot,
    stopBot,
    reconnect,
  };
}
