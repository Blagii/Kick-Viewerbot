import { io, Socket } from "socket.io-client";

export type ConnectionStatus =
  | "disconnected"
  | "connecting"
  | "connected"
  | "error";

export interface BotConfig {
  channelName: string;
  threads: number;
  timeout?: number;
  proxyType?: string;
  proxyFile?: File;
  reconnect?: boolean;
}

export interface BotStats {
  is_running: boolean;
  channel_name?: string;
  video_id?: string | null;
  active_threads: number;
  active_connections: number;
  total_proxies: number;
  request_count: number;
  successful_sessions: number;
  config: {
    threads?: number;
    timeout?: number;
    proxy_type?: string;
    reconnect?: boolean;
  };
  status: {
    state: string;
    message: string;
    proxy_count: number;
    proxy_loading_progress: number;
    startup_progress: number;
  };
  system_metrics: {
    cpu: number;
    memory: number;
    network_up: number;
    network_down: number;
  };
}

interface Callbacks {
  onConnect?: () => void;
  onDisconnect?: () => void;
  onStatsUpdate?: (stats: BotStats) => void;
  onBotStarted?: () => void;
  onBotStopped?: () => void;
  onBotError?: (error: string) => void;
  onStatusChange?: (status: ConnectionStatus) => void;
}

class WebSocketService {
  private socket: Socket | null = null;
  private status: ConnectionStatus = "disconnected";
  private callbacks: Callbacks;

  constructor(callbacks: Callbacks = {}) {
    this.callbacks = callbacks;
  }

  connect(): void {
    if (this.socket?.connected || this.status === "connecting") return;
    this.socket?.disconnect();
    this.updateStatus("connecting");
    // Same-origin polling works both locally and through a hosted preview. Next
    // proxies /socket.io to the Python backend; no browser-side localhost URLs.
    const socket = io({
      path: "/socket.io/",
      transports: ["polling"],
      timeout: 5000,
      reconnection: true,
      reconnectionDelay: 1500,
    });
    this.socket = socket;
    socket.on("connect", () => {
      this.updateStatus("connected");
      this.callbacks.onConnect?.();
    });
    socket.on("connect_error", () => this.updateStatus("error"));
    socket.on("disconnect", () => {
      this.updateStatus("disconnected");
      this.callbacks.onDisconnect?.();
    });
    socket.on("stats_update", (stats: BotStats) =>
      this.callbacks.onStatsUpdate?.(stats),
    );
    socket.on("bot_started", () => this.callbacks.onBotStarted?.());
    socket.on("bot_stopped", () => this.callbacks.onBotStopped?.());
    socket.on("bot_error", (error: { error?: string }) =>
      this.callbacks.onBotError?.(error.error || "Unknown backend error"),
    );
  }

  async startBot(config: BotConfig): Promise<void> {
    if (!this.socket?.connected) throw new Error("Backend is not connected.");
    const data: Record<string, unknown> = {
      channelName: config.channelName,
      threads: config.threads,
      timeout: config.timeout ?? 15000,
      proxyType: config.proxyType ?? "http",
      reconnect: config.reconnect ?? true,
    };
    if (config.proxyFile) {
      if (config.proxyFile.size > 1_000_000)
        throw new Error("Proxy file must be at most 1 MB.");
      data.proxyFileData = await this.fileToBase64(config.proxyFile);
    }
    const result = await this.socket
      .timeout(10000)
      .emitWithAck("start_bot", data);
    if (!result?.success)
      throw new Error(result?.error || "Could not start sessions.");
  }

  async stopBot(): Promise<void> {
    if (!this.socket?.connected) throw new Error("Backend is not connected.");
    const result = await this.socket.timeout(10000).emitWithAck("stop_bot");
    if (!result?.success)
      throw new Error(result?.error || "Could not stop sessions.");
  }

  getStats(): void {
    if (this.socket?.connected) this.socket.emit("get_stats");
  }

  disconnect(): void {
    this.socket?.disconnect();
    this.socket = null;
    this.updateStatus("disconnected");
  }

  private updateStatus(status: ConnectionStatus) {
    this.status = status;
    this.callbacks.onStatusChange?.(status);
  }

  private fileToBase64(file: File): Promise<string> {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve((reader.result as string).split(",")[1]);
      reader.onerror = () => reject(new Error("Could not read proxy file."));
      reader.readAsDataURL(file);
    });
  }
}

export default WebSocketService;
